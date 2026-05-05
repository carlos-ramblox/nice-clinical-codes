"""
SQLite store for HITL state: users, codelists, per-code decisions, audit log.

Kept separate from code_store.py (reference codes, baked into the Docker image)
because HITL state is mutable per-deployment. For production this should be
backed by RDS/Postgres behind a persistent volume — the SQLite file on a
Fargate task is ephemeral and will be lost on task restart.

Demo users are seeded on first init so the login dropdown is never empty.
"""

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from app.config import HITL_DATABASE_URL

logger = logging.getLogger(__name__)

_conn: Optional[sqlite3.Connection] = None


def _db_path() -> str:
    return HITL_DATABASE_URL.replace("sqlite:///", "")


def get_connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = _db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")  # concurrent reads while reviewing
        _init_schema(_conn)
        _seed_demo_users(_conn)
        logger.info("HITL SQLite connected: %s", path)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            role TEXT CHECK(role IN ('reviewer','admin')) NOT NULL DEFAULT 'reviewer',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );

        CREATE TABLE IF NOT EXISTS codelists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','in_review','approved','rejected')),
            query TEXT NOT NULL,
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at TEXT,
            review_notes TEXT,
            signature_hash TEXT,
            parent_id TEXT REFERENCES codelists(id)
        );

        CREATE TABLE IF NOT EXISTS codelist_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codelist_id TEXT NOT NULL REFERENCES codelists(id) ON DELETE CASCADE,
            code TEXT NOT NULL,
            term TEXT,
            vocabulary TEXT,
            ai_decision TEXT,
            ai_confidence REAL,
            ai_rationale TEXT,
            human_decision TEXT,
            override_comment TEXT,
            sources TEXT,
            is_umls_suggestion INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codelist_id TEXT NOT NULL REFERENCES codelists(id),
            event TEXT NOT NULL,
            user_id INTEGER REFERENCES users(id),
            details TEXT,
            timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_codelists_status  ON codelists(status);
        CREATE INDEX IF NOT EXISTS idx_codelists_creator ON codelists(created_by);
        CREATE INDEX IF NOT EXISTS idx_decisions_codelist ON codelist_decisions(codelist_id);
        CREATE INDEX IF NOT EXISTS idx_audit_codelist     ON audit_log(codelist_id);
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp    ON audit_log(timestamp);
        """
    )
    conn.commit()


def _seed_demo_users(conn: sqlite3.Connection) -> None:
    """Insert demo users if table is empty. Real deployments disable this."""
    existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if existing:
        return
    demo = [
        ("carlos@example.com", "Carlos Ramirez", "admin"),
        ("jane.smith@nhs.example", "Dr Jane Smith", "reviewer"),
        ("mark.patel@nhs.example", "Dr Mark Patel", "reviewer"),
    ]
    conn.executemany(
        "INSERT INTO users (email, name, role) VALUES (?, ?, ?)", demo
    )
    conn.commit()
    logger.info("Seeded %d demo users", len(demo))


# --- helpers -----------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


# --- review-queue ordering --------------------------------------------------

def _review_queue_sort_key(d: dict) -> tuple:
    """Uncertainty-sampling order (Settles 2009, *Active Learning Literature
    Survey*): the codes the LLM is least sure about should reach the
    reviewer first, since a human label there has the highest information
    value.

    Sort tuple:
        (0 if ai_decision == 'uncertain' else 1,   # explicit-uncertain to top
         |2 * ai_confidence - 1|,                  # ascending → least sure first
         code,                                     # human-readable tiebreaker
         id)                                       # full determinism on dup codes

    The schema does not enforce ``UNIQUE(codelist_id, code)``, so two rows
    can share a code; ``id`` (the decision PK) guarantees a deterministic
    order in that case. Missing/non-numeric confidence is treated as 0.5
    (maximum uncertainty), which surfaces unknown-confidence rows
    alongside the genuinely uncertain — the safer default for a clinical
    review queue.
    """
    raw_conf = d.get("ai_confidence")
    try:
        conf = float(raw_conf) if raw_conf is not None else 0.5
    except (TypeError, ValueError):
        conf = 0.5
    margin = abs(2.0 * conf - 1.0)
    return (
        0 if d.get("ai_decision") == "uncertain" else 1,
        margin,
        d.get("code") or "",
        d.get("id") or 0,
    )


def sort_review_queue(decisions: list[dict]) -> list[dict]:
    """Return ``decisions`` reordered for HITL review by uncertainty.

    See ``_review_queue_sort_key`` for the ordering definition.
    """
    return sorted(decisions, key=_review_queue_sort_key)


# --- user ops ---------------------------------------------------------------

def list_users() -> list[dict]:
    conn = get_connection()
    return [dict(r) for r in conn.execute("SELECT id, email, name, role FROM users ORDER BY id")]


def get_user(user_id: int) -> dict | None:
    conn = get_connection()
    return _row(conn.execute(
        "SELECT id, email, name, role FROM users WHERE id = ?", (user_id,)
    ).fetchone())


# --- codelist ops -----------------------------------------------------------

def create_codelist(
    name: str,
    query: str,
    created_by: int,
    decisions: list[dict],
    adopted_phenotypes: list[dict] | None = None,
) -> str:
    """Persist a search result as a draft codelist. Returns the new id.

    ``adopted_phenotypes`` is the list of HDR UK phenotypes the user
    adopted as citations during the discovery-sidebar browse (T34b).
    Each adoption is recorded as a separate ``phenotype_adopted``
    event in the audit log so the citation chain is tamper-evident
    in the same way decision-override events are; there is no
    separate adoptions table. ``get_codelist`` surfaces these to
    callers by replaying the relevant audit-log events.
    """
    conn = get_connection()
    cid = uuid.uuid4().hex[:16]
    conn.execute(
        """INSERT INTO codelists (id, name, query, created_by, status)
           VALUES (?, ?, ?, ?, 'draft')""",
        (cid, name, query, created_by),
    )
    _insert_decisions(conn, cid, decisions)
    adoptions = adopted_phenotypes or []
    _append_audit(
        conn, cid, event="created", user_id=created_by,
        details={
            "name": name,
            "query": query,
            "decision_count": len(decisions),
            "adoption_count": len(adoptions),
        },
    )
    for adoption in adoptions:
        _append_audit(
            conn, cid, event="phenotype_adopted", user_id=created_by,
            details={
                "phenotype_id": adoption.get("phenotype_id", ""),
                "name": adoption.get("name", ""),
                "hdruk_url": adoption.get("hdruk_url", ""),
                "first_publication": adoption.get("first_publication", ""),
            },
        )
    conn.commit()
    logger.info(
        "codelist %s created by user %d (%d decisions, %d adoptions)",
        cid, created_by, len(decisions), len(adoptions),
    )
    return cid


def _insert_decisions(conn: sqlite3.Connection, cid: str, decisions: Iterable[dict]) -> None:
    rows = []
    for d in decisions:
        rows.append((
            cid,
            d.get("code", ""),
            d.get("term", ""),
            d.get("vocabulary", ""),
            d.get("decision", "uncertain"),
            float(d.get("confidence") or 0.0),
            d.get("rationale", ""),
            d.get("decision", "uncertain"),  # human_decision starts == ai_decision
            None,
            json.dumps(d.get("sources") or []),
            1 if _is_umls(d.get("sources")) else 0,
        ))
    conn.executemany(
        """INSERT INTO codelist_decisions
           (codelist_id, code, term, vocabulary,
            ai_decision, ai_confidence, ai_rationale,
            human_decision, override_comment, sources, is_umls_suggestion)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def _is_umls(sources: Any) -> bool:
    if not isinstance(sources, list):
        return False
    return any(isinstance(s, str) and s.startswith("UMLS") for s in sources)


def get_codelist(cid: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT c.*, u.name AS created_by_name
           FROM codelists c LEFT JOIN users u ON u.id = c.created_by
           WHERE c.id = ?""",
        (cid,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    if result.get("reviewed_by"):
        reviewer = conn.execute(
            "SELECT name FROM users WHERE id = ?", (result["reviewed_by"],)
        ).fetchone()
        result["reviewed_by_name"] = reviewer["name"] if reviewer else None

    decisions = [dict(r) for r in conn.execute(
        """SELECT id, code, term, vocabulary,
                  ai_decision, ai_confidence, ai_rationale,
                  human_decision, override_comment, sources, is_umls_suggestion
             FROM codelist_decisions WHERE codelist_id = ?""",
        (cid,),
    )]
    for d in decisions:
        try:
            d["sources"] = json.loads(d["sources"]) if d["sources"] else []
        except (TypeError, ValueError):
            d["sources"] = []
    result["decisions"] = sort_review_queue(decisions)

    # Surface adopted phenotypes (T34b) by replaying the relevant audit
    # events. Stored audit-log only -- no separate table -- so every
    # adoption carries the same tamper-evidence guarantees as the
    # decision-override events.
    adoptions: list[dict] = []
    for r in conn.execute(
        """SELECT details FROM audit_log
            WHERE codelist_id = ? AND event = 'phenotype_adopted'
            ORDER BY id""",
        (cid,),
    ):
        raw = r["details"]
        try:
            adoptions.append(json.loads(raw) if raw else {})
        except (TypeError, ValueError):
            continue
    result["adopted_phenotypes"] = adoptions

    return result


def list_codelists(user_id: int | None = None, status: str | None = None) -> list[dict]:
    conn = get_connection()
    sql = (
        """SELECT c.id, c.name, c.version, c.status, c.query,
                  c.created_by, u.name AS created_by_name,
                  c.created_at, c.reviewed_by, c.reviewed_at,
                  (SELECT COUNT(*) FROM codelist_decisions d WHERE d.codelist_id = c.id)
                    AS decision_count
             FROM codelists c LEFT JOIN users u ON u.id = c.created_by
             WHERE 1=1"""
    )
    params: list = []
    if user_id is not None:
        sql += " AND c.created_by = ?"
        params.append(user_id)
    if status:
        sql += " AND c.status = ?"
        params.append(status)
    sql += " ORDER BY c.created_at DESC"
    return [dict(r) for r in conn.execute(sql, params)]


# --- review / approval ------------------------------------------------------

def submit_review(
    cid: str,
    reviewer_id: int,
    decisions: list[dict],
    action: str,
    notes: str | None,
) -> dict:
    """
    Apply reviewer decisions, flip status to approved/rejected, compute
    signature on approve. Each decision dict: {id, human_decision, override_comment}.
    """
    # TODO(T26): wrap this in BEGIN IMMEDIATE / SELECT ... FOR UPDATE.
    # Two reviewers approving the same cid concurrently can both pass
    # the existence check below, both UPDATE codelist_decisions, and
    # write conflicting signature_hash values — last-writer-wins on the
    # codelist row but both leave audit entries. WAL mode permits the
    # concurrent reads but does not serialise this read-modify-write.
    # Deferred: only manifests under multi-reviewer concurrent load,
    # which the demo deployment doesn't have. Revisit when an NHS
    # Trust pilot lands.
    if action not in ("approve", "reject"):
        raise ValueError(f"unknown action: {action}")

    conn = get_connection()
    cursor = conn.execute("SELECT id FROM codelists WHERE id = ?", (cid,))
    if cursor.fetchone() is None:
        raise KeyError(f"codelist not found: {cid}")

    override_events: list[dict] = []
    for d in decisions:
        did = d.get("id")
        human = d.get("human_decision")
        comment = d.get("override_comment") or None
        if did is None or human is None:
            continue

        existing = conn.execute(
            "SELECT code, ai_decision, human_decision FROM codelist_decisions WHERE id = ? AND codelist_id = ?",
            (did, cid),
        ).fetchone()
        if existing is None:
            continue

        conn.execute(
            """UPDATE codelist_decisions
                  SET human_decision = ?, override_comment = ?
                WHERE id = ? AND codelist_id = ?""",
            (human, comment, did, cid),
        )
        if existing["ai_decision"] != human:
            override_events.append({
                "decision_id": did,
                "code": existing["code"],
                "ai_decision": existing["ai_decision"],
                "human_decision": human,
                "reason": comment,
            })

    new_status = "approved" if action == "approve" else "rejected"
    signature = _compute_signature(conn, cid) if action == "approve" else None

    conn.execute(
        """UPDATE codelists
              SET status = ?, reviewed_by = ?, reviewed_at = ?,
                  review_notes = ?, signature_hash = ?
            WHERE id = ?""",
        (new_status, reviewer_id, _now(), notes, signature, cid),
    )

    # log every override, then the terminal event
    for o in override_events:
        _append_audit(conn, cid, event="override", user_id=reviewer_id, details=o)
    _append_audit(
        conn, cid, event=new_status, user_id=reviewer_id,
        details={
            "notes": notes,
            "override_count": len(override_events),
            "signature_hash": signature,
        },
    )
    conn.commit()

    return {
        "status": new_status,
        "override_count": len(override_events),
        "signature_hash": signature,
    }


def _compute_signature(conn: sqlite3.Connection, cid: str) -> str:
    """
    SHA-256 over the final human decisions in deterministic order. Gives us
    a tamper-evident digest of the approved codelist.
    """
    rows = conn.execute(
        """SELECT code, vocabulary, human_decision
             FROM codelist_decisions
            WHERE codelist_id = ?
            ORDER BY code, vocabulary""",
        (cid,),
    ).fetchall()
    payload = "\n".join(
        f"{r['code']}|{r['vocabulary']}|{r['human_decision']}" for r in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- audit log --------------------------------------------------------------

def _append_audit(
    conn: sqlite3.Connection,
    codelist_id: str,
    event: str,
    user_id: int | None,
    details: dict,
) -> None:
    conn.execute(
        """INSERT INTO audit_log (codelist_id, event, user_id, details, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        (codelist_id, event, user_id, json.dumps(details, sort_keys=True), _now()),
    )


def get_audit(cid: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.id, a.event, a.timestamp, a.details,
                  a.user_id, u.name AS user_name
             FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
            WHERE a.codelist_id = ?
            ORDER BY a.id""",
        (cid,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["details"] = json.loads(d["details"]) if d["details"] else {}
        except (TypeError, ValueError):
            d["details"] = {}
        out.append(d)
    return out

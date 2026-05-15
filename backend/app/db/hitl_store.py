"""
SQLite store for HITL state: users, codelists, per-code decisions, audit log.

Kept separate from code_store.py (reference codes, baked into the Docker image)
because HITL state is mutable per-deployment. For production this should be
backed by RDS/Postgres behind a persistent volume — the SQLite file on a
Fargate task is ephemeral and will be lost on task restart.

Demo users are seeded on first init so the login dropdown is never empty.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from app.config import HITL_DATABASE_URL
from app.db.signature import _compute_signature_v1, _compute_signature_v2
from app.services.dmd_classification import (
    VOCABULARY as DMD_VOCAB,
    infer_dmd_level,
)

logger = logging.getLogger(__name__)

_conn: Optional[sqlite3.Connection] = None


class ConflictError(Exception):
    """Raised when ``submit_review`` finds the codelist already in a terminal
    status (``approved`` / ``rejected``) under the BEGIN IMMEDIATE lock,
    or when ``_transition`` finds the codelist's actual current status
    doesn't match the caller's declared ``from_status``.

    Distinct from ``KeyError`` (codelist missing) so the route layer can
    translate it to HTTP 409 Conflict — the second concurrent reviewer
    sees a definitive "already reviewed" rather than a 200 with a
    last-writer-wins signature.

    ``status`` carries the actual status seen under the lock so the
    route can emit a 409 ``detail`` in the same format as its pre-check
    path, without re-reading the row.

    ``reason`` is an optional sub-discriminator for cases where 409
    means more than "wrong status". Currently used by
    ``submit_review_v2`` to flag ``reason="voting_finalised"`` —
    structurally the codelist is still ``in_review`` but this caller
    has already locked their votes and must use ``/consensus`` to
    change a decision. Keeping ``status`` as the literal DB status
    (always one of the five valid values) and routing the
    "voting_finalised" subcase through ``reason`` avoids the synthetic-
    status footgun where ``.status`` could be confused for a real
    ``codelists.status`` row value.
    """

    def __init__(
        self,
        codelist_id: str,
        status: str,
        *,
        reason: str | None = None,
    ) -> None:
        self.codelist_id = codelist_id
        self.status = status
        self.reason = reason
        super().__init__(f"codelist {codelist_id} is already {status}")


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
        _migrate_schema(_conn)
        _seed_demo_users(_conn)
        logger.info("HITL SQLite connected: %s", path)
    return _conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """In-place column additions for older DBs.

    SQLite has no ``ALTER TABLE ... IF NOT EXISTS``, so each migration
    is wrapped in a try/except that swallows the duplicate-column
    OperationalError ("duplicate column name: ..."). The column-default
    keeps existing rows backward-compatible: pre-T29 codelists read
    back as ``include_criteria=[]`` / ``exclude_criteria=[]`` and
    therefore hash byte-identical to the pre-T29 signature payload.
    """
    for ddl in (
        "ALTER TABLE codelists ADD COLUMN include_criteria TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE codelists ADD COLUMN exclude_criteria TEXT NOT NULL DEFAULT '[]'",
        # concept_id pinned at decision time: an approved codelist's
        # OHDSI export must stay stable even if OMOPHub later remaps the
        # same source code. Nullable for codes no retriever resolved.
        "ALTER TABLE codelist_decisions ADD COLUMN concept_id INTEGER",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    # T32: ``private`` column. Schema default is 0 (new codelists are
    # public by default per audit Rank 5), but on a pre-T32 database
    # every already-approved row was reviewed under a no-public-gallery
    # mental model. Back-fill those rows to private=1 so the migration
    # is *not* a one-way visibility door for existing reviewers; new
    # codelists created after the migration still get the audit-led
    # default-public behaviour from the column DEFAULT.
    #
    # The back-fill UPDATE runs only when the ALTER actually succeeded
    # (first run of this migration). On subsequent runs the ALTER
    # raises ``duplicate column`` and the UPDATE is skipped, so a
    # reviewer who later flipped a row back to public is not silently
    # re-hidden.
    try:
        conn.execute(
            "ALTER TABLE codelists ADD COLUMN private INTEGER NOT NULL DEFAULT 0",
        )
        conn.execute("UPDATE codelists SET private = 1")
        logger.info("T32 migration: back-filled private=1 on pre-T32 codelists")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise

    # T30: incremental column adds for the two-reviewer Delphi flow.
    # Each ALTER is idempotent via the same duplicate-column swallow.
    # Defaults (reviewer_ids='[]', signature_version=1) make pre-T30
    # rows read back as legacy single-reviewer codelists; agreement_kappa
    # stays NULL so a UI surface can distinguish "no kappa computed" from
    # "kappa computed and zero".
    for ddl in (
        "ALTER TABLE codelists ADD COLUMN reviewer_ids TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE codelists ADD COLUMN agreement_kappa REAL",
        "ALTER TABLE codelists ADD COLUMN signature_version INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE codelists ADD COLUMN include_descendants INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    conn.commit()

    # T30: rebuild ``codelists`` to add ``adjudication`` to the status
    # CHECK constraint. SQLite has no ALTER TABLE DROP/MODIFY CHECK,
    # so the only path is the table-rebuild dance documented at
    # https://www.sqlite.org/lang_altertable.html#otheralter . Runs
    # only when the existing CHECK doesn't already include the new
    # status — idempotent on subsequent boots.
    _migrate_codelists_for_t30(conn)


def _migrate_codelists_for_t30(conn: sqlite3.Connection) -> None:
    """Rebuild ``codelists`` so its status CHECK accepts ``adjudication``.

    The 12-step SQLite rebuild dance (CREATE _new, INSERT SELECT, DROP,
    RENAME) is the only way to update an existing CHECK constraint in
    SQLite. This function adds the four safety nails confirmed for T30:

    1. ``PRAGMA foreign_keys = OFF`` *outside* the transaction (SQLite
       silently no-ops the PRAGMA inside a transaction). This stops
       child-row FKs (codelist_decisions, audit_log) from tripping
       during the DROP step.
    2. ``PRAGMA foreign_key_check`` *after* COMMIT to surface any orphan
       rows the rebuild left behind — fail loud rather than ship a DB
       with silent integrity holes.
    3. Idempotency by ``sqlite_master.sql`` parse: if the existing CHECK
       already mentions ``adjudication`` we skip. No separate
       schema_migrations table for this single ticket.
    4. Recreate every index that exists today on codelists. SQLite
       drops them when the underlying table is DROPped; missing them
       silently from the rebuilt schema would slow every status / owner
       lookup.

    Plus a row-count safety check before/after — if the row counts
    diverge the rebuild raises rather than committing a half-complete
    migration.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='codelists'"
    ).fetchone()
    if row is None:
        # _init_schema runs first and would have created the table; if
        # it's missing here something is structurally wrong. Fail loud.
        raise sqlite3.OperationalError(
            "T30 migration: codelists table is missing"
        )
    if "'adjudication'" in (row["sql"] or ""):
        # Already migrated (fresh DB or re-boot after migration ran).
        return

    pre_codelists = conn.execute("SELECT COUNT(*) FROM codelists").fetchone()[0]
    pre_decisions = conn.execute("SELECT COUNT(*) FROM codelist_decisions").fetchone()[0]
    pre_audit = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            CREATE TABLE codelists_new (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft','in_review','adjudication','approved','rejected')),
                query TEXT NOT NULL,
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                reviewed_by INTEGER REFERENCES users(id),
                reviewed_at TEXT,
                review_notes TEXT,
                signature_hash TEXT,
                parent_id TEXT REFERENCES codelists(id),
                include_criteria TEXT NOT NULL DEFAULT '[]',
                exclude_criteria TEXT NOT NULL DEFAULT '[]',
                private INTEGER NOT NULL DEFAULT 0,
                reviewer_ids TEXT NOT NULL DEFAULT '[]',
                agreement_kappa REAL,
                signature_version INTEGER NOT NULL DEFAULT 1,
                include_descendants INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Explicit column list on both sides so a future column add to
        # the new schema doesn't silently leave the SELECT one short.
        conn.execute(
            """
            INSERT INTO codelists_new (
                id, name, version, status, query, created_by, created_at,
                reviewed_by, reviewed_at, review_notes, signature_hash, parent_id,
                include_criteria, exclude_criteria, private,
                reviewer_ids, agreement_kappa, signature_version,
                include_descendants
            )
            SELECT
                id, name, version, status, query, created_by, created_at,
                reviewed_by, reviewed_at, review_notes, signature_hash, parent_id,
                include_criteria, exclude_criteria, private,
                reviewer_ids, agreement_kappa, signature_version,
                include_descendants
            FROM codelists
            """
        )
        conn.execute("DROP TABLE codelists")
        conn.execute("ALTER TABLE codelists_new RENAME TO codelists")
        # Recreate every index that lives on codelists. The index list
        # here must match _init_schema's CREATE INDEX statements for
        # codelists exactly — if a future ticket adds an index there
        # without adding it here, the rebuild silently drops it.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_codelists_status  ON codelists(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_codelists_creator ON codelists(created_by)")

        # foreign_key_check runs BEFORE COMMIT so any orphan rows the
        # rebuild left behind trigger a rollback rather than commit a
        # corrupted DB. Per the SQLite docs at
        # https://www.sqlite.org/lang_altertable.html#otheralter step 9.
        # PRAGMA foreign_keys=OFF disables enforcement during DML but
        # does NOT suppress foreign_key_check's detection — it's a
        # query against sqlite_master, not constraint enforcement.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"T30 migration: foreign_key_check found {len(violations)} "
                f"violations after codelists rebuild: {[dict(v) for v in violations]}"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        # PRAGMA flip back is best-effort — the next get_connection()
        # boot would re-flip it ON via _init_schema's PRAGMA call, but
        # we should leave the connection in a sane state for the rest
        # of this process too.
        conn.execute("PRAGMA foreign_keys = ON")
        raise

    conn.execute("PRAGMA foreign_keys = ON")

    post_codelists = conn.execute("SELECT COUNT(*) FROM codelists").fetchone()[0]
    post_decisions = conn.execute("SELECT COUNT(*) FROM codelist_decisions").fetchone()[0]
    post_audit = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    if (
        post_codelists != pre_codelists
        or post_decisions != pre_decisions
        or post_audit != pre_audit
    ):
        raise sqlite3.IntegrityError(
            "T30 migration: row counts diverged across rebuild — "
            f"codelists {pre_codelists}->{post_codelists}, "
            f"decisions {pre_decisions}->{post_decisions}, "
            f"audit_log {pre_audit}->{post_audit}"
        )
    logger.info(
        "T30 migration: codelists rebuilt (status CHECK now includes "
        "'adjudication'); %d codelist / %d decision / %d audit rows "
        "preserved",
        post_codelists, post_decisions, post_audit,
    )


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
            -- T30: 'adjudication' status added between 'in_review' and
            -- 'approved' for two-reviewer Delphi runs. Pre-T30 rows
            -- (status in legacy four-tuple) keep validating because the
            -- new tuple is a superset.
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','in_review','adjudication','approved','rejected')),
            query TEXT NOT NULL,
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at TEXT,
            review_notes TEXT,
            signature_hash TEXT,
            parent_id TEXT REFERENCES codelists(id),
            -- T29: study-intent criteria (JSON-encoded list[str], default '[]').
            -- Migrate-friendly defaults so pre-T29 rows read back as empty
            -- and signature_hash stays byte-compatible.
            include_criteria TEXT NOT NULL DEFAULT '[]',
            exclude_criteria TEXT NOT NULL DEFAULT '[]',
            -- T32: owner-flippable opt-out for the public /gallery surface.
            private INTEGER NOT NULL DEFAULT 0,
            -- T30: list of user-id reviewers assigned to the codelist
            -- (JSON-encoded list[int], default '[]'). ≥2 reviewers must
            -- be assigned before the codelist transitions out of 'draft'.
            reviewer_ids TEXT NOT NULL DEFAULT '[]',
            -- T30: Cohen's kappa over the two-reviewer per-code votes,
            -- computed on entry to 'adjudication' and persisted.
            -- NULL for legacy / single-reviewer codelists.
            agreement_kappa REAL,
            -- T30: explicit signature payload version. Branches the
            -- _compute_signature path so v1 (legacy + T29 criteria
            -- conditional append) and v2 (T30 reviewer_ids + kappa
            -- fan-out) can co-exist; legacy approved hashes verify
            -- byte-identical under v1 forever. Default 1 so pre-T30
            -- rows read back as v1 without a back-fill UPDATE.
            signature_version INTEGER NOT NULL DEFAULT 1,
            include_descendants INTEGER NOT NULL DEFAULT 0
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

        -- T30: per-(decision, reviewer) vote. Replaces the single
        -- ``codelist_decisions.human_decision`` field for two-reviewer
        -- Delphi runs; the legacy column is retained on the parent row
        -- as the consensus / casting-vote outcome (single-reviewer rows
        -- continue to populate it directly). UNIQUE(decision_id,
        -- reviewer_id) lets a re-vote use INSERT OR REPLACE without
        -- silent duplicates; ON DELETE CASCADE keeps the orphan-vote
        -- shape consistent with codelist_decisions ↔ codelists.
        CREATE TABLE IF NOT EXISTS decision_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL REFERENCES codelist_decisions(id) ON DELETE CASCADE,
            reviewer_id INTEGER NOT NULL REFERENCES users(id),
            vote TEXT CHECK(vote IN ('include','exclude','uncertain')) NOT NULL,
            comment TEXT,
            voted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            UNIQUE(decision_id, reviewer_id)
        );

        CREATE INDEX IF NOT EXISTS idx_codelists_status  ON codelists(status);
        CREATE INDEX IF NOT EXISTS idx_codelists_creator ON codelists(created_by);
        CREATE INDEX IF NOT EXISTS idx_decisions_codelist ON codelist_decisions(codelist_id);
        CREATE INDEX IF NOT EXISTS idx_audit_codelist     ON audit_log(codelist_id);
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp    ON audit_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_decision_votes_decision ON decision_votes(decision_id);
        CREATE INDEX IF NOT EXISTS idx_decision_votes_reviewer ON decision_votes(reviewer_id);
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
    include_criteria: list[str] | None = None,
    exclude_criteria: list[str] | None = None,
    include_descendants: bool = False,
) -> str:
    """Persist a search result as a draft codelist. Returns the new id.

    ``adopted_phenotypes`` is the list of HDR UK phenotypes the user
    adopted as citations during the discovery-sidebar browse (T34b).
    Each adoption is recorded as a separate ``phenotype_adopted``
    event in the audit log so the citation chain is tamper-evident
    in the same way decision-override events are; there is no
    separate adoptions table. ``get_codelist`` surfaces these to
    callers by replaying the relevant audit-log events.

    ``include_criteria`` / ``exclude_criteria`` (T29) carry the
    request-level study-intent scoping. ``None`` defaults to ``[]``,
    which preserves the pre-T29 signature_hash bytes for any caller
    that doesn't supply them.
    """
    conn = get_connection()
    cid = uuid.uuid4().hex[:16]
    inc = list(include_criteria or [])
    exc = list(exclude_criteria or [])
    desc = bool(include_descendants)
    conn.execute(
        """INSERT INTO codelists (id, name, query, created_by, status,
                                  include_criteria, exclude_criteria,
                                  include_descendants)
           VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)""",
        (
            cid, name, query, created_by,
            json.dumps(inc), json.dumps(exc),
            int(desc),
        ),
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
            "include_criteria": inc,
            "exclude_criteria": exc,
            "include_descendants": desc,
        },
    )
    for adoption in adoptions:
        _append_audit(
            conn, cid, event="phenotype_adopted", user_id=created_by,
            details={
                "phenotype_id": adoption.get("phenotype_id", ""),
                "phenotype_version_id": adoption.get("phenotype_version_id"),
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
        code = d.get("code", "")
        vocab = d.get("vocabulary", "")
        # The signature payload uses ``code|vocabulary|decision`` with
        # ``\n`` row separators; embedding ``|`` or ``\n`` in either
        # field creates payload ambiguity that could collide hashes.
        # Well-formed values from upstream retrievers never contain
        # these characters, but a compromised retriever or a future
        # manual-add path might — fail loud at insertion rather than
        # later at signature-verification time.
        for value, field in ((code, "code"), (vocab, "vocabulary")):
            if isinstance(value, str) and ("|" in value or "\n" in value):
                raise ValueError(
                    f"decision {field} contains a separator character "
                    f"(``|`` or newline): {value!r}"
                )
        cid_val = d.get("concept_id")
        rows.append((
            cid,
            code,
            d.get("term", ""),
            vocab,
            d.get("decision", "uncertain"),
            float(d.get("confidence") or 0.0),
            d.get("rationale", ""),
            d.get("decision", "uncertain"),  # human_decision starts == ai_decision
            None,
            json.dumps(d.get("sources") or []),
            1 if _is_umls(d.get("sources")) else 0,
            int(cid_val) if cid_val is not None else None,
        ))
    conn.executemany(
        """INSERT INTO codelist_decisions
           (codelist_id, code, term, vocabulary,
            ai_decision, ai_confidence, ai_rationale,
            human_decision, override_comment, sources, is_umls_suggestion,
            concept_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def _is_umls(sources: Any) -> bool:
    if not isinstance(sources, list):
        return False
    return any(isinstance(s, str) and s.startswith("UMLS") for s in sources)


def get_reviewer_names(reviewer_ids: list[int]) -> dict[str, str]:
    """Resolve user-ids to names, keyed by stringified id for JSON drop-in."""
    if not reviewer_ids:
        return {}
    conn = get_connection()
    placeholders = ",".join("?" * len(reviewer_ids))
    return {
        str(r["id"]): r["name"]
        for r in conn.execute(
            f"SELECT id, name FROM users WHERE id IN ({placeholders})",
            reviewer_ids,
        )
    }


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
                  human_decision, override_comment, sources, is_umls_suggestion,
                  concept_id
             FROM codelist_decisions WHERE codelist_id = ?""",
        (cid,),
    )]
    for d in decisions:
        try:
            d["sources"] = json.loads(d["sources"]) if d["sources"] else []
        except (TypeError, ValueError):
            d["sources"] = []
        d["dmd_level"] = infer_dmd_level(d["term"]) if d.get("vocabulary") == DMD_VOCAB else None
    result["decisions"] = sort_review_queue(decisions)

    # T29: surface stored criteria as plain lists. Pre-T29 rows have the
    # column default '[]' from _migrate_schema, so callers always see a
    # well-formed (possibly empty) list.
    for key in ("include_criteria", "exclude_criteria", "reviewer_ids"):
        raw = result.get(key)
        try:
            result[key] = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            result[key] = []
    result["include_descendants"] = bool(result.get("include_descendants"))

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


# --- T32 public-gallery redaction & list/get -------------------------------
#
# The public surface returns approved codelists without auth. Three things
# leak personal data and must be stripped before the row leaves the API:
#   - reviewer / creator full names  -> reduced to initials
#   - per-decision override comments -> dropped (free-text PII)
#   - UMLS-suggestion rows           -> dropped; they're algorithmic
#     suggestions awaiting reviewer adjudication, not part of the
#     approved set in the spirit of the artefact.
#
# Redaction happens at the DAO layer so every public route shares one
# code path; the auth route returns the raw row unchanged.


_HONORIFICS_AND_SUFFIXES = {
    # honorifics
    "dr", "mr", "mrs", "ms", "miss", "prof", "professor",
    "sir", "dame", "lord", "lady", "rev", "revd", "hon",
    # post-nominal qualifications that are not name parts
    "phd", "md", "mbbs", "frcp", "mrcp", "frcs",
}


def _initials(name: str | None) -> str:
    """'Dr Jane Smith' -> 'JS'. 'Smith, J.' -> 'SJ'. Empty/None -> ''.

    Strips punctuation per token rather than dropping any token that
    happens to end in punctuation -- the older path silently lost the
    surname when the name came in 'Surname, Forename' form (NHS
    directory style). Dots become whitespace (so 'J.K.' splits into
    two initials) and the strip set covers the remaining trailing
    punctuation cases.
    """
    if not name:
        return ""
    tokens = [t.strip(",;:") for t in name.replace(".", " ").split()]
    parts = [t for t in tokens if t and t.lower() not in _HONORIFICS_AND_SUFFIXES]
    return "".join(p[0].upper() for p in parts)


def _redact_summary_row(row: dict) -> dict:
    """Row-level PII redaction: numeric user ids stripped, names reduced
    to initials, parent_id (versioning pointer) dropped.

    Single source of truth for the row-level redaction contract; called
    from both ``list_public_codelists`` (raw SQL list path) and
    ``_redact_codelist`` (detail path on top of ``get_codelist``). When
    a future schema change adds another reviewer-identifying field this
    is the one place to update.
    """
    out = dict(row)
    # Drop raw user_id columns; an unauthenticated visitor has no use for
    # them and they'd allow cross-referencing reviewer activity by id.
    for key in ("created_by", "reviewed_by"):
        out.pop(key, None)
    out["created_by_initials"] = _initials(out.pop("created_by_name", None))
    out["reviewed_by_initials"] = _initials(out.pop("reviewed_by_name", None))
    # parent_id is the versioning chain pointer. If a public codelist
    # were ever forked from a private/draft parent, leaking the parent's
    # id would let a visitor probe the parent's existence by trying it
    # against /api/public/codelists/{id} and reading 404 vs row.
    out.pop("parent_id", None)
    return out


def _redact_codelist(row: dict) -> dict:
    """Return a copy of ``row`` safe to expose to an unauthenticated caller.

    Drops UMLS-suggestion decisions, removes override comments, replaces
    reviewer/creator names with initials, strips numeric user ids and
    review notes. Sets ``redacted=True`` so downstream consumers can't
    mistake a redacted body for a full one.
    """
    out = _redact_summary_row(row)
    out["redacted"] = True
    # Review notes can carry reviewer-identifying free text. Drop wholesale
    # rather than try to scrub.
    out.pop("review_notes", None)

    decisions = out.get("decisions") or []
    redacted_decisions: list[dict] = []
    for d in decisions:
        if d.get("is_umls_suggestion"):
            continue
        rd = dict(d)
        rd.pop("override_comment", None)
        # is_umls_suggestion is always 0 here (UMLS rows dropped above);
        # don't ship a column whose value is structurally fixed.
        rd.pop("is_umls_suggestion", None)
        # ai_rationale is model output, not PII -- keep it; it's the
        # main signal a public visitor will read.
        redacted_decisions.append(rd)
    out["decisions"] = redacted_decisions

    # Stats the gallery list view wants without making the caller paginate
    # the decisions array client-side.
    out["included_count"] = sum(
        1 for d in redacted_decisions if d.get("human_decision") == "include"
    )
    out["decisions_count"] = len(redacted_decisions)
    return out


def count_public_codelists() -> int:
    """Approved & non-private codelists. Cheap; safe to call on every render."""
    conn = get_connection()
    return conn.execute(
        "SELECT COUNT(*) FROM codelists WHERE status = 'approved' AND private = 0"
    ).fetchone()[0]


def list_public_codelists(limit: int = 100, offset: int = 0) -> list[dict]:
    """Gallery list view -- approved & non-private rows, redacted, newest first.

    Ordered by reviewed_at DESC; ties broken by created_at so the order is
    stable when reviewed_at happens to be equal (or NULL on legacy rows
    that somehow slipped through with status=approved).
    """
    conn = get_connection()
    # Two joins so reviewer initials show in the list view as well as the
    # detail view -- otherwise the gallery list shows the author but not
    # the reviewer, which is jarring once a researcher clicks through.
    rows = conn.execute(
        """SELECT c.id, c.name, c.version, c.status, c.query,
                  c.created_by, uc.name AS created_by_name,
                  c.created_at, c.reviewed_by, ur.name AS reviewed_by_name,
                  c.reviewed_at, c.signature_hash,
                  (SELECT COUNT(*) FROM codelist_decisions d
                     WHERE d.codelist_id = c.id
                       AND d.is_umls_suggestion = 0) AS decisions_count,
                  (SELECT COUNT(*) FROM codelist_decisions d
                     WHERE d.codelist_id = c.id
                       AND d.is_umls_suggestion = 0
                       AND d.human_decision = 'include') AS included_count
             FROM codelists c
             LEFT JOIN users uc ON uc.id = c.created_by
             LEFT JOIN users ur ON ur.id = c.reviewed_by
            WHERE c.status = 'approved' AND c.private = 0
            ORDER BY COALESCE(c.reviewed_at, c.created_at) DESC, c.id
            LIMIT ? OFFSET ?""",
        (max(1, min(limit, 500)), max(0, offset)),
    ).fetchall()
    return [_redact_summary_row(dict(r)) for r in rows]


def get_public_codelist(cid: str) -> dict | None:
    """Same data as get_codelist but redacted, and only when approved+!private.

    Returns None if the codelist either does not exist or is not eligible
    for public display -- the route layer turns that into a single 404 so
    a private codelist's id is indistinguishable from a missing one.

    Visibility is checked on the SAME row read by ``get_codelist`` rather
    than via a separate prior SELECT; an owner flipping ``private`` between
    two queries would otherwise let a redacted-but-now-private body slip
    through the gap.

    TODO(T26): ``get_codelist`` itself fires four separate SELECTs without
    an explicit transaction, so a private flip mid-read could still affect
    the decision/audit fan-out reads. Wrap in BEGIN/COMMIT alongside the
    submit_review serialisation deferred for the same ticket.
    """
    full = get_codelist(cid)
    if full is None:
        return None
    if full.get("status") != "approved" or full.get("private"):
        return None
    return _redact_codelist(full)


def set_codelist_privacy(cid: str, private: bool, user_id: int) -> dict:
    """Owner-flippable opt-out from the public gallery.

    Returns ``{"id", "private", "status"}`` on success -- ``private`` is
    the raw 0/1 int to match the wire shape of ``list_codelists`` and
    ``get_codelist``, so a frontend that patches a list-row from the
    mutation response doesn't have to reconcile two formats.

    Raises ``KeyError`` if the codelist does not exist; ``PermissionError``
    if the caller is not the creator. The mutation is appended to the
    audit log so a flip back-and-forth is visible.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT created_by, status, private FROM codelists WHERE id = ?",
        (cid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"codelist not found: {cid}")
    if row["created_by"] != user_id:
        raise PermissionError("only the codelist creator can change privacy")
    new_val = 1 if private else 0
    if row["private"] != new_val:
        conn.execute(
            "UPDATE codelists SET private = ? WHERE id = ?", (new_val, cid),
        )
        # Audit details keep the bool form: it's persisted JSON the
        # frontend reads as a human-readable history, not a row patched
        # back into the codelist list.
        _append_audit(
            conn, cid,
            event="privacy_changed",
            user_id=user_id,
            details={"private": bool(new_val)},
        )
        conn.commit()
    return {"id": cid, "private": new_val, "status": row["status"]}


def list_codelists(user_id: int | None = None, status: str | None = None) -> list[dict]:
    conn = get_connection()
    sql = (
        """SELECT c.id, c.name, c.version, c.status, c.query,
                  c.created_by, u.name AS created_by_name,
                  c.created_at, c.reviewed_by, c.reviewed_at,
                  c.private,
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

    The body runs inside a ``BEGIN IMMEDIATE`` transaction so two reviewers
    approving the same ``cid`` concurrently are serialised at the database
    layer rather than racing on overlapping read-modify-write state. The
    second caller blocks on the lock, then re-reads the codelist's status
    under the lock; if it has already been approved/rejected we raise
    ``ConflictError`` so the route layer can return 409. The route's own
    pre-check at ``api/codelists.py:review_codelist`` is now defence-in-
    depth — the lock is the authoritative gate.

    Concurrency assumption: ``submit_review`` is called only from the
    ``POST /api/codelists/{id}/review`` route handler. SQLite per-thread
    connections (T07) + ``BEGIN IMMEDIATE`` (T26) make per-codelist
    serialisation correct under that assumption. If a future ticket adds
    a background reconciler, batch importer, or any non-route caller, the
    defensive ``conn.commit()`` at the top of this function (which assumes
    no other thread holds an implicit transaction on the singleton) needs
    revisiting before that caller lands.
    """
    if action not in ("approve", "reject"):
        raise ValueError(f"unknown action: {action}")

    conn = get_connection()
    # Flush any pending implicit transaction defensively. Python's sqlite3
    # opens an implicit transaction on first DML statement; if a previous
    # caller left one dangling (e.g. crashed between INSERT and commit),
    # ``BEGIN IMMEDIATE`` would error with "cannot start a transaction
    # within a transaction". commit() is a no-op when nothing is pending.
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing_cl = conn.execute(
            "SELECT id, status, signature_version FROM codelists WHERE id = ?",
            (cid,),
        ).fetchone()
        if existing_cl is None:
            raise KeyError(f"codelist not found: {cid}")
        if existing_cl["status"] not in ("draft", "in_review"):
            raise ConflictError(cid, existing_cl["status"])
        # ``submit_review`` handles only the legacy single-reviewer
        # (v1) flow. v2 codelists must use the per-reviewer flow:
        # POST /reviewers (assign) → POST /review per reviewer
        # (with is_final) → POST /consensus (both reviewers ACK).
        # Routing a v2 codelist through this bulk-update path would
        # silently overwrite ``human_decision`` on every row outside
        # the Delphi state machine. Step 5 wires the v2 routes;
        # this guard prevents accidental misuse before then.
        if existing_cl["signature_version"] == 2:
            raise ValueError(
                f"codelist {cid} uses the two-reviewer Delphi flow "
                "(signature_version=2); submit_review is the legacy "
                "single-reviewer path"
            )

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
    except Exception:
        conn.rollback()
        raise

    return {
        "status": new_status,
        "override_count": len(override_events),
        "signature_hash": signature,
    }


# --- T30 v2 review path ---------------------------------------------------
#
# Public functions that orchestrate the two-reviewer Delphi flow. Each
# owns its own ``BEGIN IMMEDIATE`` transaction and composes the
# ``_locked_*`` helpers from ``app.db.state_machine`` plus the
# signature dispatcher in this module.


def assign_reviewers(
    cid: str,
    reviewer_ids: list[int],
    caller_user_id: int,
) -> dict:
    """Assign exactly two reviewers to a draft codelist, transitioning
    it to ``in_review`` and locking ``signature_version=2`` (the v2
    Delphi flow).

    Guards (all 400 from the route, except status which is 409):
    - ``len(reviewer_ids) == 2`` — Cohen's kappa is n=2 only; T30
      step 3 didn't ship Fleiss. Reject 1 (legacy single-reviewer
      goes via ``submit_review``) or 3+ (n=3 is explicit-optional
      in the T30 ticket, deferred until a Fleiss helper lands).
    - Caller (the codelist creator) is NOT in ``reviewer_ids``.
      Watson 2017 Stage 3 requires reviewer independence.
    - All ``reviewer_ids`` exist in ``users``.
    - Codelist status is ``draft``. Re-assignment after
      ``in_review`` would invalidate every per-reviewer vote
      already cast; if the workflow needs to change reviewers
      mid-process, the owner forks the codelist.

    Idempotent on draft: re-posting the same reviewer-id set is a
    no-op (returns the current state without an audit row).
    Re-posting a *different* set on a draft replaces the previous
    assignment and logs both old and new ids in the
    ``reviewers_assigned`` audit event so the trail is
    reconstructible.

    Sets ``signature_version=2`` on the codelist row at first
    successful assignment. ``signature_version`` is immutable
    afterwards (the v1/v2 dispatch in ``_compute_signature``
    relies on this); a route that wants to demote a v2 codelist
    back to v1 is not part of the workflow.
    """
    from app.db.state_machine import _locked_transition

    # Quick payload-shape checks that don't need the row.
    if len(reviewer_ids) != 2:
        raise ValueError(
            f"reviewer_ids must contain exactly 2 ids "
            f"(got {len(reviewer_ids)}); n=3 is reserved for a future "
            "Fleiss-kappa path"
        )
    if len(set(reviewer_ids)) != 2:
        raise ValueError("reviewer_ids must be distinct")

    conn = get_connection()
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status, created_by, reviewer_ids, signature_version "
            "FROM codelists WHERE id = ?",
            (cid,),
        ).fetchone()
        if row is None:
            raise KeyError(f"codelist not found: {cid}")
        # Authz first: a non-creator gets 403 regardless of payload
        # validity. The self-review check below is creator-specific
        # (the creator is the one trying to be a reviewer of their
        # own codelist), so it only makes sense after we've confirmed
        # the caller IS the creator.
        if row["created_by"] != caller_user_id:
            raise PermissionError(
                "only the codelist creator can assign reviewers"
            )
        if caller_user_id in reviewer_ids:
            raise ValueError(
                "creator cannot self-review (Delphi requires reviewer independence)"
            )
        if row["status"] != "draft":
            raise ConflictError(cid, row["status"])

        # Validate every reviewer_id exists in the users table.
        placeholders = ",".join("?" * len(reviewer_ids))
        existing_ids = {
            r["id"] for r in conn.execute(
                f"SELECT id FROM users WHERE id IN ({placeholders})",
                reviewer_ids,
            )
        }
        missing = [rid for rid in reviewer_ids if rid not in existing_ids]
        if missing:
            raise ValueError(
                f"unknown reviewer_id(s): {missing}"
            )

        previous = sorted(json.loads(row["reviewer_ids"] or "[]"))
        new_ids = sorted(reviewer_ids)
        if previous == new_ids:
            # Idempotent re-post — already assigned to this set. Read
            # signature_version from the row rather than hardcoding 2;
            # the assumption "non-empty reviewer_ids implies sig_v=2"
            # holds today (this function is the only writer of
            # ``reviewer_ids`` and always sets sig_v=2 alongside) but
            # is fragile against future migrations or admin
            # corrections — the row is the source of truth.
            conn.commit()
            return {
                "id": cid,
                "reviewer_ids": new_ids,
                "status": row["status"],
                "signature_version": row["signature_version"],
            }

        conn.execute(
            "UPDATE codelists SET reviewer_ids = ?, signature_version = 2 "
            "WHERE id = ?",
            (json.dumps(new_ids), cid),
        )
        # First-time assignment transitions draft -> in_review. A
        # replacement on an already-in-review codelist would have
        # been rejected above by the status guard, so this path
        # only fires from draft.
        _locked_transition(
            conn, cid, from_status="draft", to_status="in_review",
            reviewer_id=caller_user_id,
        )
        _append_audit(
            conn, cid, event="reviewers_assigned",
            user_id=caller_user_id,
            details={"reviewer_ids": new_ids, "previous": previous},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "id": cid,
        "reviewer_ids": new_ids,
        "status": "in_review",
        "signature_version": 2,
    }


def submit_review_v2(
    cid: str,
    reviewer_id: int,
    votes: list[dict],
    is_final: bool,
) -> dict:
    """v2 per-reviewer voting path. Each ``votes`` entry:
    ``{decision_id: int, vote: 'include'|'exclude'|'uncertain', comment: str|None}``.

    Guards:
    - Status is ``in_review`` (votes mutable).
    - Caller is in ``reviewer_ids``.
    - Every ``decision_id`` belongs to this codelist.
    - Caller has NOT previously logged ``voting_finalised`` (votes
      become locked at finalisation; re-votes go through ``/consensus``).
    - If ``is_final=True``, caller has voted on every decision in
      the codelist (otherwise 400 with the missing decision_ids).

    Behaviour after a successful ``is_final=True`` write that
    leaves both reviewers finalised:
    - Compute Cohen's kappa across the two reviewers' votes.
    - If every decision has unanimous votes: update each
      ``human_decision`` to the agreed value, transition
      ``in_review -> approved``, sign as v2.
    - Otherwise: transition ``in_review -> adjudication``.
      ``human_decision`` stays unchanged; ``/consensus`` will
      resolve.
    """
    from app.db.state_machine import (
        _both_reviewers_finalised,
        _compute_codelist_kappa,
        _locked_mark_voting_finalised,
        _locked_record_vote,
        _locked_transition,
    )

    conn = get_connection()
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status, reviewer_ids FROM codelists WHERE id = ?",
            (cid,),
        ).fetchone()
        if row is None:
            raise KeyError(f"codelist not found: {cid}")
        # Reviewer-membership before status: a non-reviewer gets a
        # clean 403 regardless of where the codelist is in its
        # lifecycle.
        reviewer_ids = json.loads(row["reviewer_ids"] or "[]")
        if reviewer_id not in reviewer_ids:
            raise PermissionError(
                f"user {reviewer_id} is not a reviewer of {cid}"
            )
        if row["status"] != "in_review":
            raise ConflictError(cid, row["status"])

        # Already-finalised check next: a finalised reviewer
        # submitting a payload (well-formed or not) should see the
        # 409 "voting finalised" message, not a 400 about decision
        # ids. is_final is monotonic per reviewer — once true, no
        # more votes accepted; they must use /consensus.
        already_finalised = conn.execute(
            "SELECT 1 FROM audit_log WHERE codelist_id = ? "
            "AND event = 'voting_finalised' AND user_id = ?",
            (cid, reviewer_id),
        ).fetchone()
        if already_finalised is not None:
            raise ConflictError(
                cid, row["status"], reason="voting_finalised",
            )

        # Validate every decision_id belongs to this codelist (catches
        # cross-codelist UI copy-paste bugs).
        codelist_decision_ids = {
            r["id"] for r in conn.execute(
                "SELECT id FROM codelist_decisions WHERE codelist_id = ?",
                (cid,),
            )
        }
        for v in votes:
            if v["decision_id"] not in codelist_decision_ids:
                raise ValueError(
                    f"decision_id {v['decision_id']} does not belong to "
                    f"codelist {cid}"
                )

        # Apply the votes.
        for v in votes:
            _locked_record_vote(
                conn,
                decision_id=v["decision_id"],
                reviewer_id=reviewer_id,
                vote=v["vote"],
                comment=v.get("comment"),
            )

        result: dict[str, Any] = {
            "status": "in_review",
            "is_final": False,
        }

        if is_final:
            # All decisions must have a vote from this reviewer
            # before finalisation is allowed.
            voted_ids = {
                r["decision_id"] for r in conn.execute(
                    "SELECT decision_id FROM decision_votes "
                    "WHERE reviewer_id = ? AND decision_id IN "
                    "(SELECT id FROM codelist_decisions WHERE codelist_id = ?)",
                    (reviewer_id, cid),
                )
            }
            missing = sorted(codelist_decision_ids - voted_ids)
            if missing:
                raise ValueError(
                    f"cannot finalise: missing votes for decisions {missing}"
                )

            _locked_mark_voting_finalised(conn, cid, reviewer_id)
            result["is_final"] = True

            if _both_reviewers_finalised(conn, cid, reviewer_ids):
                kappa = _compute_codelist_kappa(conn, cid)
                conn.execute(
                    "UPDATE codelists SET agreement_kappa = ? WHERE id = ?",
                    (kappa, cid),
                )
                # Pull both reviewers' votes per decision and decide
                # the disposition.
                vote_rows = conn.execute(
                    "SELECT v.decision_id, v.reviewer_id, v.vote "
                    "FROM decision_votes v "
                    "JOIN codelist_decisions d ON d.id = v.decision_id "
                    "WHERE d.codelist_id = ?",
                    (cid,),
                ).fetchall()
                by_decision: dict[int, dict[int, str]] = {}
                for vr in vote_rows:
                    by_decision.setdefault(vr["decision_id"], {})[
                        vr["reviewer_id"]
                    ] = vr["vote"]
                a_id, b_id = sorted(reviewer_ids)
                # Explicit completeness guard before the unanimity sweep:
                # the finalisation check above ensures both reviewers
                # have voted on every decision, but if a corruption /
                # race somehow leaves a decision missing a vote, the
                # naive ``votes_by_rev.get(a_id) != votes_by_rev.get(b_id)``
                # would compare ``None == None`` and silently classify
                # as agreement, then ``votes_by_rev[a_id]`` below would
                # KeyError with a less informative message. Fail loud
                # here with the actual diagnostic instead.
                for did, votes_by_rev in by_decision.items():
                    if a_id not in votes_by_rev or b_id not in votes_by_rev:
                        raise ValueError(
                            f"internal: decision {did} is missing a vote "
                            f"from one reviewer despite both reporting "
                            f"voting_finalised"
                        )
                disagreements = [
                    did for did, votes_by_rev in by_decision.items()
                    if votes_by_rev[a_id] != votes_by_rev[b_id]
                ]
                if not disagreements:
                    # Unanimous — apply each agreed vote to
                    # human_decision and transition straight to
                    # approved (skip adjudication).
                    for did, votes_by_rev in by_decision.items():
                        agreed = votes_by_rev[a_id]
                        conn.execute(
                            "UPDATE codelist_decisions "
                            "SET human_decision = ? WHERE id = ?",
                            (agreed, did),
                        )
                    _locked_transition(
                        conn, cid, "in_review", "approved",
                        reviewer_id=reviewer_id, is_final=True,
                    )
                    signature = _compute_signature(conn, cid)
                    conn.execute(
                        "UPDATE codelists SET signature_hash = ?, "
                        "reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                        (signature, reviewer_id, _now(), cid),
                    )
                    result["status"] = "approved"
                    result["signature_hash"] = signature
                    result["agreement_kappa"] = kappa
                else:
                    _locked_transition(
                        conn, cid, "in_review", "adjudication",
                        reviewer_id=reviewer_id, is_final=True,
                    )
                    result["status"] = "adjudication"
                    result["agreement_kappa"] = kappa
                    result["disagreements"] = sorted(disagreements)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def submit_consensus(
    cid: str,
    reviewer_id: int,
    resolutions: list[dict],
    acknowledge: bool,
) -> dict:
    """Both-ACK consensus mechanism for adjudication-state codelists.

    Each ``resolutions`` entry:
    ``{decision_id: int, final_decision: 'include'|'exclude'|'uncertain',
       rationale: str (non-empty)}``.

    Guards:
    - Status is ``adjudication``.
    - Caller is in ``reviewer_ids``.
    - Every ``decision_id`` belongs to this codelist.
    - Every ``rationale`` is non-empty (the audit anchor for *why*
      we resolved this way; an empty rationale makes the resolution
      opaque to a future reader).
    - Every disputed decision (where the two reviewers' final
      votes differed) is covered by the resolutions array.
      Resolutions can also re-resolve previously-unanimous
      decisions (Watson 2017: discussion may overturn earlier
      agreement).

    Two phases:

    1. ``acknowledge=False`` — proposes a set of resolutions.
       Logs ``proposed_consensus`` with the proposer's id and the
       full resolutions array. Status stays ``adjudication``.
       Counter-proposals are unlimited; each is a fresh event
       superseding the prior proposal.

    2. ``acknowledge=True`` — accepts the *other* reviewer's most
       recent proposal. Resolutions MUST byte-equal that proposal
       (canonical sort by ``decision_id``). The fail-loud reason:
       silently changing a resolution while ACK'ing would forge
       the other reviewer's clinical agreement. On clean ACK:
       update each ``human_decision``, log ``consensus_acknowledged``,
       transition ``adjudication -> approved``, compute v2 signature.
    """
    from app.db.state_machine import _locked_transition

    if not resolutions:
        raise ValueError("resolutions must be non-empty")
    for r in resolutions:
        rationale = (r.get("rationale") or "").strip()
        if not rationale:
            raise ValueError(
                f"resolution for decision {r.get('decision_id')} "
                "requires a non-empty rationale"
            )

    conn = get_connection()
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status, reviewer_ids FROM codelists WHERE id = ?",
            (cid,),
        ).fetchone()
        if row is None:
            raise KeyError(f"codelist not found: {cid}")
        # Reviewer-membership before status — non-reviewers get 403
        # regardless of lifecycle stage.
        reviewer_ids = json.loads(row["reviewer_ids"] or "[]")
        if reviewer_id not in reviewer_ids:
            raise PermissionError(
                f"user {reviewer_id} is not a reviewer of {cid}"
            )
        if row["status"] != "adjudication":
            raise ConflictError(cid, row["status"])

        codelist_decision_ids = {
            r["id"] for r in conn.execute(
                "SELECT id FROM codelist_decisions WHERE codelist_id = ?",
                (cid,),
            )
        }
        for r in resolutions:
            if r["decision_id"] not in codelist_decision_ids:
                raise ValueError(
                    f"decision_id {r['decision_id']} does not belong to "
                    f"codelist {cid}"
                )

        # Compute the disputed set: decisions where the two reviewers'
        # votes differed at finalisation. Every disputed decision must
        # be covered by ``resolutions``.
        vote_rows = conn.execute(
            "SELECT v.decision_id, v.reviewer_id, v.vote "
            "FROM decision_votes v "
            "JOIN codelist_decisions d ON d.id = v.decision_id "
            "WHERE d.codelist_id = ?",
            (cid,),
        ).fetchall()
        by_decision: dict[int, dict[int, str]] = {}
        for vr in vote_rows:
            by_decision.setdefault(vr["decision_id"], {})[
                vr["reviewer_id"]
            ] = vr["vote"]
        a_id, b_id = sorted(reviewer_ids)
        # Explicit completeness guard before computing the disputed
        # set. The codelist is in adjudication status, which means
        # both reviewers had ``voting_finalised`` audit events at
        # that transition. If a vote is now missing despite that
        # (DB corruption / future race), classifying the decision as
        # "not disputed" via ``None == None`` would silently let the
        # consensus path commit a signature on incomplete data — fail
        # loud instead.
        for did, votes_by_rev in by_decision.items():
            if a_id not in votes_by_rev or b_id not in votes_by_rev:
                raise ValueError(
                    f"internal: decision {did} is missing a vote from "
                    f"one reviewer despite codelist being in adjudication"
                )
        disputed = {
            did for did, votes_by_rev in by_decision.items()
            if votes_by_rev[a_id] != votes_by_rev[b_id]
        }
        resolved_ids = {r["decision_id"] for r in resolutions}
        unresolved = sorted(disputed - resolved_ids)
        if unresolved:
            raise ValueError(
                f"unresolved disputed decisions: {unresolved}"
            )

        canonical = _canonical_resolutions(resolutions)

        if not acknowledge:
            _append_audit(
                conn, cid, event="proposed_consensus",
                user_id=reviewer_id,
                details={"resolutions": canonical},
            )
            conn.commit()
            return {"status": "adjudication", "acknowledged": False}

        # acknowledge=True — find the most recent proposal by SOMEONE
        # ELSE and confirm byte-equality.
        prior = conn.execute(
            "SELECT user_id, details FROM audit_log "
            "WHERE codelist_id = ? AND event = 'proposed_consensus' "
            "ORDER BY id DESC LIMIT 1",
            (cid,),
        ).fetchone()
        if prior is None:
            raise ValueError(
                "no consensus proposal exists yet; submit acknowledge=False "
                "first to propose a resolution"
            )
        if prior["user_id"] == reviewer_id:
            raise ValueError(
                "cannot acknowledge your own proposal; the other reviewer "
                "must ACK or counter-propose"
            )
        prior_resolutions = json.loads(prior["details"]).get("resolutions", [])
        if prior_resolutions != canonical:
            raise ValueError(
                "acknowledge=True requires resolutions to byte-equal the "
                "prior proposal; submit acknowledge=False to counter-propose"
            )

        # Apply each resolution.
        for r in resolutions:
            conn.execute(
                "UPDATE codelist_decisions "
                "SET human_decision = ?, override_comment = ? WHERE id = ?",
                (r["final_decision"], r["rationale"].strip(), r["decision_id"]),
            )
        _append_audit(
            conn, cid, event="consensus_acknowledged",
            user_id=reviewer_id,
            details={
                "resolutions": canonical,
                "proposer": prior["user_id"],
                "acknowledger": reviewer_id,
            },
        )
        _locked_transition(
            conn, cid, "adjudication", "approved",
            reviewer_id=reviewer_id, is_final=True,
        )
        signature = _compute_signature(conn, cid)
        conn.execute(
            "UPDATE codelists SET signature_hash = ?, reviewed_by = ?, "
            "reviewed_at = ? WHERE id = ?",
            (signature, reviewer_id, _now(), cid),
        )
        conn.commit()
        return {
            "status": "approved",
            "acknowledged": True,
            "signature_hash": signature,
        }
    except Exception:
        conn.rollback()
        raise


def _canonical_resolutions(resolutions: list[dict]) -> list[dict]:
    """Sort by ``decision_id`` and pick only the byte-comparison
    fields. Used by ``submit_consensus`` to compare an ACK against
    the prior proposal — both serialised through this helper before
    the equality check, so the comparison is order-independent and
    ignores any extraneous fields a future shape might carry.
    """
    return sorted(
        [
            {
                "decision_id": int(r["decision_id"]),
                "final_decision": r["final_decision"],
                "rationale": (r.get("rationale") or "").strip(),
            }
            for r in resolutions
        ],
        key=lambda x: x["decision_id"],
    )


def reject_codelist_v2(
    cid: str,
    reviewer_id: int,
    reason: str,
) -> dict:
    """Unilateral reject from a v2 codelist's review or adjudication
    state. Single-reviewer rejection is sufficient — reject is a
    veto by design (Watson 2017 phrasing: any reviewer can withdraw
    consensus).

    Guards:
    - Status is ``in_review`` or ``adjudication``.
    - Caller is in ``reviewer_ids``.
    - Reason is non-empty.

    Logs ``status_transition`` with reason; no signature on a
    rejected codelist.
    """
    from app.db.state_machine import _locked_transition

    if not reason or not reason.strip():
        raise ValueError("reject requires a non-empty reason")

    conn = get_connection()
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status, reviewer_ids FROM codelists WHERE id = ?",
            (cid,),
        ).fetchone()
        if row is None:
            raise KeyError(f"codelist not found: {cid}")
        # Reviewer-membership before status — non-reviewers get 403
        # regardless of lifecycle stage.
        reviewer_ids = json.loads(row["reviewer_ids"] or "[]")
        if reviewer_id not in reviewer_ids:
            raise PermissionError(
                f"user {reviewer_id} is not a reviewer of {cid}"
            )
        if row["status"] not in ("in_review", "adjudication"):
            raise ConflictError(cid, row["status"])

        _locked_transition(
            conn, cid, row["status"], "rejected",
            reviewer_id=reviewer_id, reason=reason.strip(),
        )
        conn.execute(
            "UPDATE codelists SET reviewed_by = ?, reviewed_at = ?, "
            "review_notes = ? WHERE id = ?",
            (reviewer_id, _now(), reason.strip(), cid),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"status": "rejected", "reason": reason.strip()}


def get_voting_state(cid: str, caller_user_id: int) -> dict:
    """Caller-aware view of a v2 codelist's per-reviewer voting state.

    Returns the data the v2 review UI needs in one payload:

    - ``status`` / ``signature_version`` / ``reviewer_ids`` /
      ``reviewer_names`` — codelist-level facts.
    - ``caller_id`` — for the UI to show "you" affordances.
    - ``is_caller_a_reviewer`` — false for the creator monitoring
      progress.
    - ``caller_finalised`` / ``peer_finalised`` — has each reviewer
      logged ``voting_finalised``? The peer's finalisation state is
      visible immediately (it's a public clinical event); only the
      peer's *votes* are hidden pre-self-finalisation.
    - ``caller_votes`` — current votes by the caller (may be partial
      mid-voting; replaced by the most-recent upsert per
      ``decision_id``).
    - ``peer_votes`` — ``null`` until the caller finalises (Watson
      2017 anchoring-bias prevention); a list once revealed. The
      creator (non-reviewer) sees ``peer_votes`` only after BOTH
      reviewers finalise (consistent with "no peeking until the
      independent stage is over").
    - ``disputed_decision_ids`` — populated when status=adjudication
      (i.e. both finalised AND at least one disagreement).
    - ``agreement_kappa`` — populated when both finalised (mirrors
      ``codelists.agreement_kappa``; the auto-disposition on second
      finalisation persists kappa atomically).
    - ``proposed_consensus`` — most-recent ``proposed_consensus``
      audit event (proposer + resolutions + timestamp) when status
      is adjudication; ``null`` otherwise.

    Auth: caller must be in ``reviewer_ids`` OR be the codelist
    creator. Other authenticated users see ``PermissionError``.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT status, signature_version, reviewer_ids, agreement_kappa, "
        "       created_by FROM codelists WHERE id = ?",
        (cid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"codelist not found: {cid}")
    reviewer_ids: list[int] = sorted(json.loads(row["reviewer_ids"] or "[]"))
    is_caller_a_reviewer = caller_user_id in reviewer_ids
    is_caller_creator = row["created_by"] == caller_user_id
    if not (is_caller_a_reviewer or is_caller_creator):
        raise PermissionError(
            "voting state is visible only to assigned reviewers and "
            "the codelist creator"
        )

    # Reviewer-name lookup (small JOIN; kept inline to avoid an
    # extra round-trip from the route).
    reviewer_names: dict[int, str] = {}
    if reviewer_ids:
        placeholders = ",".join(["?"] * len(reviewer_ids))
        for r in conn.execute(
            f"SELECT id, name FROM users WHERE id IN ({placeholders})",
            reviewer_ids,
        ):
            reviewer_names[r["id"]] = r["name"]

    # Finalisation flags from audit_log (the source of truth — see
    # state_machine._mark_voting_finalised).
    finalised_ids = {
        r["user_id"] for r in conn.execute(
            "SELECT DISTINCT user_id FROM audit_log "
            "WHERE codelist_id = ? AND event = 'voting_finalised'",
            (cid,),
        )
    }
    caller_finalised = caller_user_id in finalised_ids
    peer_id: int | None = None
    if is_caller_a_reviewer:
        peers = [r for r in reviewer_ids if r != caller_user_id]
        peer_id = peers[0] if peers else None
    peer_finalised = peer_id in finalised_ids if peer_id is not None else False

    # Per-reviewer votes. The privacy filter is the load-bearing
    # anchoring-bias guard: a reviewer must not see the other
    # reviewer's votes until they have finalised their own. The
    # creator (non-reviewer) gets the same "after both finalised"
    # treatment so the rule reads consistently.
    all_votes = [dict(r) for r in conn.execute(
        "SELECT v.decision_id, v.reviewer_id, v.vote, v.comment "
        "FROM decision_votes v "
        "JOIN codelist_decisions d ON d.id = v.decision_id "
        "WHERE d.codelist_id = ? "
        "ORDER BY v.decision_id, v.reviewer_id",
        (cid,),
    )]
    caller_votes = [
        {k: v[k] for k in ("decision_id", "vote", "comment")}
        for v in all_votes if v["reviewer_id"] == caller_user_id
    ]
    if is_caller_a_reviewer:
        peer_votes_visible = caller_finalised
    else:
        # Creator sees peer votes only when both reviewers have
        # finalised (i.e. once the independent stage is over).
        peer_votes_visible = (
            len(reviewer_ids) > 0
            and all(rid in finalised_ids for rid in reviewer_ids)
        )
    peer_votes: list[dict] | None
    if peer_votes_visible and peer_id is not None:
        peer_votes = [
            {
                "decision_id": v["decision_id"],
                "reviewer_id": v["reviewer_id"],
                "vote": v["vote"],
                "comment": v["comment"],
            }
            for v in all_votes if v["reviewer_id"] != caller_user_id
        ]
    elif peer_votes_visible and is_caller_creator:
        # Creator viewing both reviewers' votes; both are "peers"
        # from the creator's perspective.
        peer_votes = [
            {
                "decision_id": v["decision_id"],
                "reviewer_id": v["reviewer_id"],
                "vote": v["vote"],
                "comment": v["comment"],
            }
            for v in all_votes
        ]
    else:
        peer_votes = None

    # Disputed decision ids — only meaningful in adjudication. We
    # compute from the votes view we have rather than storing
    # separately; the value is stable while the codelist is in
    # adjudication (votes are locked).
    disputed_decision_ids: list[int] = []
    if row["status"] == "adjudication" and len(reviewer_ids) == 2:
        a_id, b_id = reviewer_ids
        by_decision: dict[int, dict[int, str]] = {}
        for v in all_votes:
            by_decision.setdefault(v["decision_id"], {})[
                v["reviewer_id"]
            ] = v["vote"]
        disputed_decision_ids = sorted(
            did for did, votes_by_rev in by_decision.items()
            if votes_by_rev.get(a_id) != votes_by_rev.get(b_id)
        )

    # Most-recent consensus proposal (only meaningful in adjudication;
    # the route returns ``null`` outside that state to keep the UI
    # contract clean).
    proposed_consensus: dict | None = None
    if row["status"] == "adjudication":
        prior = conn.execute(
            "SELECT user_id, details, timestamp FROM audit_log "
            "WHERE codelist_id = ? AND event = 'proposed_consensus' "
            "ORDER BY id DESC LIMIT 1",
            (cid,),
        ).fetchone()
        if prior is not None:
            try:
                resolutions = json.loads(prior["details"]).get(
                    "resolutions", [],
                )
            except (TypeError, ValueError):
                resolutions = []
            proposer_name = reviewer_names.get(prior["user_id"], "")
            proposed_consensus = {
                "proposer_id": prior["user_id"],
                "proposer_name": proposer_name,
                "resolutions": resolutions,
                "proposed_at": prior["timestamp"],
            }

    return {
        "status": row["status"],
        "signature_version": row["signature_version"],
        "reviewer_ids": reviewer_ids,
        "reviewer_names": reviewer_names,
        "caller_id": caller_user_id,
        "is_caller_a_reviewer": is_caller_a_reviewer,
        "caller_finalised": caller_finalised,
        "peer_id": peer_id,
        "peer_name": reviewer_names.get(peer_id) if peer_id is not None else None,
        "peer_finalised": peer_finalised,
        "caller_votes": caller_votes,
        "peer_votes": peer_votes,
        "disputed_decision_ids": disputed_decision_ids,
        "agreement_kappa": row["agreement_kappa"],
        "proposed_consensus": proposed_consensus,
    }


def _compute_signature(conn: sqlite3.Connection, cid: str) -> str:
    """SHA-256 over the codelist's final state, dispatching on
    ``signature_version``.

    - **v1** (default, legacy): pre-T30 single-reviewer format.
      ``--criteria--`` block appended only when at least one of
      ``include_criteria`` / ``exclude_criteria`` is non-empty
      (T29 conditional-append). Empty-criteria codelists hash
      byte-identical to the pre-T29 formula.
    - **v2** (post-T30): canonical two-reviewer Delphi format.
      Always emits ``--criteria--``, ``--reviewers--``, and
      ``--kappa--`` blocks. The kappa block carries its method
      tag (``cohen-unweighted:0.5234`` or ``cohen-unweighted:null``)
      so a future switch to weighted kappa or Fleiss for n=3
      produces a legitimately different hash, not a silent
      regression.

    ``signature_version`` is **immutable per codelist**: set when
    the codelist commits to either path (v1 at creation with
    empty ``reviewer_ids``, v2 when the first ≥2 reviewers are
    assigned via ``POST /reviewers``) and never mutated afterwards.
    Promoting a v1 codelist to v2 retroactively would invalidate
    every prior verification of its v1 signature, so the
    workflow forks instead — adding reviewers to a legacy codelist
    creates a new v2 codelist with a new id rather than mutating
    the existing row.

    The dispatcher fetches all the inputs both versions might need
    in two queries (one row, N decisions) and hands a plain dict /
    list[dict] to the version-specific helper. This keeps the
    helpers pure — easy to test by passing dicts directly without
    a SQL fixture.
    """
    row = conn.execute(
        "SELECT signature_version, include_criteria, exclude_criteria, "
        "       reviewer_ids, agreement_kappa, include_descendants "
        "FROM codelists WHERE id = ?",
        (cid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"codelist not found: {cid}")
    decisions = [dict(d) for d in conn.execute(
        "SELECT code, vocabulary, human_decision AS final_decision "
        "FROM codelist_decisions WHERE codelist_id = ? "
        "ORDER BY code, vocabulary",
        (cid,),
    )]
    codelist = dict(row)

    # Explicit None-check rather than ``or 1``: a future row with a
    # ``signature_version`` of 0 would slip through truthiness-fallback
    # silently into v1, defeating the unknown-version guard below.
    # ``DEFAULT 1`` makes 0 unreachable today, but the guard's job is
    # to fail loud on unexpected values.
    version = codelist.get("signature_version")
    if version is None:
        version = 1
    if version == 1:
        return _compute_signature_v1(codelist, decisions)
    if version == 2:
        return _compute_signature_v2(codelist, decisions)
    raise ValueError(f"unknown signature_version: {version}")


# --- audit log --------------------------------------------------------------

def _append_audit(
    conn: sqlite3.Connection,
    codelist_id: str,
    event: str,
    user_id: int | None,
    details: dict,
) -> None:
    """INSERT one row into ``audit_log``. Caller must hold the
    surrounding transaction (typically ``BEGIN IMMEDIATE``) — this
    helper is the single audit-write primitive used by every
    ``_locked_*`` helper in ``state_machine.py`` AND by the four
    public mutation functions in this module. Standalone calls
    outside a transaction would commit the audit row to disk
    independently of the work it documents, breaking the
    "audit-log mirrors completed clinical events" contract.
    """
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

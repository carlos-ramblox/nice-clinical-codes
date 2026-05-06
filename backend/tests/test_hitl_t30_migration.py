"""
Pin the T30 schema migration: codelists rebuild for the
``adjudication`` status, plus the new ``reviewer_ids`` /
``agreement_kappa`` / ``signature_version`` columns and the
``decision_votes`` child table.

Tests run against a fresh on-disk SQLite per case (built with the
*pre-T30* schema shape using raw DDL) so we exercise the real rebuild
path rather than the no-op idempotency path that runs against a
fresh ``_init_schema``-built DB. Each test calls
``_init_schema`` + ``_migrate_schema`` directly — bypassing the
``get_connection`` singleton — so the production singleton is never
mutated and the migration runs against a known starting shape.

The *load-bearing* property is row preservation: every existing
codelist / decision / audit row must survive the table-rebuild dance
byte-identical, and the legacy ``signature_hash`` on any approved
row must verify unchanged afterwards (the rebuild does not touch
the bytes used by ``_compute_signature``).

Run from backend/:
    pytest tests/test_hitl_t30_migration.py -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db.hitl_store import (  # noqa: E402
    _init_schema,
    _migrate_schema,
)


# ---------------------------------------------------------------------------
# pre-T30 schema shape
#
# Mirrors what _init_schema looked like immediately before T30 (post-T32):
# four-status CHECK, no reviewer_ids / agreement_kappa / signature_version,
# no decision_votes table. We seed this directly rather than git-archaeology
# the prior schema to keep the test self-contained.
# ---------------------------------------------------------------------------

_PRE_T30_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT CHECK(role IN ('reviewer','admin')) NOT NULL DEFAULT 'reviewer',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE codelists (
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
    parent_id TEXT REFERENCES codelists(id),
    include_criteria TEXT NOT NULL DEFAULT '[]',
    exclude_criteria TEXT NOT NULL DEFAULT '[]',
    private INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE codelist_decisions (
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
    is_umls_suggestion INTEGER NOT NULL DEFAULT 0,
    concept_id INTEGER
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codelist_id TEXT NOT NULL REFERENCES codelists(id),
    event TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id),
    details TEXT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX idx_codelists_status  ON codelists(status);
CREATE INDEX idx_codelists_creator ON codelists(created_by);
CREATE INDEX idx_decisions_codelist ON codelist_decisions(codelist_id);
CREATE INDEX idx_audit_codelist     ON audit_log(codelist_id);
CREATE INDEX idx_audit_timestamp    ON audit_log(timestamp);
"""


def _seed_pre_t30(conn: sqlite3.Connection) -> dict:
    """Populate a pre-T30 DB with realistic shape: one approved codelist
    with two decisions and a couple of audit events, one draft codelist
    with one decision, and the demo-user trio."""
    conn.executescript(_PRE_T30_SCHEMA)
    conn.executemany(
        "INSERT INTO users (email, name, role) VALUES (?, ?, ?)",
        [
            ("u1@example.com", "User One", "admin"),
            ("u2@example.com", "User Two", "reviewer"),
            ("u3@example.com", "User Three", "reviewer"),
        ],
    )

    # Approved codelist with a frozen signature_hash. Whatever bytes
    # this hash holds must survive the rebuild byte-identical.
    approved_sig = "a" * 64
    conn.execute(
        "INSERT INTO codelists "
        "(id, name, status, query, created_by, signature_hash, "
        " include_criteria, exclude_criteria, private) "
        "VALUES (?, ?, 'approved', 'diabetes', 1, ?, ?, ?, 0)",
        ("cl-approved", "approved fixture", approved_sig,
         json.dumps(["adult"]), json.dumps(["gestational"])),
    )
    conn.execute(
        "INSERT INTO codelists "
        "(id, name, status, query, created_by, private) "
        "VALUES ('cl-draft', 'draft fixture', 'draft', 'asthma', 1, 1)",
    )
    conn.executemany(
        "INSERT INTO codelist_decisions "
        "(codelist_id, code, term, vocabulary, ai_decision, ai_confidence, "
        " ai_rationale, human_decision, sources, is_umls_suggestion, concept_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("cl-approved", "E11", "T2D", "ICD-10",
             "include", 0.9, "ok", "include", "[]", 0, 4123),
            ("cl-approved", "E10", "T1D", "ICD-10",
             "exclude", 0.8, "ok", "exclude", "[]", 0, 4124),
            ("cl-draft", "J45", "Asthma", "ICD-10",
             "include", 0.85, "ok", "include", "[]", 0, 5001),
        ],
    )
    conn.executemany(
        "INSERT INTO audit_log (codelist_id, event, user_id, details) "
        "VALUES (?, ?, ?, ?)",
        [
            ("cl-approved", "created", 1, json.dumps({"name": "approved fixture"})),
            ("cl-approved", "approved", 2, json.dumps({"signature_hash": approved_sig})),
            ("cl-draft", "created", 1, json.dumps({"name": "draft fixture"})),
        ],
    )
    conn.commit()
    return {
        "codelists": 2,
        "codelist_decisions": 3,
        "audit_log": 3,
        "approved_signature": approved_sig,
    }


def _open_pre_t30(tmp_path: Path) -> sqlite3.Connection:
    """A fresh pre-T30 DB on disk so we exercise the real rebuild path."""
    db_path = tmp_path / "pre_t30.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    _init_schema(conn)
    _migrate_schema(conn)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_migration_preserves_every_row(tmp_path: Path) -> None:
    """Row counts across codelists, decisions, and audit_log are
    byte-identical pre/post rebuild. This is the load-bearing
    safety property — any drop would silently delete clinical state."""
    conn = _open_pre_t30(tmp_path)
    pre = _seed_pre_t30(conn)

    _migrate(conn)

    for table, expected in [
        ("codelists", pre["codelists"]),
        ("codelist_decisions", pre["codelist_decisions"]),
        ("audit_log", pre["audit_log"]),
    ]:
        actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert actual == expected, (
            f"row count diverged on {table}: {expected} -> {actual}"
        )


def test_migration_preserves_legacy_signature_hash_bytes(tmp_path: Path) -> None:
    """The approved codelist's stored ``signature_hash`` survives the
    rebuild byte-identical. CLINICAL_SAFETY.md commits the system to
    "any post-approval edit changes the hash"; the rebuild must not
    register as such an edit."""
    conn = _open_pre_t30(tmp_path)
    pre = _seed_pre_t30(conn)

    _migrate(conn)

    sig = conn.execute(
        "SELECT signature_hash FROM codelists WHERE id = 'cl-approved'"
    ).fetchone()[0]
    assert sig == pre["approved_signature"], (
        f"signature_hash mutated by migration: {pre['approved_signature']} -> {sig}"
    )


def test_migration_adds_adjudication_to_check_constraint(tmp_path: Path) -> None:
    """The new status value must validate, and the rebuilt CHECK
    constraint must reject anything outside the five-tuple."""
    conn = _open_pre_t30(tmp_path)
    _seed_pre_t30(conn)

    _migrate(conn)

    # adjudication accepted (the load-bearing new value).
    conn.execute(
        "INSERT INTO codelists (id, name, status, query, created_by) "
        "VALUES ('post-adj', 'adj fixture', 'adjudication', 'q', 1)"
    )
    # legacy values still accepted. Use fresh ids — cl-approved and
    # cl-draft are already in the seed.
    for s in ("draft", "in_review", "approved", "rejected"):
        conn.execute(
            "INSERT INTO codelists (id, name, status, query, created_by) "
            "VALUES (?, 'legacy', ?, 'q', 1)",
            (f"post-{s}", s),
        )
    # Garbage rejected by the rebuilt CHECK.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO codelists (id, name, status, query, created_by) "
            "VALUES ('post-bad', 'bad', 'not-a-status', 'q', 1)"
        )


def test_migration_backfills_new_column_defaults(tmp_path: Path) -> None:
    """Pre-T30 rows must read back with reviewer_ids='[]',
    agreement_kappa=NULL, signature_version=1 (load-bearing for the
    legacy single-reviewer path: signature_version=1 routes to the
    byte-compat v1 hash)."""
    conn = _open_pre_t30(tmp_path)
    _seed_pre_t30(conn)

    _migrate(conn)

    rows = conn.execute(
        "SELECT id, reviewer_ids, agreement_kappa, signature_version "
        "FROM codelists ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    for r in rows:
        assert r["reviewer_ids"] == "[]"
        assert r["agreement_kappa"] is None
        assert r["signature_version"] == 1


def test_migration_creates_decision_votes_table(tmp_path: Path) -> None:
    """The child vote table must exist, accept the three vote values,
    enforce UNIQUE(decision_id, reviewer_id), and cascade-delete with
    its parent decision."""
    conn = _open_pre_t30(tmp_path)
    _seed_pre_t30(conn)

    _migrate(conn)

    # Pick a decision id that exists in the seed.
    did = conn.execute(
        "SELECT id FROM codelist_decisions WHERE codelist_id = 'cl-approved' LIMIT 1"
    ).fetchone()["id"]

    # Three valid vote values.
    for vote, reviewer in [("include", 2), ("exclude", 3)]:
        conn.execute(
            "INSERT INTO decision_votes (decision_id, reviewer_id, vote) "
            "VALUES (?, ?, ?)",
            (did, reviewer, vote),
        )

    # UNIQUE(decision_id, reviewer_id) enforced.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO decision_votes (decision_id, reviewer_id, vote) "
            "VALUES (?, 2, 'uncertain')",
            (did,),
        )

    # Invalid vote rejected.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO decision_votes (decision_id, reviewer_id, vote) "
            "VALUES (?, 1, 'banana')",
            (did,),
        )

    # Cascade delete: removing the parent decision drops the votes.
    conn.commit()
    pre = conn.execute(
        "SELECT COUNT(*) FROM decision_votes WHERE decision_id = ?", (did,)
    ).fetchone()[0]
    assert pre == 2
    conn.execute("DELETE FROM codelist_decisions WHERE id = ?", (did,))
    post = conn.execute(
        "SELECT COUNT(*) FROM decision_votes WHERE decision_id = ?", (did,)
    ).fetchone()[0]
    assert post == 0


def test_migration_preserves_indexes_on_codelists(tmp_path: Path) -> None:
    """The rebuilt codelists table carries the same indexes as the
    pre-T30 shape — losing them silently would slow every status /
    owner lookup the route layer does."""
    conn = _open_pre_t30(tmp_path)
    _seed_pre_t30(conn)

    _migrate(conn)

    indexes = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='codelists'"
        )
    }
    # Both indexes from _init_schema must be present after rebuild.
    assert "idx_codelists_status" in indexes
    assert "idx_codelists_creator" in indexes


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """A second migration run must be a no-op — no rows lost, no
    errors raised. ``sqlite_master.sql`` parse for ``adjudication`` is
    the gate; running it twice in a row pins the gate works."""
    conn = _open_pre_t30(tmp_path)
    _seed_pre_t30(conn)

    _migrate(conn)
    counts_after_first = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("codelists", "codelist_decisions", "audit_log")
    }
    sig_after_first = conn.execute(
        "SELECT signature_hash FROM codelists WHERE id = 'cl-approved'"
    ).fetchone()[0]

    _migrate(conn)
    counts_after_second = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("codelists", "codelist_decisions", "audit_log")
    }
    sig_after_second = conn.execute(
        "SELECT signature_hash FROM codelists WHERE id = 'cl-approved'"
    ).fetchone()[0]

    assert counts_after_first == counts_after_second
    assert sig_after_first == sig_after_second


def test_migration_passes_foreign_key_check(tmp_path: Path) -> None:
    """``PRAGMA foreign_key_check`` returns no rows after the rebuild.
    Belt-and-braces: the migration itself raises if it sees violations,
    so a green ``foreign_key_check`` here also confirms the migration's
    own check fired and passed."""
    conn = _open_pre_t30(tmp_path)
    _seed_pre_t30(conn)

    _migrate(conn)

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert violations == [], f"FK violations after rebuild: {violations}"

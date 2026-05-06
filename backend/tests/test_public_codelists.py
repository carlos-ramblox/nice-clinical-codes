"""
T32 -- public gallery of approved codelists.

What these tests pin:
  - GET /api/public/* returns 200 without a session cookie (public).
  - Only ``status='approved' AND private=0`` rows are listed.
  - Reviewer/creator names are reduced to initials; override comments
    and review notes don't appear in the response; UMLS-suggestion rows
    are dropped.
  - ``private=true`` removes a previously-public row from both the list
    and the detail endpoint (404).
  - The privacy mutation is owner-only.
  - Public CSV / OHDSI exports succeed without auth.
  - The ``/count`` endpoint reflects approved+!private rows only --
    the search-page hero link reads from this.

Same SQLite-cleanup pattern as ``test_phenotype_adoption.py`` so we
don't accrete fixture rows in the dev DB.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup_test_codelists():
    from app.db.hitl_store import get_connection
    conn = get_connection()
    pre = {r["id"] for r in conn.execute("SELECT id FROM codelists")}
    yield
    post = {r["id"] for r in conn.execute("SELECT id FROM codelists")}
    new = post - pre
    if not new:
        return
    placeholders = ",".join(["?"] * len(new))
    params = list(new)
    conn.execute(f"DELETE FROM audit_log WHERE codelist_id IN ({placeholders})", params)
    conn.execute(f"DELETE FROM codelist_decisions WHERE codelist_id IN ({placeholders})", params)
    conn.execute(f"DELETE FROM codelists WHERE id IN ({placeholders})", params)
    conn.commit()


# --- helpers --------------------------------------------------------------

def _login_demo_user() -> dict:
    users = client.get("/api/auth/users").json()
    assert users, "demo users not seeded"
    res = client.post("/api/auth/login", json={"user_id": users[0]["id"]})
    assert res.status_code == 200, res.text
    return res.json()


def _seed_search(query: str, codes: list[dict]) -> str:
    from app.api import _search_cache
    sid = uuid.uuid4().hex[:12]
    _search_cache.put(sid, query, codes)
    return sid


# Two non-UMLS codes + one UMLS-suggestion row that the public surface
# should drop. Override comment on E10 must be redacted out.
_CODES_FIXTURE = [
    {"code": "E11", "term": "Type 2 diabetes", "vocabulary": "ICD-10",
     "decision": "include", "confidence": 0.9, "rationale": "primary T2D code",
     "sources": ["OMOPHub"]},
    {"code": "E10", "term": "Type 1 diabetes", "vocabulary": "ICD-10",
     "decision": "include", "confidence": 0.8, "rationale": "T1D",
     "sources": ["OMOPHub"]},
    {"code": "E13", "term": "Other diabetes (UMLS-expanded)", "vocabulary": "ICD-10",
     "decision": "uncertain", "confidence": 0.5, "rationale": "UMLS RN expansion",
     "sources": ["UMLS:RN"]},
]


def _create_and_approve(name: str, override_comment_for_e10: str | None = None) -> dict:
    """Create a draft, optionally override one decision (E10 -> exclude with
    comment), then approve. Returns the approved codelist body."""
    sid = _seed_search("type 2 diabetes", _CODES_FIXTURE)
    cl = client.post("/api/codelists", json={"search_id": sid, "name": name}).json()

    detail = client.get(f"/api/codelists/{cl['id']}").json()
    decisions = []
    for d in detail["decisions"]:
        if d["code"] == "E10" and override_comment_for_e10:
            decisions.append({
                "id": d["id"],
                "human_decision": "exclude",
                "override_comment": override_comment_for_e10,
            })
        else:
            decisions.append({"id": d["id"], "human_decision": d["ai_decision"]})

    res = client.post(
        f"/api/codelists/{cl['id']}/review",
        json={
            "decisions": decisions,
            "action": "approve",
            "notes": "private clinical context that should not leak",
        },
    )
    assert res.status_code == 200, res.text
    return cl


def _logout():
    client.post("/api/auth/logout")
    client.cookies.clear()


# --- tests -----------------------------------------------------------------

def test_list_redacts_and_excludes_drafts_and_private_rows():
    """List view returns approved+!private only, with reviewer name reduced
    to initials and override comments / UMLS rows absent from the body."""
    _login_demo_user()
    approved = _create_and_approve(
        "T32 list-redaction fixture",
        override_comment_for_e10="excluded for the type-2 cohort study",
    )

    # A second, draft codelist must NOT appear in the public list.
    sid_draft = _seed_search("draft only", _CODES_FIXTURE[:1])
    draft = client.post(
        "/api/codelists", json={"search_id": sid_draft, "name": "T32 draft"},
    ).json()

    _logout()

    res = client.get("/api/public/codelists")
    assert res.status_code == 200
    rows = res.json()
    ids = [r["id"] for r in rows]
    assert approved["id"] in ids
    assert draft["id"] not in ids

    row = next(r for r in rows if r["id"] == approved["id"])
    # Initials only -- no full reviewer name or numeric user id.
    assert "created_by_name" not in row
    assert "reviewed_by_name" not in row
    assert "created_by" not in row
    assert "reviewed_by" not in row
    assert row["created_by_initials"]
    # Excludes UMLS-suggestion rows from decisions_count: 2 codes, not 3.
    assert row["decisions_count"] == 2
    assert row["included_count"] == 1


def test_get_redacts_decisions_and_strips_pii():
    """Detail view: redacted=True, no override_comment field on any
    decision row, no review_notes field on the codelist, no UMLS rows.

    Also pins the leak fixes: parent_id is dropped (versioning chain
    pointer would let a visitor probe a private parent), and
    is_umls_suggestion is dropped from decisions (always 0 once UMLS
    rows are filtered, so leaking the column is just schema noise)."""
    _login_demo_user()
    approved = _create_and_approve(
        "T32 detail-redaction fixture",
        override_comment_for_e10="NOT-FOR-PUBLIC reviewer rationale",
    )
    _logout()

    res = client.get(f"/api/public/codelists/{approved['id']}")
    assert res.status_code == 200
    body = res.json()

    assert body["redacted"] is True
    assert "review_notes" not in body
    assert "created_by_name" not in body
    assert "parent_id" not in body
    assert body["created_by_initials"]
    # decisions: 2 (UMLS row dropped), no override_comment leaks, no
    # structurally-fixed is_umls_suggestion column shipping either.
    assert len(body["decisions"]) == 2
    for d in body["decisions"]:
        assert "override_comment" not in d
        assert "is_umls_suggestion" not in d
        # ai_rationale is model output, not PII -- must remain visible.
        assert d.get("ai_rationale")
    # The override comment string must not appear anywhere in the body.
    assert "NOT-FOR-PUBLIC" not in res.text


def test_list_view_surfaces_distinct_creator_and_reviewer_initials():
    """The gallery list shows author and reviewer initials so a visitor
    sees the same redacted attribution before and after clicking through.

    Pins the *distinct-JOIN* property: a future refactor that joins
    ``c.created_by`` twice by accident would otherwise pass a same-user
    fixture silently. Here the codelist is created by user A and approved
    by user B, so the two initial strings must differ."""
    users = client.get("/api/auth/users").json()
    creator = users[0]
    reviewer = next(u for u in users if u["id"] != creator["id"])
    assert _initials_for(creator["name"]) != _initials_for(reviewer["name"]), (
        "test fixture relies on demo users having distinct initials"
    )

    # Create as creator.
    client.post("/api/auth/login", json={"user_id": creator["id"]})
    sid = _seed_search("type 2 diabetes", _CODES_FIXTURE)
    cl = client.post(
        "/api/codelists",
        json={"search_id": sid, "name": "T32 distinct-initials fixture"},
    ).json()

    # Approve as a different reviewer.
    _logout()
    client.post("/api/auth/login", json={"user_id": reviewer["id"]})
    detail = client.get(f"/api/codelists/{cl['id']}").json()
    decisions = [
        {"id": d["id"], "human_decision": d["ai_decision"]}
        for d in detail["decisions"]
    ]
    approve = client.post(
        f"/api/codelists/{cl['id']}/review",
        json={"decisions": decisions, "action": "approve", "notes": None},
    )
    assert approve.status_code == 200, approve.text
    _logout()

    rows = client.get("/api/public/codelists").json()
    row = next(r for r in rows if r["id"] == cl["id"])
    assert row["created_by_initials"] == _initials_for(creator["name"])
    assert row["reviewed_by_initials"] == _initials_for(reviewer["name"])
    assert row["created_by_initials"] != row["reviewed_by_initials"]
    assert "created_by_name" not in row
    assert "reviewed_by_name" not in row


def _initials_for(name: str) -> str:
    """Compute the expected initials for a seeded user by name. Delegates
    to the implementation so the test stays robust against demo-user
    seed changes; the property the test pins is "rows show initials of
    *whichever* creator/reviewer the row references", not a fixed string."""
    from app.db.hitl_store import _initials
    return _initials(name)


def test_private_flag_blocks_public_access():
    """An owner who flips private=true removes the row from the gallery
    list and the detail endpoint returns 404 -- same shape as 'missing'
    so a private row's id can't be probed."""
    user = _login_demo_user()
    approved = _create_and_approve("T32 private fixture")

    # Visible before flip.
    _logout()
    assert any(r["id"] == approved["id"]
               for r in client.get("/api/public/codelists").json())

    # Flip private=true (owner action).
    client.post("/api/auth/login", json={"user_id": user["id"]})
    res = client.put(
        f"/api/codelists/{approved['id']}/privacy", json={"private": True},
    )
    assert res.status_code == 200, res.text
    # PUT returns the raw 0/1 int, matching list/detail shape.
    assert res.json()["private"] == 1

    # Gone from public list and detail.
    _logout()
    assert not any(r["id"] == approved["id"]
                   for r in client.get("/api/public/codelists").json())
    assert client.get(f"/api/public/codelists/{approved['id']}").status_code == 404


def test_draft_premarked_private_stays_hidden_on_approval():
    """A draft owner who pre-marks private=true before approval should
    have the codelist invisible at /gallery the moment approval flips
    status. Pins the gallery's visibility logic against the (status,
    private) combo, not just the post-approval flip path -- the spec
    says privacy is status-agnostic on purpose so a reviewer can opt
    out before the artefact ever auto-publishes."""
    user = _login_demo_user()
    sid = _seed_search("type 2 diabetes", _CODES_FIXTURE)
    cl = client.post(
        "/api/codelists", json={"search_id": sid, "name": "T32 pre-marked draft"},
    ).json()

    # Pre-mark private=true while still draft.
    res = client.put(
        f"/api/codelists/{cl['id']}/privacy", json={"private": True},
    )
    assert res.status_code == 200
    assert res.json()["private"] == 1

    # Approve. The status flips to 'approved' but the row is still
    # private, so it must NOT appear at /gallery.
    detail = client.get(f"/api/codelists/{cl['id']}").json()
    decisions = [
        {"id": d["id"], "human_decision": d["ai_decision"]}
        for d in detail["decisions"]
    ]
    approve = client.post(
        f"/api/codelists/{cl['id']}/review",
        json={"decisions": decisions, "action": "approve", "notes": None},
    )
    assert approve.status_code == 200
    _logout()

    rows = client.get("/api/public/codelists").json()
    assert not any(r["id"] == cl["id"] for r in rows)
    assert client.get(f"/api/public/codelists/{cl['id']}").status_code == 404


def test_privacy_flip_writes_audit_event():
    """CLINICAL_SAFETY.md commits to: 'retraction is itself auditable'.
    Each flip of the private flag must append a privacy_changed event
    to the codelist's audit log carrying the new value, so the
    visibility history is reconstructible."""
    user = _login_demo_user()
    approved = _create_and_approve("T32 privacy-audit fixture")

    # Flip on, flip back. Each transition writes one audit event; a
    # no-op flip (same value) must not duplicate an event.
    client.put(f"/api/codelists/{approved['id']}/privacy", json={"private": True})
    client.put(f"/api/codelists/{approved['id']}/privacy", json={"private": True})
    client.put(f"/api/codelists/{approved['id']}/privacy", json={"private": False})

    audit = client.get(f"/api/codelists/{approved['id']}/audit").json()
    flips = [e for e in audit if e["event"] == "privacy_changed"]
    # Two real transitions (off -> on -> off); the duplicate write is a
    # no-op the store collapses.
    assert len(flips) == 2
    assert flips[0]["details"]["private"] is True
    assert flips[1]["details"]["private"] is False
    # User identity is recorded so the audit trail attributes the flip.
    assert all(e["user_id"] == user["id"] for e in flips)


def test_privacy_mutation_is_owner_only():
    """Only the creator can flip private. A logged-in non-owner gets 403."""
    owner = _login_demo_user()
    approved = _create_and_approve("T32 owner-only fixture")
    _logout()

    # Log in as a different demo user.
    users = client.get("/api/auth/users").json()
    other = next(u for u in users if u["id"] != owner["id"])
    client.post("/api/auth/login", json={"user_id": other["id"]})
    res = client.put(
        f"/api/codelists/{approved['id']}/privacy", json={"private": True},
    )
    assert res.status_code == 403


def test_public_count_sends_cache_control():
    """The hero on the search page calls /count on every render. The
    response carries a short Cache-Control window so a browser doesn't
    hit the DB each time -- pin the header so a future refactor that
    drops it is caught."""
    res = client.get("/api/public/codelists/count")
    assert res.status_code == 200
    cc = res.headers.get("cache-control", "")
    assert "max-age" in cc
    assert "public" in cc


def test_public_count_and_hero_signal():
    """The /count endpoint matches the list length and reacts to private
    flips. The search page reads this to decide whether to render
    'Browse N approved codelists'."""
    _login_demo_user()
    a = _create_and_approve("T32 count fixture A")
    b = _create_and_approve("T32 count fixture B")
    _logout()

    count_before = client.get("/api/public/codelists/count").json()["count"]
    list_len = len(client.get("/api/public/codelists?limit=500").json())
    assert count_before == list_len
    assert count_before >= 2

    # Hide one and count drops by one.
    _login_demo_user()
    client.put(f"/api/codelists/{a['id']}/privacy", json={"private": True})
    _logout()
    assert client.get("/api/public/codelists/count").json()["count"] == count_before - 1

    # Sanity: b is still public.
    assert any(r["id"] == b["id"]
               for r in client.get("/api/public/codelists").json())


def test_public_csv_export_unauth():
    """CSV export of an approved+!private row works without a cookie and
    drops UMLS-suggestion rows (2 data rows, not 3)."""
    _login_demo_user()
    approved = _create_and_approve("T32 csv export fixture")
    _logout()

    res = client.get(
        f"/api/public/codelists/{approved['id']}/export?format=csv",
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    text = res.text
    # 1 header line + 2 data rows.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 1 + 2
    assert "ai_rationale" in lines[0]


def test_public_ohdsi_export_unauth():
    """OHDSI export shape works on the public route too -- pinned for the
    'public artefact round-trips into ATLAS' demo path."""
    _login_demo_user()
    approved = _create_and_approve("T32 ohdsi export fixture")
    _logout()

    res = client.get(
        f"/api/public/codelists/{approved['id']}/export?format=ohdsi",
    )
    assert res.status_code == 200
    body = res.json()
    assert "concept_set" in body and "unmapped" in body
    assert body["concept_set"]["name"] == "T32 ohdsi export fixture"


def test_migration_backfills_private_one_on_pre_t32_rows():
    """Pre-T32 codelists were approved under a no-public-gallery mental
    model; the migration must back-fill those rows to private=1 rather
    than leave them at the column DEFAULT 0 (which would auto-publish
    them on deploy). Pins the back-fill against a future refactor
    that 'simplifies' the migration by dropping the UPDATE.

    Uses an in-memory SQLite connection to avoid touching the dev DB
    or its module-level cache; only the migration path is exercised."""
    import sqlite3
    from app.db.hitl_store import _migrate_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Pre-T32 codelists shape: post-T29 (include_criteria /
    # exclude_criteria already present, the T29 ALTER would have added
    # them on a real pre-T29 deployment), pre-T32 (no `private` column
    # yet). All other columns (reviewed_by / reviewed_at /
    # signature_hash / parent_id) have been part of _init_schema since
    # T0 and were always present on a real production DB; keep them
    # here so subsequent migrations (T30 rebuild) find the columns
    # they need to copy across.
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE codelists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'draft',
            query TEXT NOT NULL,
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at TEXT,
            review_notes TEXT,
            signature_hash TEXT,
            parent_id TEXT REFERENCES codelists(id),
            include_criteria TEXT NOT NULL DEFAULT '[]',
            exclude_criteria TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE codelist_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codelist_id TEXT NOT NULL REFERENCES codelists(id),
            code TEXT NOT NULL,
            is_umls_suggestion INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codelist_id TEXT NOT NULL,
            event TEXT NOT NULL,
            user_id INTEGER,
            details TEXT,
            timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
        INSERT INTO users (id, email, name) VALUES (1, 'a@x', 'A');
        INSERT INTO codelists (id, name, query, created_by, status)
            VALUES ('pre1', 'pre-T32 approved', 'q', 1, 'approved'),
                   ('pre2', 'pre-T32 draft', 'q', 1, 'draft');
        """
    )

    _migrate_schema(conn)

    rows = {r["id"]: r["private"]
            for r in conn.execute("SELECT id, private FROM codelists")}
    assert rows == {"pre1": 1, "pre2": 1}, (
        "pre-T32 rows must be back-filled to private=1; got " + repr(rows)
    )

    # Idempotency: a second migration call must not re-back-fill rows
    # the user has since flipped back to public.
    conn.execute("UPDATE codelists SET private = 0 WHERE id = 'pre1'")
    conn.commit()
    _migrate_schema(conn)
    rows_after = {r["id"]: r["private"]
                  for r in conn.execute("SELECT id, private FROM codelists")}
    assert rows_after == {"pre1": 0, "pre2": 1}, (
        "second migration silently re-hid a row the owner had unhidden: "
        + repr(rows_after)
    )

    conn.close()


def test_unknown_codelist_returns_404():
    res = client.get("/api/public/codelists/does-not-exist")
    assert res.status_code == 404


# --- _initials unit tests --------------------------------------------------
#
# Inline pytest parametrize against the helper so the redaction contract
# is visible in one place. Originally bitten by a 'Surname, Forename'
# format silently dropping the surname when the trailing comma was used
# as a token-rejection rule.

@pytest.mark.parametrize("name,expected", [
    ("Dr Jane Smith", "JS"),
    ("Carlos Ramirez", "CR"),
    ("Smith, J.", "SJ"),                # NHS directory format -- regression case
    ("Prof. Mark Patel, PhD", "MP"),    # honorific + suffix both stripped
    ("Sir John Doe", "JD"),
    ("Mary-Anne O'Brien", "MO"),         # hyphen / apostrophe pass through
    ("J. K. Rowling", "JKR"),
    ("", ""),
    (None, ""),
])
def test_initials_handles_common_name_formats(name, expected):
    from app.db.hitl_store import _initials
    assert _initials(name) == expected

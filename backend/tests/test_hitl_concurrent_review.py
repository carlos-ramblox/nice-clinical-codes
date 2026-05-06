"""
Pin the T26 BEGIN IMMEDIATE serialisation guarantee for ``submit_review``.

Without the lock, two reviewers approving the same ``cid`` concurrently
can both pass the existence/status check, both UPDATE
``codelist_decisions``, and both write audit-log rows — last-writer-wins
on ``signature_hash`` and the surviving signature may not match either
reviewer's submitted decisions. CLINICAL_SAFETY.md commits the system
to an "explicit reviewer action" gate per codelist; a clobbered hash
breaks that guarantee.

The wrap is ``BEGIN IMMEDIATE`` + a status re-read inside the
transaction; the second caller (whether concurrent across processes or
sequential after the first commits) sees a definitive 409 rather than
a 200 with a meaningless signature.

The test uses sequential double-review because the in-process singleton
connection serialises Python-level threads at the sqlite3 module's own
lock — a "true race" test against the singleton would never exercise
the BEGIN IMMEDIATE path and would be flaky. Cross-process (multiple
uvicorn workers / Fargate tasks) is where the SQL-level lock matters,
and the contract verified here ("second review on a terminal codelist
returns 409") is exactly what BEGIN IMMEDIATE enforces in that
deployment shape.

Run from backend/:
    pytest tests/test_hitl_concurrent_review.py -v
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
    """Snapshot codelist ids before each test, delete the diff after,
    so the dev SQLite does not accrete fixture rows. Same pattern as
    test_phenotype_adoption / test_signature_hash_criteria.
    """
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


def _login_demo_user() -> dict:
    users = client.get("/api/auth/users").json()
    assert users, "demo users not seeded"
    res = client.post("/api/auth/login", json={"user_id": users[0]["id"]})
    assert res.status_code == 200
    return res.json()


def _seed_search(query: str, codes: list[dict]) -> str:
    from app.api import _search_cache
    sid = uuid.uuid4().hex[:12]
    _search_cache.put(sid, query, codes)
    return sid


_CODES_FIXTURE = [
    {"code": "E11", "term": "T2D", "vocabulary": "ICD-10",
     "decision": "include", "confidence": 0.9, "rationale": "ok", "sources": []},
    {"code": "E10", "term": "T1D", "vocabulary": "ICD-10",
     "decision": "exclude", "confidence": 0.8, "rationale": "ok", "sources": []},
]


def _create_draft() -> str:
    sid = _seed_search("type 2 diabetes", _CODES_FIXTURE)
    res = client.post(
        "/api/codelists",
        json={"search_id": sid, "name": "T26 race fixture"},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _approve_payload(cid: str) -> dict:
    detail = client.get(f"/api/codelists/{cid}").json()
    return {
        "decisions": [
            {"id": d["id"], "human_decision": d["ai_decision"]}
            for d in detail["decisions"]
        ],
        "action": "approve",
        "notes": None,
    }


def test_second_review_after_approve_returns_409():
    """First approval succeeds with a signature. Second approval on the
    same cid raises ConflictError inside ``submit_review`` and the route
    returns 409. This is the load-bearing regression for T26 — without
    BEGIN IMMEDIATE the second call would silently overwrite the first
    reviewer's signature_hash."""
    _login_demo_user()
    cid = _create_draft()

    first = client.post(f"/api/codelists/{cid}/review", json=_approve_payload(cid))
    assert first.status_code == 200, first.text
    first_sig = first.json()["signature_hash"]
    assert first_sig, "first approval must produce a signature_hash"

    second = client.post(f"/api/codelists/{cid}/review", json=_approve_payload(cid))
    assert second.status_code == 409, second.text
    assert "approved" in second.json()["detail"].lower()

    # Authoritative state: signature is the first reviewer's; status is
    # terminal; audit log carries exactly one 'approved' event. If the
    # lock were removed the second approval would clobber signature_hash
    # and we'd see two 'approved' events.
    detail = client.get(f"/api/codelists/{cid}").json()
    assert detail["status"] == "approved"
    assert detail["signature_hash"] == first_sig

    audit = client.get(f"/api/codelists/{cid}/audit").json()
    approved_events = [e for e in audit if e["event"] == "approved"]
    assert len(approved_events) == 1, (
        f"expected exactly one 'approved' audit event, got {len(approved_events)}: {audit}"
    )


def test_second_review_after_reject_returns_409():
    """Same contract as the approve path: a rejected codelist is also
    terminal, a follow-up review attempt must 409. Pins that the in-
    transaction status check covers both terminal states, not only
    'approved'."""
    _login_demo_user()
    cid = _create_draft()

    payload = _approve_payload(cid)
    payload["action"] = "reject"
    first = client.post(f"/api/codelists/{cid}/review", json=payload)
    assert first.status_code == 200, first.text

    second = client.post(f"/api/codelists/{cid}/review", json=_approve_payload(cid))
    assert second.status_code == 409, second.text
    assert "rejected" in second.json()["detail"].lower()


def test_review_missing_codelist_still_returns_404():
    """Regression: the BEGIN IMMEDIATE wrap must not change the
    not-found path. ``submit_review`` still raises KeyError inside the
    transaction (rolls back); the route translates that to 404, not
    409."""
    _login_demo_user()
    res = client.post(
        "/api/codelists/does-not-exist/review",
        json={"decisions": [], "action": "approve", "notes": None},
    )
    assert res.status_code == 404, res.text


def test_legacy_single_reviewer_happy_path_unchanged():
    """End-to-end smoke test that the BEGIN IMMEDIATE wrap doesn't
    regress the single-reviewer happy path: create draft, approve,
    observe a signature_hash + ``override_count=0``. The defensive
    ``conn.commit()`` at the top of submit_review is a no-op in this
    path (the prior ``create_codelist`` already committed), so this
    test does NOT pin the defensive commit specifically — it pins the
    happy-path equivalence under the new transaction wrap."""
    _login_demo_user()
    cid = _create_draft()

    res = client.post(f"/api/codelists/{cid}/review", json=_approve_payload(cid))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "approved"
    assert body["signature_hash"]
    assert body["override_count"] == 0


def test_submit_review_refuses_v2_codelist():
    """The legacy ``submit_review`` path is single-reviewer (v1) only.
    A codelist with ``signature_version=2`` (the two-reviewer Delphi
    flow, set by ``POST /reviewers`` in step 5) must be rejected
    before any decision rows are mutated — silently overwriting
    ``human_decision`` outside the per-reviewer flow would corrupt
    the Delphi audit chain.

    Tests the store-level guard directly: ``submit_review`` raises
    ``ValueError`` on a v2 codelist. Step 5's route refactor will
    catch the ValueError and translate to an HTTP code (likely 422
    or 409); we don't go through the route here because the route
    has not yet been refactored to expect v2-shaped traffic, and
    TestClient propagates uncaught exceptions instead of converting
    them to 500."""
    import pytest as _pytest
    from app.db.hitl_store import get_connection, submit_review

    _login_demo_user()
    cid = _create_draft()

    # Promote the row to v2 directly. In production this happens via
    # POST /reviewers (step 5); for this test we bypass the route to
    # exercise submit_review's guard in isolation.
    conn = get_connection()
    conn.execute(
        "UPDATE codelists SET signature_version = 2 WHERE id = ?", (cid,),
    )
    conn.commit()

    payload = _approve_payload(cid)
    with _pytest.raises(ValueError, match="two-reviewer"):
        submit_review(
            cid=cid,
            reviewer_id=1,
            decisions=payload["decisions"],
            action=payload["action"],
            notes=payload["notes"],
        )

    # Status must remain unchanged; no signature was written. The
    # try/except/rollback in submit_review handles the cleanup.
    detail = client.get(f"/api/codelists/{cid}").json()
    assert detail["status"] == "draft"
    assert detail.get("signature_hash") in (None, "")

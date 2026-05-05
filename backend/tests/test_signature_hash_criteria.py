"""
Pin the T29 backward-compat and determinism contract for codelist
``signature_hash``.

CLINICAL_SAFETY.md commits to: "deterministic SHA-256 content-hash that
detects post-approval edits". An unconditional change to the signature
payload format would invalidate every pre-T29 approved hash on the next
verification — the audit log would falsely flag tampering for codelists
that haven't actually been edited. T29 therefore appends the criteria
block CONDITIONALLY (only when at least one criterion is set) and
sorts both lists before serialisation so semantically-equal intents
hash identically. These tests pin both properties.

Run from backend/:
    pytest tests/test_signature_hash_criteria.py -v
"""
from __future__ import annotations

import hashlib
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
    """Snapshot codelist ids before each test, delete the diff after, so the
    dev SQLite does not accrete fixture rows. Same pattern as
    test_phenotype_adoption."""
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


def _seed_search(query: str, codes: list[dict], **criteria) -> str:
    """Insert a search-cache entry directly so we don't run the pipeline.
    ``criteria`` may contain ``include_criteria`` / ``exclude_criteria``."""
    from app.api import _search_cache
    sid = uuid.uuid4().hex[:12]
    _search_cache.put(sid, query, codes, **criteria)
    return sid


_CODES_FIXTURE = [
    {"code": "E11", "term": "T2D", "vocabulary": "ICD-10",
     "decision": "include", "confidence": 0.9, "rationale": "ok", "sources": []},
    {"code": "E10", "term": "T1D", "vocabulary": "ICD-10",
     "decision": "exclude", "confidence": 0.8, "rationale": "ok", "sources": []},
]


def _approve_with_no_overrides(cid: str) -> dict:
    """Approve a codelist taking the AI decisions verbatim. The signature
    is computed from human_decision, which equals ai_decision when the
    reviewer doesn't override — so the input set is fully determined by
    the seeded codes."""
    detail = client.get(f"/api/codelists/{cid}").json()
    decisions = [
        {"id": d["id"], "human_decision": d["ai_decision"]}
        for d in detail["decisions"]
    ]
    res = client.post(
        f"/api/codelists/{cid}/review",
        json={"decisions": decisions, "action": "approve", "notes": None},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _legacy_payload_hash(decisions: list[dict]) -> str:
    """Reproduce the pre-T29 signature payload byte-for-byte. If T29's
    conditional-append is correct, an empty-criteria codelist's signature
    must equal this value."""
    # Decisions sorted by (code, vocabulary) to match _compute_signature's
    # ORDER BY clause.
    rows = sorted(decisions, key=lambda d: (d["code"], d["vocabulary"]))
    payload = "\n".join(
        f"{d['code']}|{d['vocabulary']}|{d['decision']}" for d in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_empty_criteria_signature_byte_compat_with_pre_T29():
    """A codelist created with no criteria must produce the same SHA-256
    as the pre-T29 formula. This is the load-bearing backward-compat
    property: every existing approved hash in production verifies
    unchanged after T29 ships."""
    _login_demo_user()
    sid = _seed_search("type 2 diabetes", _CODES_FIXTURE)
    cl = client.post(
        "/api/codelists",
        json={"search_id": sid, "name": "T29 byte-compat fixture"},
    ).json()
    result = _approve_with_no_overrides(cl["id"])

    expected = _legacy_payload_hash(_CODES_FIXTURE)
    assert result["signature_hash"] == expected, (
        f"empty-criteria signature diverged from legacy formula:\n"
        f"  got      {result['signature_hash']}\n"
        f"  expected {expected}"
    )


def test_signature_deterministic_against_criteria_list_order():
    """Two codelists with the same codes and the same criteria *set* but
    different list ORDER must hash identically. _compute_signature sorts
    both lists before serialisation; this pins that sort."""
    _login_demo_user()

    sid_a = _seed_search(
        "diabetes",
        _CODES_FIXTURE,
        exclude_criteria=["gestational", "type 1"],
    )
    cl_a = client.post(
        "/api/codelists",
        json={"search_id": sid_a, "name": "T29 order A"},
    ).json()
    sig_a = _approve_with_no_overrides(cl_a["id"])["signature_hash"]

    sid_b = _seed_search(
        "diabetes",
        _CODES_FIXTURE,
        exclude_criteria=["type 1", "gestational"],  # same set, swapped order
    )
    cl_b = client.post(
        "/api/codelists",
        json={"search_id": sid_b, "name": "T29 order B"},
    ).json()
    sig_b = _approve_with_no_overrides(cl_b["id"])["signature_hash"]

    assert sig_a == sig_b, (
        f"criteria order leaked into signature:\n  A {sig_a}\n  B {sig_b}"
    )


def test_signature_changes_when_criteria_become_non_empty():
    """The conditional append must actually take effect. Two codelists
    with identical codes but different criteria-presence must diverge —
    otherwise the criteria are silently absent from the audit guarantee."""
    _login_demo_user()

    sid_empty = _seed_search("diabetes", _CODES_FIXTURE)
    cl_empty = client.post(
        "/api/codelists",
        json={"search_id": sid_empty, "name": "T29 empty-criteria"},
    ).json()
    sig_empty = _approve_with_no_overrides(cl_empty["id"])["signature_hash"]

    sid_nonempty = _seed_search(
        "diabetes",
        _CODES_FIXTURE,
        exclude_criteria=["gestational"],
    )
    cl_nonempty = client.post(
        "/api/codelists",
        json={"search_id": sid_nonempty, "name": "T29 non-empty-criteria"},
    ).json()
    sig_nonempty = _approve_with_no_overrides(cl_nonempty["id"])["signature_hash"]

    assert sig_empty != sig_nonempty, (
        "criteria didn't change the signature — conditional append is a no-op"
    )
    # Belt-and-braces: the empty case still equals the legacy formula.
    assert sig_empty == _legacy_payload_hash(_CODES_FIXTURE)

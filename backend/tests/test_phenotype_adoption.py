"""
Tests for the "use this phenotype" adoption flow (T34b).

Adoptions are recorded as ``phenotype_adopted`` audit-log events by
``hitl_store.create_codelist`` -- no separate adoptions table -- so
tamper-evidence is shared with the decision-override events. The
tests below pin both the audit-log shape and the read-side
``GET /api/codelists/{id}`` projection that surfaces adoptions to
the frontend.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

# Allow `import app.*` whether the test is invoked from backend/ or repo root.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup_test_codelists():
    """Drop any codelist rows the test created so the dev SQLite DB does
    not accrete fixture rows across runs. Same pattern as the cross-
    reference test file: snapshot ids before, delete the diff after.
    """
    from app.db.hitl_store import get_connection
    conn = get_connection()
    pre_ids = {r["id"] for r in conn.execute("SELECT id FROM codelists")}
    yield
    post_ids = {r["id"] for r in conn.execute("SELECT id FROM codelists")}
    new_ids = post_ids - pre_ids
    if not new_ids:
        return
    placeholders = ",".join(["?"] * len(new_ids))
    params = list(new_ids)
    conn.execute(f"DELETE FROM audit_log WHERE codelist_id IN ({placeholders})", params)
    conn.execute(f"DELETE FROM codelist_decisions WHERE codelist_id IN ({placeholders})", params)
    conn.execute(f"DELETE FROM codelists WHERE id IN ({placeholders})", params)
    conn.commit()

def _login_demo_user():
    users = client.get("/api/auth/users").json()
    if not users:
        return None
    res = client.post("/api/auth/login", json={"user_id": users[0]["id"]})
    return res.json() if res.status_code == 200 else None


def _seed_search(query: str, codes: list[dict]) -> str:
    """Insert a search-cache entry directly so we don't run the pipeline."""
    from app.api import _search_cache
    sid = uuid.uuid4().hex[:12]
    _search_cache.put(sid, query, codes)
    return sid


def _create(payload: dict) -> dict:
    res = client.post("/api/codelists", json=payload)
    assert res.status_code == 201, res.text
    return res.json()


def test_create_codelist_without_adoptions_keeps_back_compat():
    # Existing callers that POST {search_id, name} (no adoptions field)
    # still work; the field defaults to [].
    assert _login_demo_user() is not None
    sid = _seed_search("asthma", [
        {"code": "J45", "term": "Asthma", "vocabulary": "ICD-10",
         "decision": "include", "confidence": 0.9, "rationale": "ok", "sources": []},
    ])
    cl = _create({"search_id": sid, "name": "T34b back-compat"})
    assert cl["adopted_phenotypes"] == []


def test_create_codelist_with_adoptions_records_audit_events():
    # Each adoption becomes its own phenotype_adopted audit event with
    # the citation metadata in details. Audit log is the single source
    # of truth (no separate adoptions table); tamper-evidence shared
    # with the decision-override events.
    assert _login_demo_user() is not None
    sid = _seed_search("type 2 diabetes", [
        {"code": "E11", "term": "T2D", "vocabulary": "ICD-10",
         "decision": "include", "confidence": 0.9, "rationale": "ok", "sources": []},
    ])
    adoptions = [
        {
            "phenotype_id": "PH8",
            "name": "Diabetes",
            "hdruk_url": "https://phenotypes.healthdatagateway.org/phenotypes/PH8",
            "first_publication": "Diabetes UK methods 2024",
        },
        {
            "phenotype_id": "PH152",
            "name": "Diabetes (multi-coding)",
            "hdruk_url": "https://phenotypes.healthdatagateway.org/phenotypes/PH152",
            "first_publication": "",
        },
    ]
    cl = _create({"search_id": sid, "name": "T34b adoption fixture",
                  "adopted_phenotypes": adoptions})

    audit = client.get(f"/api/codelists/{cl['id']}/audit").json()
    adopted_events = [e for e in audit if e["event"] == "phenotype_adopted"]
    assert len(adopted_events) == 2
    by_pid = {e["details"]["phenotype_id"]: e for e in adopted_events}
    assert "PH8" in by_pid and "PH152" in by_pid
    assert by_pid["PH8"]["details"]["name"] == "Diabetes"
    assert by_pid["PH8"]["details"]["hdruk_url"].endswith("/phenotypes/PH8")
    assert by_pid["PH8"]["details"]["first_publication"] == "Diabetes UK methods 2024"


def test_get_codelist_surfaces_adopted_phenotypes_to_frontend():
    # The frontend reads codelist.adopted_phenotypes from GET
    # /api/codelists/{id}; the projection replays audit events filtered
    # to event='phenotype_adopted' so the read path stays consistent
    # whether the adoptions came from this codelist's create call or
    # any future post-creation adoption mutation.
    assert _login_demo_user() is not None
    sid = _seed_search("asthma", [
        {"code": "J45", "term": "Asthma", "vocabulary": "ICD-10",
         "decision": "include", "confidence": 0.9, "rationale": "ok", "sources": []},
    ])
    adoptions = [{
        "phenotype_id": "PH12",
        "name": "Asthma",
        "hdruk_url": "https://phenotypes.healthdatagateway.org/phenotypes/PH12",
        "first_publication": "Nissen et al. 2017",
    }]
    cl = _create({"search_id": sid, "name": "T34b read-side fixture",
                  "adopted_phenotypes": adoptions})

    res = client.get(f"/api/codelists/{cl['id']}")
    assert res.status_code == 200
    body = res.json()
    assert "adopted_phenotypes" in body
    assert len(body["adopted_phenotypes"]) == 1
    row = body["adopted_phenotypes"][0]
    assert row["phenotype_id"] == "PH12"
    assert row["name"] == "Asthma"
    assert row["hdruk_url"].endswith("/phenotypes/PH12")
    assert row["first_publication"] == "Nissen et al. 2017"


def test_create_codelist_validates_adoption_payload_shape():
    # Adoption shape is enforced by Pydantic so the frontend cannot
    # silently submit malformed entries. 422 from FastAPI's validator.
    assert _login_demo_user() is not None
    sid = _seed_search("asthma", [
        {"code": "J45", "term": "x", "vocabulary": "ICD-10",
         "decision": "include", "confidence": 0.9, "rationale": "ok", "sources": []},
    ])
    res = client.post("/api/codelists", json={
        "search_id": sid,
        "name": "T34b validation fixture",
        "adopted_phenotypes": [{"phenotype_id": ""}],  # missing name + hdruk_url
    })
    assert res.status_code == 422

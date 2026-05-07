"""OpenCodelists upload-CSV export contract."""
from __future__ import annotations

import csv
import io
import json
import sys
import uuid
import zipfile
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
    conn.execute(
        f"DELETE FROM decision_votes WHERE decision_id IN "
        f"(SELECT id FROM codelist_decisions WHERE codelist_id IN ({placeholders}))",
        params,
    )
    conn.execute(f"DELETE FROM audit_log WHERE codelist_id IN ({placeholders})", params)
    conn.execute(f"DELETE FROM codelist_decisions WHERE codelist_id IN ({placeholders})", params)
    conn.execute(f"DELETE FROM codelists WHERE id IN ({placeholders})", params)
    conn.commit()


# --- helpers ---------------------------------------------------------------

def _users() -> list[dict]:
    return client.get("/api/auth/users").json()


def _login(user_id: int) -> dict:
    res = client.post("/api/auth/login", json={"user_id": user_id})
    assert res.status_code == 200
    return res.json()


def _seed_search(query: str, codes: list[dict]) -> str:
    from app.api import _search_cache
    sid = uuid.uuid4().hex[:12]
    _search_cache.put(sid, query, codes)
    return sid


# Mixed SNOMED + ICD-10 + off-corpus QOF + one excluded row.
_CODES_FIXTURE = [
    {"code": "44054006", "term": "Diabetes mellitus type 2",
     "vocabulary": "SNOMED CT", "decision": "include",
     "confidence": 0.9, "rationale": "primary T2D code", "sources": []},
    {"code": "73211009", "term": "Diabetes mellitus",
     "vocabulary": "SNOMED CT", "decision": "include",
     "confidence": 0.85, "rationale": "umbrella term", "sources": []},
    {"code": "E11", "term": "Type 2 diabetes mellitus",
     "vocabulary": "ICD-10 (WHO)", "decision": "include",
     "confidence": 0.95, "rationale": "ICD-10 primary", "sources": []},
    {"code": "QOF-DM-001", "term": "QOF-only marker",
     "vocabulary": "QOF Business Rules", "decision": "include",
     "confidence": 0.7, "rationale": "rule reference", "sources": []},
    {"code": "46635009", "term": "Type 1 diabetes mellitus",
     "vocabulary": "SNOMED CT", "decision": "exclude",
     "confidence": 0.8, "rationale": "out of scope", "sources": []},
]


def _create_draft_as(creator_id: int) -> str:
    _login(creator_id)
    sid = _seed_search("type 2 diabetes", _CODES_FIXTURE)
    res = client.post(
        "/api/codelists",
        json={"search_id": sid, "name": "T33 export fixture"},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _decision_ids_for(cid: str) -> list[dict]:
    detail = client.get(f"/api/codelists/{cid}").json()
    return detail["decisions"]


def _approve_via_two_reviewers(creator: int, r1: int, r2: int) -> str:
    """Drive a codelist through the unanimous-approval v2 path so we
    end up at status='approved', signature_version=2."""
    cid = _create_draft_as(creator)
    _login(creator)
    res = client.post(
        f"/api/codelists/{cid}/reviewers",
        json={"reviewer_ids": [r1, r2]},
    )
    assert res.status_code == 200, res.text

    # Both reviewers vote identically on every decision (mirroring the
    # AI's decision so no override comment is needed) -> unanimous,
    # auto-approves on second finalisation.
    decisions = _decision_ids_for(cid)
    votes = [
        {"decision_id": d["id"], "vote": d["ai_decision"]}
        for d in decisions
    ]
    for reviewer in (r1, r2):
        _login(reviewer)
        res = client.post(
            f"/api/codelists/{cid}/review",
            json={"votes": votes, "is_final": True},
        )
        assert res.status_code == 200, res.text

    detail = client.get(f"/api/codelists/{cid}").json()
    assert detail["status"] == "approved", detail
    assert detail["signature_version"] == 2
    return cid


def _approve_via_single_reviewer(creator: int) -> str:
    """Legacy v1 single-reviewer approval -- the negative case the
    export endpoint must refuse."""
    cid = _create_draft_as(creator)
    _login(creator)
    decisions = _decision_ids_for(cid)
    payload = {
        "decisions": [
            {"id": d["id"], "human_decision": d["ai_decision"]}
            for d in decisions
        ],
        "action": "approve",
    }
    res = client.post(f"/api/codelists/{cid}/review", json=payload)
    assert res.status_code == 200, res.text
    detail = client.get(f"/api/codelists/{cid}").json()
    assert detail["status"] == "approved"
    assert detail["signature_version"] == 1
    return cid


# --- positive case (mixed-vocabulary split) -------------------------------

def test_mixed_vocabulary_emits_one_csv_per_opencodelists_coding_system():
    """SNOMED + ICD-10 split into two CSVs; QOF lands in dropped_codes; excluded rows filtered."""
    users = _users()
    creator, r1, r2 = users[0]["id"], users[1]["id"], users[2]["id"]
    cid = _approve_via_two_reviewers(creator, r1, r2)

    res = client.get(f"/api/codelists/{cid}/export.opencodelists.csv")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/zip"), res.headers["content-type"]
    cd = res.headers["content-disposition"]
    assert cd.startswith("attachment; filename=\""), cd
    assert cd.endswith(".opencodelists.zip\""), cd

    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        names = zf.namelist()
        csv_names = sorted(n for n in names if n.endswith(".csv"))
        prov_names = [n for n in names if n.endswith(".provenance.json")]
        assert len(prov_names) == 1
        # Two coding systems represented (SNOMED CT + ICD-10) -> two CSVs.
        assert len(csv_names) == 2, csv_names
        assert any(n.endswith(".snomedct.csv") for n in csv_names)
        assert any(n.endswith(".icd10.csv") for n in csv_names)

        # OpenCodelists's CSVValidationMixin matches the literal string.
        for csv_name in csv_names:
            with zf.open(csv_name) as fh:
                text = fh.read().decode("utf-8")
            assert text.splitlines()[0] == "code,term", csv_name

        snomed = {
            row["code"]: row
            for row in csv.DictReader(
                io.StringIO(zf.read(next(n for n in csv_names if n.endswith(".snomedct.csv"))).decode("utf-8"))
            )
        }
        icd10 = {
            row["code"]: row
            for row in csv.DictReader(
                io.StringIO(zf.read(next(n for n in csv_names if n.endswith(".icd10.csv"))).decode("utf-8"))
            )
        }
        assert set(snomed.keys()) == {"44054006", "73211009"}, sorted(snomed.keys())
        assert set(icd10.keys()) == {"E11"}, sorted(icd10.keys())

        prov = json.loads(zf.read(prov_names[0]).decode("utf-8"))
        assert prov["source"] == "clinicalcodes.uk"
        assert prov["signature"]["signature_version"] == 2
        assert prov["signature"]["value"], "approved row must carry a signature"
        assert sorted(prov["reviewers"]["reviewer_ids"]) == sorted([r1, r2])

        manifest = {entry["opencodelists_slug"]: entry for entry in prov["coding_systems"]}
        assert set(manifest.keys()) == {"snomedct", "icd10"}
        assert manifest["snomedct"]["code_count"] == 2
        assert manifest["icd10"]["code_count"] == 1
        assert manifest["snomedct"]["csv_filename"] in csv_names
        assert manifest["icd10"]["csv_filename"] in csv_names

        dropped_codes = {d["code"] for d in prov["dropped_codes"]}
        assert dropped_codes == {"QOF-DM-001"}, prov["dropped_codes"]
        assert prov["dropped_codes"][0]["vocabulary"] == "QOF Business Rules"


# --- negative cases --------------------------------------------------------

def test_v1_single_reviewer_approved_returns_409():
    """Legacy v1 (single-reviewer) approval is refused — two-reviewer trail required."""
    users = _users()
    creator = users[0]["id"]
    cid = _approve_via_single_reviewer(creator)

    res = client.get(f"/api/codelists/{cid}/export.opencodelists.csv")
    assert res.status_code == 409, res.text
    assert "two-reviewer" in res.json()["detail"]


def test_all_unsupported_vocabulary_returns_422():
    """All-QOF (no OpenCodelists slug) fails loud rather than emit a ZIP without CSVs."""
    from app.api import _search_cache

    users = _users()
    creator, r1, r2 = users[0]["id"], users[1]["id"], users[2]["id"]
    _login(creator)
    sid = uuid.uuid4().hex[:12]
    _search_cache.put(sid, "qof-only fixture", [
        {"code": "QOF-001", "term": "QOF marker A",
         "vocabulary": "QOF Business Rules", "decision": "include",
         "confidence": 0.9, "rationale": "rule", "sources": []},
        {"code": "QOF-002", "term": "QOF marker B",
         "vocabulary": "QOF Business Rules", "decision": "include",
         "confidence": 0.85, "rationale": "rule", "sources": []},
    ])
    create = client.post(
        "/api/codelists",
        json={"search_id": sid, "name": "T33 422 fixture"},
    )
    assert create.status_code == 201, create.text
    cid = create.json()["id"]

    # Assert each setup call so a v2-approval regression doesn't trip the
    # 409 "not approved" guard before the 422 "no mappable vocab" we test for.
    res = client.post(
        f"/api/codelists/{cid}/reviewers",
        json={"reviewer_ids": [r1, r2]},
    )
    assert res.status_code == 200, res.text
    decisions = _decision_ids_for(cid)
    votes = [{"decision_id": d["id"], "vote": d["ai_decision"]} for d in decisions]
    for reviewer in (r1, r2):
        _login(reviewer)
        res = client.post(
            f"/api/codelists/{cid}/review",
            json={"votes": votes, "is_final": True},
        )
        assert res.status_code == 200, res.text
    assert client.get(f"/api/codelists/{cid}").json()["status"] == "approved"

    res = client.get(f"/api/codelists/{cid}/export.opencodelists.csv")
    assert res.status_code == 422, res.text
    assert "OpenCodelists coding system" in res.json()["detail"]


# --- runner ---------------------------------------------------------------

def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {t.__name__}")
    return failed


if __name__ == "__main__":
    sys.exit(_run_all())

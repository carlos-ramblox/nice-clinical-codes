"""Pin the OHDSI concept-set JSON export contract.

Covers schema validity, isExcluded mapping, the unmapped-array
fallback for codes without a concept_id, and an ATLAS-paste smoke
through the /api/export route.

    pytest tests/test_ohdsi_export.py -v
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.exports.ohdsi import to_ohdsi_concept_set  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


# Required keys per the OHDSI spec / circe-be ConceptSetItem.
_CONCEPT_KEYS = {"CONCEPT_ID", "VOCABULARY_ID", "CONCEPT_CODE", "CONCEPT_NAME"}
_ITEM_KEYS = {"concept", "isExcluded", "includeDescendants", "includeMapped"}


def _diabetes_fixture() -> list[dict]:
    """Two mapped + one unmapped + one uncertain."""
    return [
        {"code": "44054006", "term": "Diabetes mellitus type 2",
         "vocabulary": "SNOMED CT", "decision": "include",
         "concept_id": 201826},
        {"code": "E10", "term": "Type 1 diabetes mellitus",
         "vocabulary": "ICD-10 (WHO)", "decision": "exclude",
         "concept_id": 201254},
        # UMLS suggestion: no OMOP concept_id -> unmapped.
        {"code": "C0011860", "term": "Diabetes Mellitus, Non-Insulin-Dependent",
         "vocabulary": "UMLS", "decision": "include",
         "concept_id": None},
        # uncertain -> withheld from both arrays.
        {"code": "73211009", "term": "Diabetes mellitus",
         "vocabulary": "SNOMED CT", "decision": "uncertain",
         "concept_id": 73211},
    ]


# --- 1. Schema-valid -------------------------------------------------------

def test_schema_validity_against_ohdsi_concept_set_specification():
    out = to_ohdsi_concept_set("type 2 diabetes", _diabetes_fixture())
    cs = out["concept_set"]

    assert set(cs.keys()) == {"id", "name", "expression"}
    assert isinstance(cs["id"], int)
    assert cs["name"] == "type 2 diabetes"

    items = cs["expression"]["items"]
    assert len(items) == 2  # mapped include + mapped exclude; unmapped + uncertain elsewhere

    for item in items:
        assert set(item.keys()) == _ITEM_KEYS
        assert set(item["concept"].keys()) == _CONCEPT_KEYS
        assert isinstance(item["concept"]["CONCEPT_ID"], int)
        assert isinstance(item["concept"]["VOCABULARY_ID"], str)
        assert isinstance(item["concept"]["CONCEPT_CODE"], str)
        assert isinstance(item["concept"]["CONCEPT_NAME"], str)
        assert item["concept"]["VOCABULARY_ID"] in {"SNOMED", "ICD10"}
        assert isinstance(item["isExcluded"], bool)
        assert isinstance(item["includeDescendants"], bool)
        assert isinstance(item["includeMapped"], bool)


# --- 2. isExcluded mapping -------------------------------------------------

def test_isExcluded_mirrors_decision_exclude():
    out = to_ohdsi_concept_set("t2dm", _diabetes_fixture())
    items = {it["concept"]["CONCEPT_CODE"]: it for it in out["concept_set"]["expression"]["items"]}

    assert items["44054006"]["isExcluded"] is False
    assert items["E10"]["isExcluded"] is True

    for it in items.values():
        assert it["includeDescendants"] is False
        assert it["includeMapped"] is False


# --- 3. Unmapped surface ---------------------------------------------------

def test_unmapped_codes_surface_in_parallel_array_not_items():
    """Codes without a concept_id must surface in unmapped, never as items
    (ATLAS rejects items with a non-integer CONCEPT_ID)."""
    out = to_ohdsi_concept_set("t2dm", _diabetes_fixture())

    items = out["concept_set"]["expression"]["items"]
    item_codes = {it["concept"]["CONCEPT_CODE"] for it in items}
    assert "C0011860" not in item_codes

    unmapped = out["unmapped"]
    assert len(unmapped) == 1
    [umls] = unmapped
    assert umls == {
        "code": "C0011860",
        "vocabulary": "UMLS",
        "term": "Diabetes Mellitus, Non-Insulin-Dependent",
        "decision": "include",
    }

    # uncertain rows go into NEITHER array.
    assert "73211009" not in item_codes
    assert all(u["code"] != "73211009" for u in unmapped)


# --- 4. ATLAS-paste smoke --------------------------------------------------

def test_atlas_paste_smoke_route_round_trip():
    """End-to-end through /api/export with a JSON round-trip on the
    concept_set (mirrors ATLAS' Concept Set Import dialog)."""
    from app.api import _search_cache

    sid = uuid.uuid4().hex[:12]
    codes = _diabetes_fixture()
    _search_cache.put(sid, "type 2 diabetes", codes)

    response = client.get(f"/api/export/{sid}?output_format=ohdsi")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()
    assert json.loads(json.dumps(payload["concept_set"])) == payload["concept_set"]
    assert payload == to_ohdsi_concept_set("type 2 diabetes", codes)


# --- Runner ---------------------------------------------------------------

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

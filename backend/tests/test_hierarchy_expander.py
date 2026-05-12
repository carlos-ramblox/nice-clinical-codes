"""hierarchy_expander adds 'Is a' descendants of LLM-included concepts."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("OMOPHUB_API_KEY", "dummy")

from app.graph.nodes import hierarchy_expander as he  # noqa: E402


def _scored(code, vocab, decision="include", concept_id=None):
    return {
        "code": code, "term": f"term {code}", "vocabulary": vocab,
        "decision": decision, "confidence": 0.95, "rationale": "r",
        "sources": ["OMOPHub"], "usage_frequency": None,
        "usage_status": None, "usage_source": None, "usage_setting": None,
        "concept_id": concept_id, "dmd_level": None,
    }


def _desc(concept_id, code, vocab="SNOMED", standard="S"):
    return {
        "concept_id": concept_id, "concept_name": f"desc {code}",
        "concept_code": code, "vocabulary_id": vocab,
        "standard_concept": standard, "domain_id": "Condition",
    }


class _FakeHierarchy:
    def __init__(self, mapping):
        self._mapping = mapping
        self.calls: list[int] = []

    def descendants(self, concept_id, **kwargs):
        self.calls.append(concept_id)
        return {"descendants": list(self._mapping.get(concept_id, []))}


class _FakeClient:
    def __init__(self, hierarchy):
        self.hierarchy = hierarchy


def _reset_state():
    with he._CACHE_LOCK:
        he._CACHE.clear()
    with he._client_lock:
        he._client = None


def _patched(hierarchy):
    return patch.object(he, "OMOPHub", lambda **_: _FakeClient(hierarchy))


def test_included_parent_gets_expanded_with_standard_descendants():
    _reset_state()
    fake = _FakeHierarchy({
        201826: [
            _desc(45271, "DESC1", "SNOMED", "S"),
            _desc(46271, "E11.9", "ICD10", "S"),
        ],
    })
    state = {"scored_codes": [
        _scored("PARENT", "SNOMED CT", decision="include", concept_id=201826),
    ]}
    with _patched(fake):
        out = he.expand_hierarchy(state)
    codes = sorted(c["code"] for c in out["scored_codes"])
    assert codes == ["DESC1", "E11.9", "PARENT"]
    descendants = [c for c in out["scored_codes"] if "Expanded via" in c["rationale"]]
    assert len(descendants) == 2
    assert all(d["decision"] == "include" for d in descendants)


def test_uncertain_and_excluded_parents_skipped():
    _reset_state()
    fake = _FakeHierarchy({201826: [_desc(45271, "44054006")]})
    state = {"scored_codes": [
        _scored("X", "SNOMED CT", decision="uncertain", concept_id=201826),
        _scored("Y", "SNOMED CT", decision="exclude", concept_id=201826),
    ]}
    with _patched(fake):
        out = he.expand_hierarchy(state)
    assert out == {}
    assert fake.calls == []


def test_non_standard_descendants_dropped():
    _reset_state()
    fake = _FakeHierarchy({201826: [
        _desc(45271, "X1", "SNOMED", "S"),
        _desc(45272, "X2", "SNOMED", "C"),  # non-standard, dropped
        _desc(45273, "X3", "SNOMED", None),
    ]})
    state = {"scored_codes": [
        _scored("P", "SNOMED CT", decision="include", concept_id=201826),
    ]}
    with _patched(fake):
        out = he.expand_hierarchy(state)
    added = [c for c in out["scored_codes"] if "Expanded via" in c["rationale"]]
    assert [c["code"] for c in added] == ["X1"]


def test_non_omop_vocab_descendants_dropped():
    _reset_state()
    fake = _FakeHierarchy({201826: [
        _desc(45271, "X1", "SNOMED", "S"),
        _desc(45272, "RX1", "RxNorm", "S"),  # outside OMOPHUB_VOCABULARIES
    ]})
    state = {"scored_codes": [
        _scored("P", "SNOMED CT", decision="include", concept_id=201826),
    ]}
    with _patched(fake):
        out = he.expand_hierarchy(state)
    added = [c for c in out["scored_codes"] if "Expanded via" in c["rationale"]]
    assert [c["code"] for c in added] == ["X1"]


def test_duplicate_descendants_collapse_against_existing_codes():
    _reset_state()
    fake = _FakeHierarchy({201826: [_desc(45271, "ALREADY", "SNOMED", "S")]})
    state = {"scored_codes": [
        _scored("P", "SNOMED CT", decision="include", concept_id=201826),
        _scored("ALREADY", "SNOMED CT", decision="exclude", concept_id=999),
    ]}
    with _patched(fake):
        out = he.expand_hierarchy(state)
    assert out == {}, "duplicate should not be re-added"


def test_no_concept_id_parents_skipped():
    _reset_state()
    fake = _FakeHierarchy({})
    state = {"scored_codes": [
        _scored("P", "SNOMED CT", decision="include", concept_id=None),
    ]}
    with _patched(fake):
        out = he.expand_hierarchy(state)
    assert out == {}
    assert fake.calls == []


def test_cache_avoids_second_lookup_for_same_concept_id():
    _reset_state()
    fake = _FakeHierarchy({201826: [_desc(45271, "X1", "SNOMED", "S")]})
    state = {"scored_codes": [
        _scored("P", "SNOMED CT", decision="include", concept_id=201826),
    ]}
    with _patched(fake):
        he.expand_hierarchy(state)
        he.expand_hierarchy(state)
    assert fake.calls == [201826]  # second call served from cache


def test_missing_api_key_is_a_no_op():
    _reset_state()
    fake = _FakeHierarchy({201826: [_desc(45271, "X1")]})
    state = {"scored_codes": [
        _scored("P", "SNOMED CT", decision="include", concept_id=201826),
    ]}
    with patch.object(he, "OMOPHUB_API_KEY", ""), _patched(fake):
        out = he.expand_hierarchy(state)
    assert out == {}
    assert fake.calls == []

"""Routing gate tests for hierarchy_expander (T37j)."""
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


def _parsed(include_descendants: bool, name: str = "epilepsy") -> dict:
    return {
        "name": name,
        "condition_type": "primary",
        "coding_systems": ["SNOMED", "ICD10"],
        "domain": "Condition",
        "include_criteria": [],
        "exclude_criteria": [],
        "include_descendants": include_descendants,
    }


def test_all_false_conditions_skip_expansion():
    _reset_state()
    fake = _FakeHierarchy({201826: [_desc(45271, "X1", "SNOMED", "S")]})
    state = {
        "parsed_conditions": [_parsed(False), _parsed(False, "asthma")],
        "scored_codes": [
            _scored("P", "SNOMED CT", decision="include", concept_id=201826),
        ],
    }
    with _patched(fake):
        out = he.expand_hierarchy(state)
    assert out == {}
    assert fake.calls == []


def test_missing_parsed_conditions_skip_expansion():
    _reset_state()
    fake = _FakeHierarchy({201826: [_desc(45271, "X1", "SNOMED", "S")]})
    state = {
        "parsed_conditions": [],
        "scored_codes": [
            _scored("P", "SNOMED CT", decision="include", concept_id=201826),
        ],
    }
    with _patched(fake):
        out = he.expand_hierarchy(state)
    assert out == {}
    assert fake.calls == []


def test_any_true_condition_triggers_expansion():
    _reset_state()
    fake = _FakeHierarchy({201826: [_desc(45271, "DESC1", "SNOMED", "S")]})
    state = {
        "parsed_conditions": [_parsed(False, "asthma"), _parsed(True, "epilepsy")],
        "scored_codes": [
            _scored("P", "SNOMED CT", decision="include", concept_id=201826),
        ],
    }
    with _patched(fake):
        out = he.expand_hierarchy(state)
    assert fake.calls == [201826]
    codes = sorted(c["code"] for c in out["scored_codes"])
    assert codes == ["DESC1", "P"]


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
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
            passed += 1
            print(f"PASS {t.__name__}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(_run_all())

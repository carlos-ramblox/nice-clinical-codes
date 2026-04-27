"""
Tests for the vocabulary-constraint filter in
backend/app/graph/nodes/result_merger.py (Fix F).

Two cases per the task brief:
  - vocab constraint set → non-matching codes filtered out before the cap.
  - no constraint (mixed coding_systems or none) → everything passes
    through unfiltered.

Run with pytest from backend/, or as a script:
    python -m tests.test_result_merger_vocab
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.graph.nodes.result_merger import merge_and_dedup
from app.graph.vocab_matching import requested_vocab_set


def _code(c, vocab, source="OMOPHub"):
    return {
        "code": c,
        "term": c,
        "vocabulary": vocab,
        "source": source,
        "domain": "Condition",
        "similarity_score": None,
        "usage_frequency": None,
    }


# --- requested_vocab_set() helper (shared module) ---------------------------

def test_helper_returns_allowed_vocabs_when_single_coding_system():
    conds = [{"coding_systems": ["ICD10"]}]
    assert requested_vocab_set(conds) == ("ICD-10 (WHO)", "ICD-10", "ICD10", "ICD-10-CM")


def test_helper_returns_none_when_two_coding_systems_in_one_condition():
    conds = [{"coding_systems": ["SNOMED", "ICD10"]}]
    assert requested_vocab_set(conds) is None


def test_helper_returns_none_when_conditions_disagree():
    conds = [{"coding_systems": ["ICD10"]}, {"coding_systems": ["SNOMED"]}]
    assert requested_vocab_set(conds) is None


def test_helper_returns_none_for_empty_conditions():
    assert requested_vocab_set([]) is None


def test_helper_returns_opcs4_aliases_when_opcs4_requested():
    conds = [{"coding_systems": ["OPCS4"]}]
    assert requested_vocab_set(conds) == ("OPCS-4", "OPCS4")


# --- merge_and_dedup() filter behaviour --------------------------------------

def test_vocab_constraint_filters_out_non_matching_codes():
    state = {
        "retrieved_codes": [
            _code("I21",       "ICD-10 (WHO)", source="OMOPHub"),
            _code("I22",       "ICD-10 (WHO)", source="OMOPHub"),
            _code("22298006",  "SNOMED CT",    source="QOF"),
            _code("57054005",  "SNOMED CT",    source="OpenCodelists"),
            _code("C0027051",  "UMLS",         source="UMLS (synonym)"),
        ],
        "parsed_conditions": [{"coding_systems": ["ICD10"]}],
    }
    out = merge_and_dedup(state)
    kept = out["enriched_codes"]
    assert {c["code"] for c in kept} == {"I21", "I22"}
    assert all(c["vocabulary"] == "ICD-10 (WHO)" for c in kept)


def test_no_constraint_lets_everything_through():
    state = {
        "retrieved_codes": [
            _code("I21",      "ICD-10 (WHO)", source="OMOPHub"),
            _code("22298006", "SNOMED CT",    source="QOF"),
            _code("57054005", "SNOMED CT",    source="OpenCodelists"),
        ],
        # Default parser output: both vocabularies allowed.
        "parsed_conditions": [{"coding_systems": ["SNOMED", "ICD10"]}],
    }
    out = merge_and_dedup(state)
    kept = out["enriched_codes"]
    assert {c["code"] for c in kept} == {"I21", "22298006", "57054005"}


def test_heterogeneous_conditions_pass_through_unfiltered():
    # Two conditions with different vocab sets — Fix F should NOT trigger.
    state = {
        "retrieved_codes": [
            _code("I21",      "ICD-10 (WHO)"),
            _code("22298006", "SNOMED CT"),
        ],
        "parsed_conditions": [
            {"coding_systems": ["ICD10"]},
            {"coding_systems": ["SNOMED"]},
        ],
    }
    out = merge_and_dedup(state)
    kept_codes = {c["code"] for c in out["enriched_codes"]}
    assert kept_codes == {"I21", "22298006"}


def test_snomed_only_constraint_filters_icd10():
    state = {
        "retrieved_codes": [
            _code("I21",      "ICD-10 (WHO)"),
            _code("22298006", "SNOMED CT"),
        ],
        "parsed_conditions": [{"coding_systems": ["SNOMED"]}],
    }
    out = merge_and_dedup(state)
    assert {c["code"] for c in out["enriched_codes"]} == {"22298006"}


# --- Runner ------------------------------------------------------------------

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

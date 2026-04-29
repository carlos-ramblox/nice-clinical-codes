"""
Tests for the regex-based vocabulary cue extraction in
backend/app/graph/nodes/query_parser.py.

The cue-extraction tests cover only the deterministic prefix step that
runs before the Claude call. They do not invoke the LLM and have no
network or API-key dependency. The single end-to-end parse_query test
mocks ``ChatAnthropic`` so it can verify the full
"cue extracted → LLM sees cleaned query → coding_systems override
applied" path without a network round-trip.

Run with pytest from the backend/ directory:

    cd backend
    pytest tests/test_query_parser_vocab.py -v

If pytest is not available, the file also runs as a script:

    python -m tests.test_query_parser_vocab
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Allow `import app.*` whether the test is invoked from backend/ or the repo
# root. backend/ has no installable package layout, so we splice it onto
# sys.path explicitly when running as a script.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# parse_query checks ANTHROPIC_API_KEY before calling the LLM; the
# mocked client below means we never actually use the value.
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-test-key")

from app.graph.nodes.query_parser import (  # noqa: E402
    Condition,
    ParsedQuery,
    extract_vocabulary_cues,
    parse_query,
)


def test_explicit_icd10_in_parens():
    cleaned, cues = extract_vocabulary_cues("Myocardial infarction (ICD10)")
    assert cues == ["ICD10"]
    assert cleaned == "Myocardial infarction"


def test_explicit_snomed_prefix():
    cleaned, cues = extract_vocabulary_cues("SNOMED CT codes for asthma")
    assert cues == ["SNOMED"]
    assert cleaned == "codes for asthma"


def test_no_cue_returns_empty_list():
    cleaned, cues = extract_vocabulary_cues("intracranial hypertension")
    assert cues == []
    assert cleaned == "intracranial hypertension"


def test_mixed_case_and_separator():
    # Case-insensitive, dash-separator: "Atrial Fibrillation - icd-10"
    cleaned, cues = extract_vocabulary_cues("Atrial Fibrillation - icd-10")
    assert cues == ["ICD10"]
    assert cleaned == "Atrial Fibrillation"


def test_icd_10_with_space_separator():
    cleaned, cues = extract_vocabulary_cues("Heart failure ICD 10")
    assert cues == ["ICD10"]
    assert cleaned == "Heart failure"


def test_two_cues_both_extracted():
    cleaned, cues = extract_vocabulary_cues("SNOMED and ICD-10 codes for diabetes")
    assert cues == ["SNOMED", "ICD10"]
    assert "SNOMED" not in cleaned
    assert "ICD" not in cleaned
    assert "diabetes" in cleaned
    # After cue removal the query previously started with the orphaned
    # conjunction "and"; the cleanup now strips that leading word.
    assert not cleaned.lower().startswith("and ")
    assert cleaned == "codes for diabetes"


def test_opcs4_cue_extracted_and_propagated():
    # OPCS-4 is recognised by the regex AND propagated downstream so
    # retrievers/output filters can act on the constraint.
    cleaned, cues = extract_vocabulary_cues("Hip replacement OPCS-4")
    assert cues == ["OPCS4"]
    assert cleaned == "Hip replacement"


def test_empty_query():
    cleaned, cues = extract_vocabulary_cues("")
    assert cues == []
    assert cleaned == ""


def test_query_is_only_a_cue():
    cleaned, cues = extract_vocabulary_cues("ICD10")
    assert cues == ["ICD10"]
    assert cleaned == ""


# --- End-to-end parse_query (LLM mocked) -------------------------------------

def _patch_llm_returning(parsed: ParsedQuery):
    """Patch ChatAnthropic so parse_query never makes a network call.

    Returns a context manager. Inside it, ``ChatAnthropic(...)`` returns
    a mock whose ``with_structured_output(...).invoke(...)`` resolves to
    the supplied ``ParsedQuery``.
    """
    fake_client = MagicMock()
    fake_struct = MagicMock()
    fake_struct.invoke.return_value = parsed
    fake_client.with_structured_output.return_value = fake_struct
    return patch("app.graph.nodes.query_parser.ChatAnthropic", return_value=fake_client)


def test_opcs4_cue_overrides_coding_systems_end_to_end():
    # The LLM returns its default ["SNOMED", "ICD10"] for the cleaned
    # query "Hip replacement"; the OPCS-4 cue must override that to
    # ["OPCS4"] on every condition before the dict reaches the retrievers.
    fake = ParsedQuery(conditions=[
        Condition(name="Hip replacement", condition_type="primary",
                  coding_systems=["SNOMED", "ICD10"], domain="Procedure"),
    ])
    with _patch_llm_returning(fake):
        result = parse_query("Hip replacement OPCS-4")

    assert result["vocabulary_cues"] == ["OPCS4"]
    assert len(result["conditions"]) == 1
    assert result["conditions"][0]["coding_systems"] == ["OPCS4"]
    assert result["conditions"][0]["name"] == "Hip replacement"


def test_no_cue_leaves_llm_coding_systems_untouched_end_to_end():
    fake = ParsedQuery(conditions=[
        Condition(name="diabetes", condition_type="primary",
                  coding_systems=["SNOMED", "ICD10"], domain="Condition"),
    ])
    with _patch_llm_returning(fake):
        result = parse_query("diabetes")

    assert result["vocabulary_cues"] == []
    # No cue → LLM's coding_systems passes through unchanged.
    assert sorted(result["conditions"][0]["coding_systems"]) == ["ICD10", "SNOMED"]


def test_icd10_cue_overrides_multi_condition_query_end_to_end():
    # Two conditions, both should have coding_systems rewritten to ["ICD10"].
    fake = ParsedQuery(conditions=[
        Condition(name="diabetes", condition_type="primary",
                  coding_systems=["SNOMED", "ICD10"], domain="Condition"),
        Condition(name="stroke", condition_type="comorbidity",
                  coding_systems=["SNOMED"], domain="Condition"),
    ])
    with _patch_llm_returning(fake):
        result = parse_query("diabetes and stroke (ICD10)")

    assert result["vocabulary_cues"] == ["ICD10"]
    assert all(c["coding_systems"] == ["ICD10"] for c in result["conditions"])


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        else:
            passed += 1
            print(f"PASS {t.__name__}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(_run_all())

"""
Tests for the T37 "Did you mean…?" disambiguation path in
backend/app/graph/nodes/query_parser.py.

The LLM is mocked (same pattern as test_query_parser_vocab.py) so these
run offline with no network or API-key dependency. Each test pins a
user-facing property from the ticket §5 fixture table, not the
implementation: the structured-output fields plumb through parse_query,
and should_flag_disambiguation flags the right cases with the right reason.

Run from the backend/ directory:

    cd backend
    pytest tests/test_query_parser_disambiguation.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-test-key")

from app.graph.nodes.query_parser import (  # noqa: E402
    Condition,
    ParsedQuery,
    parse_query,
    should_flag_disambiguation,
)


def _condition(name: str, **overrides) -> Condition:
    fields = dict(
        name=name,
        condition_type="primary",
        coding_systems=["SNOMED", "ICD10"],
        domain="Condition",
    )
    fields.update(overrides)
    return Condition(**fields)


def _patch_llm_returning(condition: Condition):
    """Patch ChatAnthropic so parse_query returns ``condition`` without a
    network call (mirrors test_query_parser_vocab._patch_llm_returning)."""
    fake_client = MagicMock()
    fake_struct = MagicMock()
    fake_struct.invoke.return_value = ParsedQuery(conditions=[condition])
    fake_client.with_structured_output.return_value = fake_struct
    return patch("app.graph.nodes.query_parser.ChatAnthropic", return_value=fake_client)


# --- Ambiguous abbreviations: alternatives >= 2, flagged as abbreviation ----

_ABBREVIATIONS = {
    "MS": ["multiple sclerosis", "mitral stenosis"],
    "DM": ["diabetes mellitus", "dermatomyositis"],
    "CA": ["cancer", "coronary artery", "cardiac arrest"],
    "RA": ["rheumatoid arthritis", "right atrium", "refractory anaemia"],
    "MI": ["myocardial infarction", "mitral insufficiency"],
    "AS": ["aortic stenosis", "ankylosing spondylitis"],
}


@pytest.mark.parametrize("abbrev,expansions", _ABBREVIATIONS.items())
def test_ambiguous_abbreviation_produces_alternatives(abbrev, expansions):
    cond = _condition(expansions[0], alternatives=expansions, parse_confidence=0.9)
    with _patch_llm_returning(cond):
        result = parse_query(abbrev)

    assert len(result["conditions"][0]["alternatives"]) >= 2
    suggestions = result["disambiguation_suggestions"]
    assert suggestions and suggestions[0]["reason"] == "ambiguous_abbreviation"


# --- Misspellings: corrected term appears in alternatives -------------------

_MISSPELLINGS = {
    "diabetis": "diabetes mellitus",
    "hypertenion": "hypertension",
    "asma": "asthma",
    "parkinsosn": "Parkinson's disease",
}


@pytest.mark.parametrize("typo,corrected", _MISSPELLINGS.items())
def test_misspelling_suggests_corrected_term(typo, corrected):
    cond = _condition(corrected, alternatives=[corrected], parse_confidence=0.95)
    with _patch_llm_returning(cond):
        result = parse_query(typo)

    assert corrected in result["conditions"][0]["alternatives"]
    suggestions = result["disambiguation_suggestions"]
    assert suggestions and suggestions[0]["reason"] == "possible_misspelling"


# --- Non-English: detected_language != "en" ---------------------------------

_NON_ENGLISH = {
    "diabète sucré": ("fr", "diabetes mellitus"),
    "diabetes mellitus tipo 2": ("es", "type 2 diabetes mellitus"),
}


@pytest.mark.parametrize("query,lang_term", _NON_ENGLISH.items())
def test_non_english_sets_detected_language(query, lang_term):
    lang, english = lang_term
    cond = _condition(english, detected_language=lang, parse_confidence=0.9)
    with _patch_llm_returning(cond):
        result = parse_query(query)

    assert result["conditions"][0]["detected_language"] != "en"
    suggestions = result["disambiguation_suggestions"]
    assert suggestions and suggestions[0]["reason"] == "non_english_input"


# --- Vague queries: low parse_confidence ------------------------------------

_VAGUE = {
    "heart problem": ["heart failure", "ischaemic heart disease", "atrial fibrillation"],
    "kidney issue": ["chronic kidney disease", "acute kidney injury", "kidney stones"],
}


@pytest.mark.parametrize("query,alts", _VAGUE.items())
def test_vague_query_has_low_confidence(query, alts):
    cond = _condition(alts[0], alternatives=alts, parse_confidence=0.5)
    with _patch_llm_returning(cond):
        result = parse_query(query)

    assert result["conditions"][0]["parse_confidence"] < 0.75
    suggestions = result["disambiguation_suggestions"]
    assert suggestions  # vague queries are flagged


# --- Unambiguous: no alternatives, high confidence, NO banner ----------------

_UNAMBIGUOUS = [
    "Type 2 diabetes mellitus",
    "Heart failure (SNOMED)",
    "Asthma without COPD",
    "Hypertension excluding pregnancy-induced",
]


@pytest.mark.parametrize("query", _UNAMBIGUOUS)
def test_unambiguous_query_produces_no_banner(query):
    cond = _condition(query, alternatives=[], parse_confidence=0.97)
    with _patch_llm_returning(cond):
        result = parse_query(query)

    parsed = result["conditions"][0]
    assert parsed["alternatives"] == []
    assert parsed["parse_confidence"] >= 0.75
    assert result["disambiguation_suggestions"] == []
    assert should_flag_disambiguation(cond, query) is None


# --- should_flag_disambiguation returns the right reason per trigger ---------

def test_flag_reason_per_trigger_type():
    abbrev = _condition("multiple sclerosis", parse_confidence=1.0, alternatives=[])
    assert should_flag_disambiguation(abbrev, "MS")["reason"] == "ambiguous_abbreviation"

    non_en = _condition("diabetes mellitus", detected_language="fr", parse_confidence=0.9)
    assert should_flag_disambiguation(non_en, "diabète sucré")["reason"] == "non_english_input"

    low_conf = _condition("heart failure", parse_confidence=0.5, alternatives=[])
    assert should_flag_disambiguation(low_conf, "heart problem")["reason"] == "low_parse_confidence"

    misspell = _condition("diabetes mellitus", parse_confidence=0.95, alternatives=["diabetes mellitus"])
    assert should_flag_disambiguation(misspell, "diabetis")["reason"] == "possible_misspelling"


def test_lowercase_prose_token_does_not_false_flag():
    """A lower-case "as"/"mi" in prose must not trip the abbreviation backstop
    (the ticket's "no false-positive noise" acceptance criterion)."""
    cond = _condition("diabetes mellitus", parse_confidence=0.95, alternatives=[])
    assert should_flag_disambiguation(cond, "diabetes as a comorbidity") is None

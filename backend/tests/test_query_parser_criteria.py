"""
Tests for T29 inclusion/exclusion criteria in the query model.

Two surfaces are pinned here:

1. **Schema contract** (7 tests). The four natural-language patterns the
   prompt is documented to extract — "X excluding Y", "X but not Y",
   "X without Y", "X, exclude Y", "X, not Y" — all flow through the
   ``Condition.exclude_criteria`` field on ``ParsedQuery`` and surface
   intact as ``parsed_conditions[i]["exclude_criteria"]``. We mock the
   LLM with the *expected* extraction and assert the round-trip; this
   guards against regressions like dropping the field from the Pydantic
   schema, the prompt, or the per-condition ``model_dump`` flatten step.
   The actual LLM behaviour is checked by the live smoke run before
   commit (see commit-message footer) — adding live API calls to CI
   would be slow, flaky, and subject to the temp=0 1-3% flip rate
   reported in T07.

2. **Structured-override semantics** (2 tests). When
   ``request_include_criteria`` / ``request_exclude_criteria`` are
   supplied, they must overwrite the LLM-extracted criteria on every
   condition — structured input wins because it came from the explicit
   UI escape hatch.

3. **Prompt anchor** (1 test). A string-presence check against
   SYSTEM_PROMPT pins the four-pattern guidance so an unrelated edit
   that strips it does not silently regress extraction quality.

Run with pytest from the backend/ directory:

    cd backend
    pytest tests/test_query_parser_criteria.py -v

If pytest is not available, the file also runs as a script:

    python -m tests.test_query_parser_criteria
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Allow `import app.*` whether the test is invoked from backend/ or repo root.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# parse_query checks ANTHROPIC_API_KEY before calling the LLM; the
# mocked client below means we never actually use the value.
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-test-key")

from app.graph.nodes.query_parser import (  # noqa: E402
    SYSTEM_PROMPT,
    Condition,
    ParsedQuery,
    parse_query,
)


def _patch_llm_returning(parsed: ParsedQuery):
    fake_client = MagicMock()
    fake_struct = MagicMock()
    fake_struct.invoke.return_value = parsed
    fake_client.with_structured_output.return_value = fake_struct
    return patch("app.graph.nodes.query_parser.ChatAnthropic", return_value=fake_client)


def _diabetes_excluding(term: str) -> ParsedQuery:
    return ParsedQuery(conditions=[
        Condition(
            name="diabetes",
            condition_type="primary",
            coding_systems=["SNOMED", "ICD10"],
            domain="Condition",
            include_criteria=[],
            exclude_criteria=[term],
        ),
    ])


# --- 7 schema-contract tests, one per documented NL pattern -----------------

def test_pattern_excluding_X():
    fake = _diabetes_excluding("gestational")
    with _patch_llm_returning(fake):
        result = parse_query("diabetes excluding gestational")
    assert result["conditions"][0]["exclude_criteria"] == ["gestational"]
    assert result["conditions"][0]["include_criteria"] == []


def test_pattern_but_not_X():
    fake = _diabetes_excluding("gestational")
    with _patch_llm_returning(fake):
        result = parse_query("diabetes but not gestational")
    assert result["conditions"][0]["exclude_criteria"] == ["gestational"]


def test_pattern_without_X():
    fake = _diabetes_excluding("gestational")
    with _patch_llm_returning(fake):
        result = parse_query("diabetes without gestational")
    assert result["conditions"][0]["exclude_criteria"] == ["gestational"]


def test_pattern_comma_exclude_X():
    fake = _diabetes_excluding("gestational")
    with _patch_llm_returning(fake):
        result = parse_query("diabetes, exclude gestational")
    assert result["conditions"][0]["exclude_criteria"] == ["gestational"]


def test_pattern_comma_not_X():
    # The comma-then-negative pattern (refinement C in pre-flight): a
    # real-world phrasing distinct from the four above. "diabetes, not
    # gestational" must surface the same as "excluding gestational".
    fake = _diabetes_excluding("gestational")
    with _patch_llm_returning(fake):
        result = parse_query("diabetes, not gestational")
    assert result["conditions"][0]["exclude_criteria"] == ["gestational"]


def test_combined_with_comorbidities_per_condition_criteria():
    # Each condition carries its own criteria list. The pipeline does
    # not flatten across conditions, so a comorbidity may have its own
    # carve-out distinct from the primary's.
    fake = ParsedQuery(conditions=[
        Condition(name="diabetes", condition_type="primary",
                  coding_systems=["SNOMED", "ICD10"], domain="Condition",
                  include_criteria=[], exclude_criteria=["gestational"]),
        Condition(name="hypertension", condition_type="comorbidity",
                  coding_systems=["SNOMED", "ICD10"], domain="Condition",
                  include_criteria=[], exclude_criteria=["white-coat"]),
    ])
    with _patch_llm_returning(fake):
        result = parse_query(
            "diabetes excluding gestational with hypertension excluding white-coat"
        )
    by_name = {c["name"]: c for c in result["conditions"]}
    assert by_name["diabetes"]["exclude_criteria"] == ["gestational"]
    assert by_name["hypertension"]["exclude_criteria"] == ["white-coat"]


def test_empty_case_returns_empty_lists_not_none():
    # A plain query yields empty lists on every condition, never
    # ``None``. Downstream consumers (llm_reasoning._render_condition,
    # signature_hash) iterate the lists; ``None`` would TypeError.
    fake = ParsedQuery(conditions=[
        Condition(name="diabetes", condition_type="primary",
                  coding_systems=["SNOMED", "ICD10"], domain="Condition",
                  include_criteria=[], exclude_criteria=[]),
    ])
    with _patch_llm_returning(fake):
        result = parse_query("type 2 diabetes")
    cond = result["conditions"][0]
    assert cond["include_criteria"] == []
    assert cond["exclude_criteria"] == []


# --- structured-override semantics ------------------------------------------

def test_request_exclude_criteria_overrides_empty_llm_extraction():
    # Plain query, LLM returned no criteria. The request-level
    # ``request_exclude_criteria=["gestational"]`` overrides — the
    # structured input is the explicit UI escape hatch, so it wins
    # whether or not the LLM extracted anything.
    fake = ParsedQuery(conditions=[
        Condition(name="diabetes", condition_type="primary",
                  coding_systems=["SNOMED", "ICD10"], domain="Condition",
                  include_criteria=[], exclude_criteria=[]),
    ])
    with _patch_llm_returning(fake):
        result = parse_query(
            "diabetes",
            request_exclude_criteria=["gestational"],
        )
    assert result["conditions"][0]["exclude_criteria"] == ["gestational"]


def test_request_exclude_criteria_overrides_llm_extracted_criteria():
    # The LLM extracted ``["type 1"]`` from "diabetes excluding type 1",
    # but the request supplied ``["gestational"]``. Structured wins —
    # the LLM's extraction is discarded, not merged. This pins the
    # "who wins" semantics so a future maintainer doesn't quietly turn
    # this into a union and silently change behaviour.
    fake = ParsedQuery(conditions=[
        Condition(name="diabetes", condition_type="primary",
                  coding_systems=["SNOMED", "ICD10"], domain="Condition",
                  include_criteria=[], exclude_criteria=["type 1"]),
    ])
    with _patch_llm_returning(fake):
        result = parse_query(
            "diabetes excluding type 1",
            request_exclude_criteria=["gestational"],
        )
    assert result["conditions"][0]["exclude_criteria"] == ["gestational"]


# --- prompt anchor ----------------------------------------------------------

def test_system_prompt_documents_four_extraction_patterns():
    # The 4 patterns are the contract with the LLM; if a future edit
    # rewrites the prompt and drops them, behaviour silently regresses.
    # String-presence keeps the maintainer honest about touching them.
    for needle in (
        '"X excluding Y"',
        '"X but not Y"',
        '"X without Y"',
        '"X, exclude Y"',
        "exclude_criteria",
        "include_criteria",
    ):
        assert needle in SYSTEM_PROMPT, f"SYSTEM_PROMPT missing: {needle}"


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

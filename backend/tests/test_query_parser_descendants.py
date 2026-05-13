"""Extraction tests for include_descendants (T37j)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

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


def _condition(name: str, *, include_descendants: bool) -> Condition:
    return Condition(
        name=name,
        condition_type="primary",
        coding_systems=["SNOMED", "ICD10"],
        domain="Condition",
        include_criteria=[],
        exclude_criteria=[],
        include_descendants=include_descendants,
    )


# --- 6 schema-contract tests, three per direction --------------------------


def test_cue_all_forms_of_extracts_true():
    fake = ParsedQuery(conditions=[_condition("epilepsy", include_descendants=True)])
    with _patch_llm_returning(fake):
        result = parse_query("all forms of epilepsy")
    assert result["conditions"][0]["include_descendants"] is True


def test_cue_any_X_extracts_true():
    fake = ParsedQuery(conditions=[_condition("dementia", include_descendants=True)])
    with _patch_llm_returning(fake):
        result = parse_query("any type of dementia")
    assert result["conditions"][0]["include_descendants"] is True


def test_cue_including_subtypes_extracts_true():
    fake = ParsedQuery(conditions=[_condition("copd", include_descendants=True)])
    with _patch_llm_returning(fake):
        result = parse_query("COPD including subtypes")
    assert result["conditions"][0]["include_descendants"] is True


def test_cue_diagnosis_only_extracts_false():
    fake = ParsedQuery(
        conditions=[_condition("diabetes mellitus", include_descendants=False)]
    )
    with _patch_llm_returning(fake):
        result = parse_query("diabetes mellitus diagnosis only")
    assert result["conditions"][0]["include_descendants"] is False


def test_cue_excluding_complications_extracts_false():
    fake = ParsedQuery(
        conditions=[_condition("diabetes mellitus", include_descendants=False)]
    )
    with _patch_llm_returning(fake):
        result = parse_query("diabetes mellitus, excluding complications")
    assert result["conditions"][0]["include_descendants"] is False


def test_cue_specific_to_extracts_false():
    fake = ParsedQuery(
        conditions=[_condition("heart failure", include_descendants=False)]
    )
    with _patch_llm_returning(fake):
        result = parse_query("heart failure specific to HFrEF")
    assert result["conditions"][0]["include_descendants"] is False


# --- structured-override semantics ------------------------------------------


def test_request_override_true_overwrites_llm_false():
    fake = ParsedQuery(
        conditions=[_condition("epilepsy", include_descendants=False)]
    )
    with _patch_llm_returning(fake):
        result = parse_query("epilepsy", request_include_descendants=True)
    assert result["conditions"][0]["include_descendants"] is True


def test_request_override_false_overwrites_llm_true():
    fake = ParsedQuery(
        conditions=[_condition("diabetes mellitus", include_descendants=True)]
    )
    with _patch_llm_returning(fake):
        result = parse_query(
            "all forms of diabetes mellitus",
            request_include_descendants=False,
        )
    assert result["conditions"][0]["include_descendants"] is False


# --- default + prompt anchor ------------------------------------------------


def test_bare_query_defaults_to_false():
    fake = ParsedQuery(
        conditions=[_condition("asthma", include_descendants=False)]
    )
    with _patch_llm_returning(fake):
        result = parse_query("type 2 diabetes")
    assert result["conditions"][0]["include_descendants"] is False


def test_system_prompt_documents_both_cue_directions():
    for needle in (
        '"all forms of X"',
        '"any X"',
        '"including subtypes"',
        '"X diagnosis only"',
        '"X, excluding complications"',
        '"specific to X"',
        "include_descendants",
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

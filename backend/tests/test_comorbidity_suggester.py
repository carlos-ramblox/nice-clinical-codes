"""Unit tests for the comorbidity_suggester node. LLM always mocked — no network needed."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# _llm_suggestions early-returns [] when the key is unset, before the
# (mocked) ChatAnthropic is touched — set a sentinel so the live-source
# tests reach the mock.
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-test-key")

from app.graph.nodes.comorbidity_suggester import (  # noqa: E402
    MAX_SUGGESTIONS,
    _extract_anchor,
    _merge_and_rank,
    _tier,
    suggest_comorbidities,
)


# --- helpers ---

def _code(term: str, decision: str = "include") -> dict:
    return {"code": "x", "term": term, "vocabulary": "SNOMED CT", "decision": decision}


def _cond(name: str, condition_type: str = "primary") -> dict:
    return {"name": name, "condition_type": condition_type}


def _hint(name: str, conf: float, by: list[str], cui: str | None = None) -> dict:
    h = {"condition_name": name, "rationale": "r", "confidence": conf, "suggested_by": by}
    if cui is not None:
        h["cui"] = cui
    return h


def _patch_llm(suggestions: list[dict] | None = None, *, raises: Exception | None = None):
    fake_struct = MagicMock()
    if raises is not None:
        fake_struct.ainvoke = AsyncMock(side_effect=raises)
    else:
        result = SimpleNamespace(suggestions=[
            SimpleNamespace(
                condition_name=s["condition_name"],
                rationale=s.get("rationale", "r"),
                confidence=s["confidence"],
            )
            for s in (suggestions or [])
        ])
        fake_struct.ainvoke = AsyncMock(return_value=result)
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value = fake_struct
    return patch(
        "app.graph.nodes.comorbidity_suggester.ChatAnthropic",
        return_value=fake_llm,
    ), fake_llm


# --- anchor selection ---

def test_anchor_keeps_primary_only_and_included_only():
    final = [_code("Congestive heart failure", "include"),
             _code("Some excluded term", "exclude")]
    parsed = [_cond("Heart failure", "primary"),
              _cond("Type 2 diabetes mellitus", "comorbidity")]
    anchor = _extract_anchor(final, parsed)
    assert anchor["primary_names"] == ["Heart failure"]      # comorbidity dropped
    assert anchor["included_terms"] == ["Congestive heart failure"]  # excluded dropped


# --- node-level contracts ---

def test_no_inclusions_short_circuits_to_empty():
    state = {"final_code_list": [_code("x", "exclude")],
             "parsed_conditions": [_cond("Heart failure")]}
    out = asyncio.run(suggest_comorbidities(state))
    assert out == {"comorbidity_suggestions": []}


def test_llm_failure_degrades_to_empty_without_raising():
    state = {"final_code_list": [_code("Congestive heart failure")],
             "parsed_conditions": [_cond("Heart failure")]}
    p, _ = _patch_llm(raises=RuntimeError("simulated Anthropic outage"))
    with p:
        out = asyncio.run(suggest_comorbidities(state))
    assert out == {"comorbidity_suggestions": []}


def test_llm_source_maps_and_tags_suggested_by_llm():
    state = {"final_code_list": [_code("Congestive heart failure")],
             "parsed_conditions": [_cond("Heart failure")]}
    p, _ = _patch_llm([
        {"condition_name": "Hypertension", "confidence": 0.95},
        {"condition_name": "Atrial fibrillation", "confidence": 0.9},
    ])
    with p:
        out = asyncio.run(suggest_comorbidities(state))
    sugg = out["comorbidity_suggestions"]
    assert [s["condition_name"] for s in sugg] == ["Hypertension", "Atrial fibrillation"]
    assert all(s["suggested_by"] == ["LLM"] for s in sugg)


def test_no_primary_condition_skips_llm():
    """Anchor with no primary name → LLM source never invoked → []."""
    state = {"final_code_list": [_code("Congestive heart failure")],
             "parsed_conditions": [_cond("Type 2 diabetes", "comorbidity")]}
    p, fake_llm = _patch_llm([{"condition_name": "X", "confidence": 0.9}])
    with p:
        out = asyncio.run(suggest_comorbidities(state))
    assert out == {"comorbidity_suggestions": []}
    fake_llm.with_structured_output.assert_not_called()


def test_temperature_zero_determinism_contract():
    """Determinism half 1: the LLM is constructed with temperature=0."""
    state = {"final_code_list": [_code("Congestive heart failure")],
             "parsed_conditions": [_cond("Heart failure")]}
    p, _ = _patch_llm([{"condition_name": "Hypertension", "confidence": 0.95}])
    with p as mock_cls:
        asyncio.run(suggest_comorbidities(state))
    assert mock_cls.call_args.kwargs["temperature"] == 0


# --- merge / rank (no LLM needed) ---

def test_dedup_against_parsed_conditions():
    parsed = [_cond("Heart failure"), _cond("Type 2 diabetes mellitus", "comorbidity")]
    sources = [[
        _hint("Type 2 diabetes mellitus", 0.95, ["LLM"]),  # already searched → dropped
        _hint("Atrial fibrillation", 0.9, ["LLM"]),
    ]]
    out = _merge_and_rank(sources, parsed)
    assert [h["condition_name"] for h in out] == ["Atrial fibrillation"]


def test_cross_source_union_and_tier_ordering():
    parsed = [_cond("Heart failure")]
    llm = [_hint("Atrial fibrillation", 0.9, ["LLM"]),
           _hint("Chronic kidney disease", 0.7, ["LLM"])]
    medgen = [_hint("atrial  fibrillation", 0.6, ["MedGen"]),  # norm-key merges w/ AF
              _hint("Anaemia", 0.5, ["MedGen"])]
    out = _merge_and_rank([llm, medgen], parsed)
    # AF agreed by both → tier 0 first, with union provenance + max confidence
    assert out[0]["condition_name"] == "Atrial fibrillation"
    assert set(out[0]["suggested_by"]) == {"LLM", "MedGen"}
    assert out[0]["confidence"] == 0.9
    # then MedGen-only (Anaemia, tier 1) before LLM-only (CKD, tier 2),
    # even though CKD has the higher confidence — tier beats confidence.
    assert [h["condition_name"] for h in out[1:]] == ["Anaemia", "Chronic kidney disease"]
    assert _tier(out[1]) == 1 and _tier(out[2]) == 2


def test_cap_at_max_suggestions():
    parsed = [_cond("Heart failure")]
    many = [_hint(f"Condition {i}", 0.9 - i * 0.01, ["LLM"]) for i in range(MAX_SUGGESTIONS + 5)]
    out = _merge_and_rank([many], parsed)
    assert len(out) == MAX_SUGGESTIONS


def test_stable_sort_determinism():
    """Determinism half 2: equal tier+confidence keep insertion order, and
    repeated calls produce identical output."""
    parsed = [_cond("Heart failure")]
    src = [[_hint("Alpha", 0.8, ["LLM"]),
            _hint("Bravo", 0.8, ["LLM"]),
            _hint("Charlie", 0.8, ["LLM"])]]
    first = [h["condition_name"] for h in _merge_and_rank(src, parsed)]
    second = [h["condition_name"] for h in _merge_and_rank(src, parsed)]
    assert first == ["Alpha", "Bravo", "Charlie"] == second


def test_cui_dedup_key_collapses_differing_names():
    """When two sources share a CUI, they merge even if the display name
    differs (the resolved-key path P2 relies on)."""
    parsed = [_cond("Heart failure")]
    sources = [
        [_hint("Atrial fibrillation", 0.9, ["LLM"], cui="C0004238")],
        [_hint("AF", 0.6, ["MedGen"], cui="C0004238")],
    ]
    out = _merge_and_rank(sources, parsed)
    assert len(out) == 1
    assert set(out[0]["suggested_by"]) == {"LLM", "MedGen"}


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

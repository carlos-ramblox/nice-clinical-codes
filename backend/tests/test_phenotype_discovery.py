"""
Tests for the HDR UK phenotype-discovery service
(``backend/app/services/phenotype_discovery.py``).

Six tests transferred from the deleted ``test_hdruk_retriever.py``
(T36 reframe): the judge / fallthrough / prompt-formatter / drug-vs-
condition tests still apply; the canonical-vocab filter and retriever-
wiring tests do not (they tested code that was removed in T36).

Run with pytest from backend/, or as a script:

    python -m tests.test_phenotype_discovery
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# Allow `import app.*` whether the test is invoked from backend/ or repo root.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import phenotype_discovery as pd  # noqa: E402


# --- Fixtures --------------------------------------------------------------

def _phenotype(pid, name, **extra):
    """Phenotype dict shaped like the HDR UK search response."""
    return {
        "phenotype_id": pid,
        "name": name,
        "type": [{"name": "Disease or syndrome"}],
        "coding_system": [{"name": "SNOMED CT"}],
        "data_sources": [{"name": "CPRD GOLD"}],
        "publications": [],
        **extra,
    }


class _FakeStructuredLLM:
    """Stand-in for ``ChatAnthropic.with_structured_output(...)``."""
    def __init__(self, decisions_by_id, raise_on_invoke=None):
        self._decisions_by_id = decisions_by_id
        self._raise = raise_on_invoke

    def invoke(self, _messages):
        if self._raise is not None:
            raise self._raise
        return pd._PhenotypeRelevanceBatch(decisions=[
            pd._PhenotypeRelevance(phenotype_id=pid, relevant=v["relevant"], reason=v["reason"])
            for pid, v in self._decisions_by_id.items()
        ])


class _FakeChatAnthropic:
    """Stand-in for ``ChatAnthropic`` so we don't open a real LLM connection."""
    def __init__(self, structured_llm):
        self._structured_llm = structured_llm

    def with_structured_output(self, _schema):
        return self._structured_llm


def _patch_judge_llm(structured_llm):
    return patch.object(
        pd,
        "ChatAnthropic",
        lambda *_a, **_kw: _FakeChatAnthropic(structured_llm),
    )


# --- Judge behaviour -------------------------------------------------------

def test_judge_drops_phenotypes_marked_irrelevant():
    phenos = [
        _phenotype("PH12", "Asthma"),
        _phenotype("PH1690", "Chronic paediatric conditions: Asthma"),
        _phenotype("PH335", "Viral diseases (excl chronic hepatitis/HIV)"),
    ]
    decisions = {
        "PH12":   {"relevant": True,  "reason": "matches"},
        "PH1690": {"relevant": False, "reason": "paediatric scope, query is adult"},
        "PH335":  {"relevant": False, "reason": "explicitly excludes the query target"},
    }
    fake = _FakeStructuredLLM(decisions)
    with _patch_judge_llm(fake), \
         patch.object(pd, "HDR_UK_USE_JUDGE", True), \
         patch.object(pd, "ANTHROPIC_API_KEY", "dummy"):
        kept = pd.judge_phenotype_relevance("HIV", phenos)
    assert [p["phenotype_id"] for p in kept] == ["PH12"]


def test_judge_disabled_returns_input_unchanged():
    phenos = [_phenotype("PH12", "Asthma"), _phenotype("PH99", "Anything")]
    with patch.object(pd, "HDR_UK_USE_JUDGE", False):
        out = pd.judge_phenotype_relevance("asthma", phenos)
    assert out is phenos  # same object, no LLM consulted


def test_judge_with_no_api_key_passes_through():
    phenos = [_phenotype("PH12", "Asthma")]
    with patch.object(pd, "HDR_UK_USE_JUDGE", True), \
         patch.object(pd, "ANTHROPIC_API_KEY", ""):
        out = pd.judge_phenotype_relevance("asthma", phenos)
    assert out == phenos


def test_judge_llm_failure_falls_through_to_input():
    # Network blip / parse failure must not sink the discovery endpoint --
    # better to over-include than to admit a black-hole HDR UK contribution.
    phenos = [_phenotype("PH12", "Asthma"), _phenotype("PH99", "Anything")]
    fake = _FakeStructuredLLM({}, raise_on_invoke=RuntimeError("boom"))
    with _patch_judge_llm(fake), \
         patch.object(pd, "HDR_UK_USE_JUDGE", True), \
         patch.object(pd, "ANTHROPIC_API_KEY", "dummy"):
        out = pd.judge_phenotype_relevance("asthma", phenos)
    assert out == phenos


def test_judge_prompt_includes_phenotype_type_and_metadata():
    # The type field is what lets the LLM distinguish Drug phenotypes
    # from Disease phenotypes, etc. -- losing it would silently break the
    # scope-fit guidance in the system prompt. Asserting it round-trips
    # through the formatter pins the prompt-plumbing path so a future
    # refactor cannot silently drop the signal.
    p_drug = _phenotype(
        "PH99", "Statin therapy",
        type=[{"name": "Drug or therapy"}],
        coding_system=[{"name": "BNF codes"}],
        data_sources=[{"name": "CPRD prescription data"}],
        publications=[{"details": "BNF prescription codes used in lipid-lowering trials"}],
    )
    block = pd._format_phenotype_for_judge(p_drug)
    assert "PH99" in block
    assert "Statin therapy" in block
    assert "type: Drug or therapy" in block
    assert "coding_systems: BNF codes" in block
    assert "data_sources: CPRD prescription data" in block
    assert "first_publication: BNF prescription codes" in block


def test_judge_drops_drug_phenotype_when_query_is_a_condition():
    # End-to-end check that when the LLM (mocked here) marks a Drug
    # phenotype as irrelevant for a condition query, it gets dropped while
    # the Disease phenotype passes. Mirrors the "Beware drug/treatment
    # phenotypes when the query is for a condition" rule in the system
    # prompt; the mock encodes the LLM's expected behaviour rather than
    # exercising the live model, so this test is deterministic and offline.
    phenos = [
        _phenotype("PH50", "Type 2 diabetes mellitus",
                   type=[{"name": "Disease or syndrome"}]),
        _phenotype("PH99", "Statin therapy",
                   type=[{"name": "Drug or therapy"}]),
    ]
    decisions = {
        "PH50": {"relevant": True,  "reason": "matches condition query"},
        "PH99": {"relevant": False, "reason": "drug therapy phenotype, query asks for the condition"},
    }
    fake = _FakeStructuredLLM(decisions)
    with _patch_judge_llm(fake), \
         patch.object(pd, "HDR_UK_USE_JUDGE", True), \
         patch.object(pd, "ANTHROPIC_API_KEY", "dummy"):
        kept = pd.judge_phenotype_relevance("type 2 diabetes", phenos)
    assert [p["phenotype_id"] for p in kept] == ["PH50"]


# --- Runner ----------------------------------------------------------------

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

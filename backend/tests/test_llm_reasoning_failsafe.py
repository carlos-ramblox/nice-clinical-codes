"""
Pin the fail-toward-review safety contract in score_codes.

CLINICAL_SAFETY.md commits to: a code never silently leaves the system
without explicit reviewer action. score_codes implements the upstream
half of that promise — when the LLM call fails for any reason, every
code in the batch is returned as ``decision="uncertain"`` with
confidence 0.0 and a rationale that names the error. The reviewer
then sees the whole batch as needing adjudication rather than getting
phantom include/exclude decisions.

This test pins the behaviour so a refactor that turns the broad
``except Exception`` in llm_reasoning._score_batch into a re-raise
(the simplest sloppy edit) is caught by CI before it ships.

Run from backend/:
    pytest tests/test_llm_reasoning_failsafe.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# score_codes raises if ANTHROPIC_API_KEY is unset, before it ever
# touches the (mocked) LLM. The patched ChatAnthropic below means the
# value here is just a sentinel.
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-test-key")

from app.graph.nodes.llm_reasoning import score_codes  # noqa: E402


def _code(code: str, vocab: str = "SNOMED CT", term: str = "x") -> dict:
    return {
        "code": code,
        "term": term,
        "vocabulary": vocab,
        "source": "OMOPHub",
        "sources": ["OMOPHub"],
        "source_count": 1,
        "domain": "Condition",
        "similarity_score": None,
        "usage_frequency": None,
    }


def _patch_llm_raising(exc: Exception):
    """Patch ChatAnthropic so the structured-output invoke raises exc."""
    fake_llm = MagicMock()
    fake_struct = MagicMock()
    fake_struct.invoke.side_effect = exc
    fake_llm.with_structured_output.return_value = fake_struct
    return patch("app.graph.nodes.llm_reasoning.ChatAnthropic", return_value=fake_llm)


def test_llm_exception_returns_all_uncertain():
    """An LLM-side exception must yield uncertain/0.0 for every code in
    the batch. No code may slip through with an include/exclude
    decision under failure."""
    state = {
        "enriched_codes": [_code("A"), _code("B")],
        "parsed_conditions": [{"name": "test", "condition_type": "primary"}],
    }
    with _patch_llm_raising(RuntimeError("simulated Anthropic outage")):
        out = score_codes(state)

    scored = out["scored_codes"]
    assert len(scored) == 2
    for s in scored:
        assert s["decision"] == "uncertain"
        assert s["confidence"] == 0.0
        assert s["rationale"].startswith("LLM error:")
    # ambiguous_codes should mirror scored when everything is uncertain
    assert len(out["ambiguous_codes"]) == 2


def test_llm_exception_preserves_code_identity_and_order():
    """The fallback must attach each uncertain decision to the right
    code. score_codes pre-sorts by (vocabulary, code), so the failure
    output should follow that order."""
    state = {
        "enriched_codes": [_code("Z"), _code("A")],
        "parsed_conditions": [{"name": "test", "condition_type": "primary"}],
    }
    with _patch_llm_raising(ValueError("structured-output parse failed")):
        out = score_codes(state)

    assert [s["code"] for s in out["scored_codes"]] == ["A", "Z"]


def test_llm_exception_carries_through_multi_batch():
    """BATCH_SIZE = 40 in llm_reasoning. With 50 codes the failure
    path must fire on every batch and still cover all 50 codes."""
    codes = [_code(f"C{i:03d}") for i in range(50)]
    state = {
        "enriched_codes": codes,
        "parsed_conditions": [{"name": "test", "condition_type": "primary"}],
    }
    with _patch_llm_raising(RuntimeError("rate limited")):
        out = score_codes(state)

    assert len(out["scored_codes"]) == 50
    assert all(s["decision"] == "uncertain" for s in out["scored_codes"])


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

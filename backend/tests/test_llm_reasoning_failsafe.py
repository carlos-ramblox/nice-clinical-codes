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

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# score_codes raises if ANTHROPIC_API_KEY is unset, before it ever
# touches the (mocked) LLM. The patched ChatAnthropic below means the
# value here is just a sentinel.
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-test-key")

from app.graph.nodes.llm_reasoning import BATCH_SIZE, score_codes  # noqa: E402


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


def _patch_llm_ainvoke(side_effect):
    """Patch ChatAnthropic so the structured-output ainvoke is driven by
    ``side_effect`` (an Exception, a callable, or a list of return values
    consumed in order — see ``AsyncMock.side_effect`` semantics)."""
    fake_llm = MagicMock()
    fake_struct = MagicMock()
    fake_struct.ainvoke = AsyncMock(side_effect=side_effect)
    fake_llm.with_structured_output.return_value = fake_struct
    return patch("app.graph.nodes.llm_reasoning.ChatAnthropic", return_value=fake_llm)


def _patch_llm_raising(exc: Exception):
    """Convenience for the all-batches-fail tests."""
    return _patch_llm_ainvoke(exc)


def test_llm_exception_returns_all_uncertain():
    """An LLM-side exception must yield uncertain/0.0 for every code in
    the batch. No code may slip through with an include/exclude
    decision under failure."""
    state = {
        "enriched_codes": [_code("A"), _code("B")],
        "parsed_conditions": [{"name": "test", "condition_type": "primary"}],
    }
    with _patch_llm_raising(RuntimeError("simulated Anthropic outage")):
        out = asyncio.run(score_codes(state))

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
        out = asyncio.run(score_codes(state))

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
        out = asyncio.run(score_codes(state))

    assert len(out["scored_codes"]) == 50
    assert all(s["decision"] == "uncertain" for s in out["scored_codes"])


def test_gather_isolates_one_failing_batch():
    """asyncio.gather over per-batch coroutines must keep good batches
    intact when one batch raises. With three batches and the middle one
    raising, the failing batch's codes go to uncertain (rationale begins
    with "LLM error:") while the other two batches surface real LLM
    decisions. This is the parallelism-correctness contract."""
    # Three full batches of distinct codes — sort key is (vocab, code),
    # so the lexicographic prefixes A/B/C below pin which batch each
    # code lands in after score_codes' deterministic sort.
    codes = (
        [_code(f"A{i:03d}") for i in range(BATCH_SIZE)]
        + [_code(f"B{i:03d}") for i in range(BATCH_SIZE)]
        + [_code(f"C{i:03d}") for i in range(BATCH_SIZE)]
    )

    def _decisions_for(prefix: str):
        # Mock BatchDecisions object: only `.decisions` is read, and
        # each decision needs `.model_dump()`.
        return MagicMock(decisions=[
            MagicMock(model_dump=lambda code=f"{prefix}{i:03d}": {
                "code": code, "decision": "include", "confidence": 0.9,
                "rationale": "ok",
            })
            for i in range(BATCH_SIZE)
        ])

    # Side-effect order matches batch order (A, B, C) since gather
    # schedules in argument order. Middle batch raises.
    side_effects = [
        _decisions_for("A"),
        RuntimeError("transient 529 on batch B"),
        _decisions_for("C"),
    ]

    state = {
        "enriched_codes": codes,
        "parsed_conditions": [{"name": "test", "condition_type": "primary"}],
    }
    with _patch_llm_ainvoke(side_effects):
        out = asyncio.run(score_codes(state))

    scored = out["scored_codes"]
    assert len(scored) == 3 * BATCH_SIZE

    by_prefix = {"A": [], "B": [], "C": []}
    for s in scored:
        by_prefix[s["code"][0]].append(s)

    # A and C: untouched LLM decisions (include / 0.9)
    for prefix in ("A", "C"):
        assert all(s["decision"] == "include" for s in by_prefix[prefix])
        assert all(s["confidence"] == 0.9 for s in by_prefix[prefix])

    # B: every code uncertain with LLM-error rationale
    assert all(s["decision"] == "uncertain" for s in by_prefix["B"])
    assert all(s["confidence"] == 0.0 for s in by_prefix["B"])
    assert all(s["rationale"].startswith("LLM error:") for s in by_prefix["B"])


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

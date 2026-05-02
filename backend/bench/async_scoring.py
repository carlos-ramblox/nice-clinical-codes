"""One-off wall-clock benchmark for the async-scoring refactor (T04).

Runs _score_batch over 100 realistic SNOMED candidate codes from the
diabetes benchmark fixture, once with asyncio.gather (parallel — what
ships) and once with sequential awaits (what the for-loop did before).
Prints both wall-clock numbers and the speedup, then a determinism
check (per-code decision-label equivalence between the two runs) so
the acceptance criterion "identical to sequential within Haiku temp=0
noise" is verifiable from the same run.

Note on layering: this script imports ``_score_batch`` (single-leading-
underscore private) directly. That is intentional — the benchmark's
job is to time the exact unit the production ``score_codes`` gathers,
and going through ``score_codes`` would mix in the full state-dict
plumbing (sort, fallback padding, summary log) and the LLM client
construction we want held constant. If ``_score_batch`` is renamed,
this file is the right place for the build to break loudly.

Run from backend/:
    python -m bench.async_scoring
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from langchain_anthropic import ChatAnthropic

from app.config import ANTHROPIC_API_KEY, LLM_SCORING_MODEL
from app.graph.nodes.llm_reasoning import BATCH_SIZE, BatchDecisions, _score_batch

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "data" / "test_sets" / "benchmark_2026_04" / "diabetes_mellitus.result_postfix.json"


def _load_codes() -> list[dict]:
    with open(FIXTURE, encoding="utf-8") as f:
        data = json.load(f)
    return [
        {
            "code": c["code"],
            "term": c["term"],
            "vocabulary": c["vocabulary"],
            "sources": c.get("sources", []),
            "source_count": c.get("source_count") or len(c.get("sources", [])) or 1,
        }
        for c in data["scored_codes"]
    ]


def _build_structured_llm():
    llm = ChatAnthropic(
        model=LLM_SCORING_MODEL,
        api_key=ANTHROPIC_API_KEY,
        max_tokens=4096,
        temperature=0,
    )
    return llm.with_structured_output(BatchDecisions)


async def _parallel(structured_llm, conditions, batches) -> list[list[dict]]:
    return await asyncio.gather(
        *[_score_batch(structured_llm, conditions, b) for b in batches]
    )


async def _sequential(structured_llm, conditions, batches) -> list[list[dict]]:
    out = []
    for b in batches:
        out.append(await _score_batch(structured_llm, conditions, b))
    return out


def _flat(batched: list[list[dict]]) -> list[dict]:
    return [d for batch in batched for d in batch]


def _decision_match(seq_out: list[list[dict]], par_out: list[list[dict]]) -> tuple[int, int, list[str]]:
    """Compare decision labels (and codes) per position. Confidence and
    rationale text are excluded — Haiku at temp=0 is mostly but not
    bitwise deterministic for free-text fields, and the safety contract
    only pins the label."""
    seq, par = _flat(seq_out), _flat(par_out)
    n = min(len(seq), len(par))
    diffs: list[str] = []
    if len(seq) != len(par):
        diffs.append(f"length mismatch: seq={len(seq)} par={len(par)}")
    matches = 0
    for s, p in zip(seq, par):
        if s["code"] != p["code"]:
            diffs.append(f"code order: {s['code']} vs {p['code']}")
        elif s["decision"] != p["decision"]:
            diffs.append(f"{s['code']}: seq={s['decision']} par={p['decision']}")
        else:
            matches += 1
    return matches, n, diffs


def main() -> None:
    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    codes = _load_codes()
    codes = sorted(codes, key=lambda c: (c.get("vocabulary", ""), c.get("code", "")))
    batches = [codes[i:i + BATCH_SIZE] for i in range(0, len(codes), BATCH_SIZE)]
    conditions = [{"name": "type 2 diabetes", "condition_type": "primary"}]

    print(f"n_codes={len(codes)}  batch_size={BATCH_SIZE}  n_batches={len(batches)}")
    print(f"model={LLM_SCORING_MODEL}")
    print()

    structured_llm = _build_structured_llm()

    # Warm-up: the first call sometimes carries TLS/handshake overhead
    # that would skew the smaller of the two timings. One throwaway call
    # against a small batch evens that out.
    print("warm-up...")
    asyncio.run(_score_batch(structured_llm, conditions, codes[:3]))

    print("sequential...")
    t0 = time.perf_counter()
    seq_out = asyncio.run(_sequential(structured_llm, conditions, batches))
    t_seq = time.perf_counter() - t0
    seq_n = sum(len(b) for b in seq_out)
    print(f"  -> {t_seq:.2f}s   ({seq_n} decisions)")

    print("parallel...")
    t0 = time.perf_counter()
    par_out = asyncio.run(_parallel(structured_llm, conditions, batches))
    t_par = time.perf_counter() - t0
    par_n = sum(len(b) for b in par_out)
    print(f"  -> {t_par:.2f}s   ({par_n} decisions)")

    print()
    print(f"speedup: {t_seq / t_par:.2f}x   (sequential {t_seq:.2f}s -> parallel {t_par:.2f}s)")

    matches, n, diffs = _decision_match(seq_out, par_out)
    print(f"determinism: {matches}/{n} decisions match (label-level, ignoring rationale)")
    if diffs:
        print("  diffs:")
        for d in diffs[:10]:
            print(f"    - {d}")
        if len(diffs) > 10:
            print(f"    ... and {len(diffs) - 10} more")


if __name__ == "__main__":
    main()

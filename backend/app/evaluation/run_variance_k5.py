"""K-runs variance benchmark runner (T07).

Runs the production pipeline K times per benchmark codelist and persists
each run as ``<short>.result_runK_<k>.json`` (k = 1..K). Sibling to
``run_ablation.py``; uses the same in-process ``run_pipeline`` invocation
the ``/api/evaluate`` route uses, so the result envelope is identical to
the existing ``<short>.result_postfix.json`` files (same ``stages``
shape, same ``scored_codes`` payload). The aggregator
(``benchmark_aggregate.py``) then reads either family transparently.

Why in-process rather than POSTing to the live API:

* No network hop, no live-API dependency, fully resumable.
* The F1-relevant code path on ``develop`` is unchanged from the
  ``ad7ccad-dirty`` deployed image that produced the existing
  ``result_postfix.json`` files (T03 was reporting-only, T04 was
  bit-equivalent async parallelism, T05 is passive observability,
  T06 only reorders the HITL queue). Drift between the K=5 mean F1
  and the persisted single-run Post-fix column is therefore
  attributable to run-to-run variance, not code drift; that variance
  is what this benchmark measures.

Resumability
------------
Each ``(codelist, k)`` pair is written to its own JSON file as soon as
the pipeline returns. On re-run, any pair whose file already exists is
skipped. So an aborted run can be resumed by re-invoking the same
command — useful given the wall-clock is ~30 s × 15 lists × 5 runs.

Cost guard
----------
A best-effort USD estimator (``--cap-usd``, default 20) sums an a-priori
per-batch cost from the post-LLM scored-code count and aborts the loop
before starting the next codelist if the running estimate exceeds the
cap. This is a soft guard, not a hard kill mid-codelist.

Usage::

    python -m app.evaluation.run_variance_k5
    python -m app.evaluation.run_variance_k5 --runs 5 --cap-usd 20
    python -m app.evaluation.run_variance_k5 --codelists heart_failure,diabetes_mellitus
    python -m app.evaluation.run_variance_k5 --pause-after-first
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from app.evaluation.evaluator import run_evaluation
from app.graph.graph import run_pipeline
from app.graph.nodes.llm_reasoning import BATCH_SIZE

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"

# Soft cost model. Haiku 4.5 pricing per 1k tokens at time of writing:
# ~$0.0008 input, ~$0.004 output. Per scoring batch we send the system
# prompt (~1.2k tokens) plus ~40 codes' descriptions (~1k tokens) and
# receive ~40 structured decisions (~1k tokens). Round to ~$0.001/batch
# on Haiku as a deliberately-conservative single-number estimate.
USD_PER_SCORING_BATCH = 0.001
# Each pipeline run also makes one Sonnet 4 query-parsing call. Sonnet
# input ~$0.003/1k, output ~$0.015/1k; the parser prompt is ~200 tokens
# in, ~150 tokens out -> ~$0.003 per call. Negligible at K=5×15 calls
# (~$0.23) but included for honesty.
USD_PER_QUERY_PARSE = 0.003


def _result_path(short: str, k: int) -> Path:
    return BENCH / f"{short}.result_runK_{k}.json"


def _build_result_envelope(test_set: list[dict], pipeline_result: dict, elapsed: float) -> dict:
    """Mirror the envelope ``/api/evaluate`` writes, so the file shape
    matches the existing ``<short>.result_postfix.json`` files exactly."""
    final_codes = pipeline_result.get("final_code_list", [])
    retrieved_codes = pipeline_result.get("retrieved_codes", [])
    enriched_codes = pipeline_result.get("enriched_codes", [])
    eval_result = run_evaluation(test_set, {
        "results": final_codes,
        "retrieved_codes": retrieved_codes,
        "enriched_codes": enriched_codes,
    })
    eval_result["elapsed_seconds"] = round(elapsed, 2)
    eval_result["pipeline_results_count"] = len(final_codes)
    eval_result["scored_codes"] = final_codes
    eval_result["pipeline"] = "rag"
    eval_result["cold_start"] = False
    return eval_result


def _estimate_run_cost(scored_count: int) -> float:
    n_batches = max(1, (scored_count + BATCH_SIZE - 1) // BATCH_SIZE)
    return USD_PER_QUERY_PARSE + n_batches * USD_PER_SCORING_BATCH


async def _one_run(short: str, k: int, query: str, test_set: list[dict]) -> tuple[dict, float]:
    """Run the pipeline once and return (envelope, elapsed_seconds)."""
    t0 = time.perf_counter()
    pipeline_result = await run_pipeline(query, disabled_retrievers=None)
    elapsed = time.perf_counter() - t0
    envelope = _build_result_envelope(test_set, pipeline_result, elapsed)
    return envelope, elapsed


async def run(
    runs: int = 5,
    cap_usd: float = 20.0,
    codelists: list[str] | None = None,
    pause_after_first: bool = False,
) -> None:
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)

    if codelists:
        wanted = set(codelists)
        selection = [s for s in selection if s["short"] in wanted]
        missing = wanted - {s["short"] for s in selection}
        if missing:
            raise SystemExit(f"Unknown codelist short(s): {sorted(missing)}")

    total_pairs = sum(
        1
        for s in selection
        for k in range(1, runs + 1)
        if not _result_path(s["short"], k).exists()
    )
    already_done = len(selection) * runs - total_pairs
    logger.info(
        "Plan: %d codelists × %d runs = %d pairs (%d already on disk, %d to run). Cap: $%.2f",
        len(selection), runs, len(selection) * runs, already_done, total_pairs, cap_usd,
    )

    spent_usd = 0.0
    pair_idx = 0
    sweep_t0 = time.perf_counter()
    paused_already = False

    for s in selection:
        short = s["short"]
        with open(BENCH / f"{short}.json", encoding="utf-8") as f:
            test_set = json.load(f)
        query = test_set[0].get("Research_question", "")

        for k in range(1, runs + 1):
            out_path = _result_path(short, k)
            if out_path.exists():
                logger.info("[%s run %d] already on disk, skipping", short, k)
                continue

            if spent_usd >= cap_usd:
                logger.warning(
                    "USD cap $%.2f reached (estimated spent $%.2f). Aborting before %s run %d.",
                    cap_usd, spent_usd, short, k,
                )
                return

            pair_idx += 1
            logger.info("[%s run %d] starting (pair %d/%d, est-spent $%.2f)", short, k, pair_idx, total_pairs, spent_usd)

            envelope, elapsed = await _one_run(short, k, query, test_set)
            out_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")

            run_cost = _estimate_run_cost(envelope["pipeline_results_count"])
            spent_usd += run_cost

            inc = envelope["stages"].get("included_only", {})
            logger.info(
                "[%s run %d] done in %.1fs  scored=%d  P=%.3f R=%.3f F1=%.3f  est-cost $%.4f  -> %s",
                short, k, elapsed, envelope["pipeline_results_count"],
                inc.get("precision", 0.0), inc.get("recall", 0.0), inc.get("f1", 0.0),
                run_cost, out_path.name,
            )

            if pause_after_first and not paused_already:
                paused_already = True
                sweep_elapsed = time.perf_counter() - sweep_t0
                projected_total = sweep_elapsed * total_pairs
                projected_cost = run_cost * total_pairs
                print()
                print("=" * 72)
                print("PAUSE-AFTER-FIRST sanity check")
                print(f"  first pair wall-clock: {sweep_elapsed:.1f}s")
                print(f"  projected total wall-clock for {total_pairs} pairs: "
                      f"{projected_total / 60:.1f} min")
                print(f"  projected total estimated cost: ${projected_cost:.2f} "
                      f"(cap ${cap_usd:.2f})")
                print("Re-run without --pause-after-first to continue the sweep.")
                print("=" * 72)
                return

    sweep_elapsed = time.perf_counter() - sweep_t0
    logger.info(
        "Sweep complete in %.1fs (%.1f min). Estimated cost: $%.2f. Cap was $%.2f.",
        sweep_elapsed, sweep_elapsed / 60, spent_usd, cap_usd,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5, help="Runs per codelist (default: 5)")
    parser.add_argument("--cap-usd", type=float, default=20.0, help="Soft USD cost cap (default: 20)")
    parser.add_argument("--codelists", type=str, default=None,
                        help="Comma-separated list of codelist short names to run "
                             "(default: all 15 from selection.json)")
    parser.add_argument("--pause-after-first", action="store_true",
                        help="Stop after the first pair, print wall-clock + cost projection, exit. "
                             "Used to sanity-check latency before committing to the full sweep.")
    args = parser.parse_args()

    codelists = [c.strip() for c in args.codelists.split(",")] if args.codelists else None
    asyncio.run(run(
        runs=args.runs,
        cap_usd=args.cap_usd,
        codelists=codelists,
        pause_after_first=args.pause_after_first,
    ))


if __name__ == "__main__":
    main()

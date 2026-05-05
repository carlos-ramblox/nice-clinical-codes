"""T29 cost-trimmed benchmark runner.

Two distinct claims to verify, run as a single script for ergonomics:

1. **Empty-criteria F1 invariance.** With ``include_criteria=[]`` and
   ``exclude_criteria=[]`` everywhere — the default — the per-codelist
   F1 must stay within ±0.01 of the v2 K=5 baseline. The conditional
   path in ``llm_reasoning._render_condition`` produces a string
   byte-identical to pre-T29 when both lists are empty, and
   ``_compute_signature`` likewise appends nothing, so this is true
   by construction; the run is a sanity check, not a discovery.
   Five codelists (heart_failure, diabetes_mellitus, hypertension,
   mi_icd10, asthma_pincer) cover the spread of conditions and
   vocabularies in the v2 set.

2. **Carve-out lift on intent-sensitive lists.** Three runs with
   non-empty criteria:
   - ``hypertension`` with ``exclude_criteria=["secondary"]`` —
     reinforces the LLM's existing "primary only" instinct (§4 case 4)
     with an auditable, structured criterion. The expected effect is
     small but non-negative; the negative result is also informative
     for the methods paper.
   - ``hiv`` with ``include_criteria=["AIDS-defining illness"]`` —
     tests whether structured inclusion can recover the AIDS-defining
     condition codes that §4 case 7's Fix C overcorrection
     incorrectly excluded.
   - ``diabetes_no_gestational`` (the new persona-audit fixture) with
     ``exclude_criteria=["gestational"]`` — the cleanest demonstration
     of the carve-out's effect, since the fixture was designed for it.

Outputs land in ``data/test_sets/persona_audit/t29_benchmark_results.json``
so they don't co-mingle with the v2 K=5 history.

Cost (Haiku 4.5, today's prices, ~3-5 batches per run): ~$0.001/batch
× ~5 batches/run × 8 runs ≈ $0.04 + 8×$0.003 parser ≈ $0.07. Wall-clock
~4 min. Well under the user's $5 / 20-min cap.

Usage::

    python -m app.evaluation.run_t29_benchmark
    python -m app.evaluation.run_t29_benchmark --dry-run   # plan only

The script prints a summary table on completion and writes a JSON
artefact for EVALUATION.md §5.9 to cite verbatim.
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

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
PERSONA_AUDIT = ROOT / "data" / "test_sets" / "persona_audit"
OUT = PERSONA_AUDIT / "t29_benchmark_results.json"

# Plan: (label, fixture_path, query_override or None, exclusions, inclusions)
# - empty-criteria runs use empty lists for both
# - carve-out runs name the criterion explicitly
# - fixture path is relative to repo root for portability
PLAN: list[tuple[str, Path, str | None, list[str], list[str]]] = [
    # --- 1. empty-criteria invariance ---
    ("empty/heart_failure",     BENCH / "heart_failure.json",     None, [], []),
    ("empty/diabetes_mellitus", BENCH / "diabetes_mellitus.json", None, [], []),
    ("empty/hypertension",      BENCH / "hypertension.json",      None, [], []),
    ("empty/mi_icd10",          BENCH / "mi_icd10.json",          None, [], []),
    ("empty/asthma_pincer",     BENCH / "asthma_pincer.json",     None, [], []),
    # --- 2. carve-out lift ---
    ("carve/hypertension_exclude_secondary",
     BENCH / "hypertension.json", None, ["secondary"], []),
    ("carve/hiv_include_aids_defining",
     BENCH / "hiv.json", None, [], ["AIDS-defining illness"]),
    ("carve/diabetes_no_gestational",
     PERSONA_AUDIT / "diabetes_no_gestational.json", None, ["gestational"], []),
]


async def _one_run(label: str, test_set: list[dict], exclusions: list[str],
                   inclusions: list[str]) -> dict:
    query = test_set[0].get("Research_question", "")
    t0 = time.perf_counter()
    pipeline_result = await run_pipeline(
        query,
        disabled_retrievers=None,
        include_criteria=inclusions or None,
        exclude_criteria=exclusions or None,
    )
    elapsed = time.perf_counter() - t0
    final_codes = pipeline_result.get("final_code_list", [])
    eval_result = run_evaluation(test_set, {
        "results": final_codes,
        "retrieved_codes": pipeline_result.get("retrieved_codes", []),
        "enriched_codes": pipeline_result.get("enriched_codes", []),
    })
    inc = eval_result.get("stages", {}).get("included_only", {})
    return {
        "label": label,
        "query": query,
        "exclusions": exclusions,
        "inclusions": inclusions,
        "elapsed_seconds": round(elapsed, 2),
        "n_scored": len(final_codes),
        "precision": round(inc.get("precision", 0.0), 4),
        "recall": round(inc.get("recall", 0.0), 4),
        "f1": round(inc.get("f1", 0.0), 4),
    }


async def run(dry_run: bool = False) -> None:
    PERSONA_AUDIT.mkdir(parents=True, exist_ok=True)
    if dry_run:
        print(f"Plan: {len(PLAN)} runs")
        for (label, path, _q, exc, inc) in PLAN:
            print(f"  {label:55s}  exc={exc!r:30s}  inc={inc!r:25s}  fixture={path.name}")
        return

    rows: list[dict] = []
    sweep_t0 = time.perf_counter()
    for (label, path, _q, exc, inc) in PLAN:
        with open(path, encoding="utf-8") as f:
            test_set = json.load(f)
        logger.info("[%s] starting (%d/%d)", label, len(rows) + 1, len(PLAN))
        row = await _one_run(label, test_set, exc, inc)
        rows.append(row)
        logger.info(
            "[%s] %.1fs scored=%d P=%.3f R=%.3f F1=%.3f",
            label, row["elapsed_seconds"], row["n_scored"],
            row["precision"], row["recall"], row["f1"],
        )

    sweep_elapsed = time.perf_counter() - sweep_t0
    artefact = {
        "ticket": "T29",
        "purpose": "inclusion/exclusion criteria — empty-invariance + carve-out lift",
        "wall_clock_seconds": round(sweep_elapsed, 1),
        "runs": rows,
    }
    OUT.write_text(json.dumps(artefact, indent=2), encoding="utf-8")
    logger.info("Wrote %s (%.1fs total)", OUT.relative_to(ROOT), sweep_elapsed)

    print()
    print("=" * 78)
    print(f"T29 benchmark — {len(rows)} runs in {sweep_elapsed / 60:.1f} min")
    print("=" * 78)
    print(f"{'label':55s} {'P':>7s} {'R':>7s} {'F1':>7s} {'n':>5s}")
    print("-" * 78)
    for r in rows:
        print(f"{r['label']:55s} {r['precision']:>7.3f} {r['recall']:>7.3f} {r['f1']:>7.3f} {r['n_scored']:>5d}")
    print("-" * 78)
    print(f"Artefact: {OUT.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan without invoking the LLM.")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()

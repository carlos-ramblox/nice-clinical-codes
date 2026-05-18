"""Cap-sensitivity sweep on MAX_CANDIDATES.

Runs the K=5 paired benchmark at four cap values (100, 300, 500, 1000)
in bare and override modes. The parent loops over caps and spawns a
subprocess per (cap, mode) so each child re-imports ``app.config`` with
the env-fixed ``MAX_CANDIDATES`` value (the cap is read once at import
time in ``result_merger.py`` and ``umls_enrichment_node.py``).

Outputs land in ``data/test_sets/benchmark_2026_04/_cap_sensitivity/
cap_{N}_{mode}/`` with per-codelist ``runK_{1..5}`` envelopes that
mirror the existing ``_postT37j_bare`` / ``_postT37j_override`` files,
plus a ``cap_diagnostics`` block carrying the pre/post-cap counts and
gold-overlap.

Usage::

    # full sweep (4 caps x bare/override)
    python -m app.evaluation.run_cap_sensitivity

    # headline binary only
    python -m app.evaluation.run_cap_sensitivity --caps 100,500

    # single-codelist validation
    python -m app.evaluation.run_cap_sensitivity --caps 100 \
        --single-codelist heart_failure --skip-override

    # parent invokes itself for each child; --child is internal
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import truststore  # noqa: E402
truststore.inject_into_ssl()

from app.db.code_normalize import normalize_code  # noqa: E402


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"
CAP_DIR = BENCH / "_cap_sensitivity"

# Descendant-closed gold lists per T37j_path_a_summary.md.
DESCENDANT_CLOSED = (
    "epilepsy", "dementia", "copd", "lung_cancer",
    "psychosis_schiz_bipolar", "stroke", "asthma_pincer",
)

CAPS = (100, 300, 500, 1000)

# Per-batch cost estimate copied from run_variance_k5 (Haiku 4.5 pricing).
USD_PER_SCORING_BATCH = 0.001
USD_PER_QUERY_PARSE = 0.003


def _gold_set_from_test(test_set: list[dict]) -> set[str]:
    return {
        normalize_code(c, "")
        for entry in test_set
        for c in str(entry.get("Codelist", "")).split(";")
        if c.strip()
    }


def _output_dir(cap: int, mode: str) -> Path:
    return CAP_DIR / f"cap_{cap}_{mode}"


async def _one_run(
    short: str,
    k: int,
    query: str,
    test_set: list[dict],
    include_descendants: bool | None,
    out_dir: Path,
) -> tuple[dict | None, float]:
    """Run the pipeline once at the env-fixed cap and persist the envelope."""
    from app.evaluation.evaluator import run_evaluation  # noqa: WPS433
    from app.graph.graph import run_pipeline  # noqa: WPS433
    from app.graph.nodes.llm_reasoning import BATCH_SIZE  # noqa: WPS433

    out_path = out_dir / f"{short}.result_runK_{k}.json"
    if out_path.exists():
        return None, 0.0

    t0 = time.perf_counter()
    pipeline_result = await run_pipeline(query, include_descendants=include_descendants)
    elapsed = time.perf_counter() - t0

    final_codes = pipeline_result.get("final_code_list", [])
    retrieved_codes = pipeline_result.get("retrieved_codes", [])
    enriched_codes = pipeline_result.get("enriched_codes", [])

    envelope = run_evaluation(test_set, {
        "results": final_codes,
        "retrieved_codes": retrieved_codes,
        "enriched_codes": enriched_codes,
    })
    envelope["elapsed_seconds"] = round(elapsed, 2)
    envelope["pipeline_results_count"] = len(final_codes)
    envelope["scored_codes"] = final_codes
    envelope["pipeline"] = "rag"
    envelope["cold_start"] = False

    pre_cap = pipeline_result.get("candidates_pre_cap") or []
    pre_cap_keys = {normalize_code(c["code"], "") for c in pre_cap}
    enriched_keys = {normalize_code(c.get("code", ""), "") for c in enriched_codes}
    final_included_keys = {
        normalize_code(c.get("code", ""), "")
        for c in final_codes if c.get("decision") == "include"
    }
    gold = _gold_set_from_test(test_set)

    gold_pre = gold & pre_cap_keys
    gold_post_caps = gold & enriched_keys
    gold_final = gold & final_included_keys

    n_batches = max(1, (len(final_codes) + BATCH_SIZE - 1) // BATCH_SIZE)
    est_cost = USD_PER_QUERY_PARSE + n_batches * USD_PER_SCORING_BATCH

    envelope["cap_diagnostics"] = {
        "max_candidates_setting": pipeline_result.get("max_candidates_setting"),
        "candidates_before_cap": pipeline_result.get("candidates_before_cap_count"),
        "candidates_after_first_cap": pipeline_result.get("candidates_after_merger_cap_count"),
        "candidates_after_umls_cap": (
            pipeline_result.get("candidates_after_umls_cap_count")
            if pipeline_result.get("candidates_after_umls_cap_count") is not None
            else pipeline_result.get("candidates_after_merger_cap_count")
        ),
        "gold_codes_retrieved_before_cap": len(gold_pre),
        "gold_codes_after_caps": len(gold_post_caps),
        "gold_codes_lost_due_to_cap": len(gold_pre) - len(gold_post_caps),
        "gold_codes_in_final_output": len(gold_final),
        "include_descendants": include_descendants,
        "wallclock_seconds": round(elapsed, 2),
        "est_llm_cost_usd": round(est_cost, 5),
    }

    out_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    return envelope, est_cost


async def _child_main(
    cap: int,
    mode: str,
    codelists: list[str] | None,
    runs: int,
) -> None:
    out_dir = _output_dir(cap, mode)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)

    if codelists:
        wanted = set(codelists)
        selection = [s for s in selection if s["short"] in wanted]

    include_descendants = True if mode == "override" else None
    spent = 0.0
    t_start = time.perf_counter()

    for s in selection:
        short = s["short"]
        with open(BENCH / f"{short}.json", encoding="utf-8") as f:
            test_set = json.load(f)
        query = test_set[0].get("Research_question", "")
        for k in range(1, runs + 1):
            envelope, cost = await _one_run(
                short, k, query, test_set, include_descendants, out_dir,
            )
            if envelope is None:
                logger.info("[cap=%d %s %s run %d] already on disk", cap, mode, short, k)
                continue
            spent += cost
            inc = envelope["stages"].get("included_only", {})
            cd = envelope["cap_diagnostics"]
            logger.info(
                "[cap=%d %s %s run %d] %.1fs F1=%.3f P=%.3f R=%.3f "
                "pre_cap=%d after_merge=%d after_umls=%d gold_pre=%d gold_lost=%d gold_final=%d",
                cap, mode, short, k, cd["wallclock_seconds"],
                inc.get("f1", 0.0), inc.get("precision", 0.0), inc.get("recall", 0.0),
                cd["candidates_before_cap"], cd["candidates_after_first_cap"],
                cd["candidates_after_umls_cap"],
                cd["gold_codes_retrieved_before_cap"], cd["gold_codes_lost_due_to_cap"],
                cd["gold_codes_in_final_output"],
            )

    elapsed = time.perf_counter() - t_start
    meta = {
        "cap": cap,
        "mode": mode,
        "runs": runs,
        "codelists": [s["short"] for s in selection],
        "elapsed_seconds": round(elapsed, 2),
        "est_cost_usd": round(spent, 4),
    }
    (out_dir / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"CHILD_DONE cap={cap} mode={mode} elapsed={elapsed:.1f}s spent=${spent:.4f}")


def _spawn_child(
    cap: int,
    mode: str,
    codelists: list[str] | None,
    runs: int,
) -> tuple[float, float]:
    """Spawn the child subprocess for one (cap, mode) batch. Returns (elapsed, cost)."""
    env = {
        **os.environ,
        "MAX_CANDIDATES": str(cap),
        "EMIT_CAP_DIAGNOSTICS": "1",
    }
    cmd = [
        sys.executable, "-m", "app.evaluation.run_cap_sensitivity",
        "--child", "--cap", str(cap), "--mode", mode, "--runs", str(runs),
    ]
    if codelists:
        cmd += ["--codelists", ",".join(codelists)]

    logger.info("Spawning child: cap=%d mode=%s codelists=%s", cap, mode, codelists or "all")
    result = subprocess.run(
        cmd, env=env, check=True,
        cwd=str(ROOT / "backend"),
    )
    meta_path = _output_dir(cap, mode) / "_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return meta["elapsed_seconds"], meta["est_cost_usd"]
    return 0.0, 0.0


def parent_main(args: argparse.Namespace) -> None:
    caps = [int(c) for c in args.caps.split(",")] if args.caps else list(CAPS)

    wanted = [c.strip() for c in args.codelists.split(",") if c.strip()] if args.codelists else None
    if wanted:
        bare_lists = wanted
        override_lists = [c for c in wanted if c in DESCENDANT_CLOSED]
    else:
        bare_lists = None
        override_lists = list(DESCENDANT_CLOSED)

    total_spent = 0.0
    total_elapsed = 0.0

    for cap in caps:
        if total_spent >= args.cap_usd:
            logger.warning(
                "USD cap $%.2f reached (spent $%.2f). Aborting before cap=%d.",
                args.cap_usd, total_spent, cap,
            )
            break

        elapsed, cost = _spawn_child(cap, "bare", bare_lists, args.runs)
        total_spent += cost
        total_elapsed += elapsed
        logger.info("After cap=%d bare: spent=$%.2f elapsed=%.0fs (cumulative)", cap, total_spent, total_elapsed)

        if args.skip_override or not override_lists:
            continue
        if total_spent >= args.cap_usd:
            logger.warning("USD cap reached; skipping override at cap=%d", cap)
            continue

        elapsed, cost = _spawn_child(cap, "override", override_lists, args.runs)
        total_spent += cost
        total_elapsed += elapsed
        logger.info("After cap=%d override: spent=$%.2f elapsed=%.0fs (cumulative)", cap, total_spent, total_elapsed)

    print(f"PARENT_DONE caps={caps} total_elapsed={total_elapsed:.0f}s total_spent=${total_spent:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cap", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("bare", "override"), help=argparse.SUPPRESS)

    parser.add_argument("--codelists", type=str, default=None,
                        help="Comma-separated codelist shorts to sweep "
                             "(parent: filter the sweep; child: the literal list to process)")
    parser.add_argument("--caps", type=str, default=None,
                        help="Comma-separated cap values to sweep (default: 100,300,500,1000)")
    parser.add_argument("--runs", type=int, default=5,
                        help="K runs per (codelist, cap, mode) (default: 5)")
    parser.add_argument("--skip-override", action="store_true",
                        help="Skip the override mode (bare-only sweep)")
    parser.add_argument("--cap-usd", type=float, default=30.0,
                        help="Hard USD cap across the whole sweep (default: 30)")
    args = parser.parse_args()

    if args.child:
        codelists = [c.strip() for c in args.codelists.split(",")] if args.codelists else None
        asyncio.run(_child_main(args.cap, args.mode, codelists, args.runs))
    else:
        parent_main(args)


if __name__ == "__main__":
    main()

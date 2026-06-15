"""One-shot retrieval test: does an expanded HIV query lift the
joint-retriever coverage of the NHSD HIV refset?

Baseline: "Human immunodeficiency virus (HIV) codes" (current benchmark
query) surfaces ~100 candidates pre-cap, of which only 9 intersect the
243-code gold. The cap is not the binding constraint; the retrievers
are.

Expanded probe: "HIV including AIDS-defining illnesses and HIV-related
complications". One run, cap=500, bare mode, K=1. Captures the pre-cap
candidate pool and the gold-overlap, then prints a side-by-side against
the existing K=5 mean at the baseline query.

Run with:

    python -m bench.test_hiv_expanded_query
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

import truststore  # noqa: E402
truststore.inject_into_ssl()

os.environ.setdefault("MAX_CANDIDATES", "500")
os.environ["EMIT_CAP_DIAGNOSTICS"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.code_normalize import normalize_code  # noqa: E402
from app.graph.graph import run_pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"

BASELINE_QUERY = "Human immunodeficiency virus (HIV) codes"
EXPANDED_QUERY = "HIV including AIDS-defining illnesses and HIV-related complications"


def _gold_set() -> set[str]:
    with open(BENCH / "hiv.json", encoding="utf-8") as f:
        ts = json.load(f)
    return {
        normalize_code(c, "")
        for entry in ts
        for c in str(entry.get("Codelist", "")).split(";")
        if c.strip()
    }


def _baseline_overlap() -> dict:
    """K=5 mean from the cap=500 bare HIV runs already on disk."""
    pre_cap_gold: list[int] = []
    pre_cap_size: list[int] = []
    final_gold: list[int] = []
    dir_ = BENCH / "_cap_sensitivity" / "cap_500_bare"
    for k in range(1, 6):
        p = dir_ / f"hiv.result_runK_{k}.json"
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            run = json.load(f)
        cd = run.get("cap_diagnostics") or {}
        pre_cap_gold.append(cd.get("gold_codes_retrieved_before_cap", 0))
        pre_cap_size.append(cd.get("candidates_before_cap", 0))
        final_gold.append(cd.get("gold_codes_in_final_output", 0))
    return {
        "k": len(pre_cap_gold),
        "mean_pre_cap_pool": statistics.fmean(pre_cap_size) if pre_cap_size else 0,
        "mean_gold_in_pre_cap_pool": statistics.fmean(pre_cap_gold) if pre_cap_gold else 0,
        "mean_gold_in_final_output": statistics.fmean(final_gold) if final_gold else 0,
    }


async def _run_expanded(gold: set[str]) -> dict:
    result = await run_pipeline(EXPANDED_QUERY, include_descendants=None)
    pre_cap = result.get("candidates_pre_cap") or []
    enriched = result.get("enriched_codes") or []
    final = result.get("final_code_list") or []
    final_included = [c for c in final if c.get("decision") == "include"]

    pre_cap_keys = {normalize_code(c["code"], "") for c in pre_cap}
    enriched_keys = {normalize_code(c.get("code", ""), "") for c in enriched}
    final_keys = {normalize_code(c.get("code", ""), "") for c in final_included}

    return {
        "query": EXPANDED_QUERY,
        "pre_cap_size": len(pre_cap),
        "post_cap_size": result.get("candidates_after_merger_cap_count"),
        "post_umls_size": result.get("candidates_after_umls_cap_count"),
        "max_candidates": result.get("max_candidates_setting"),
        "gold_in_pre_cap": len(gold & pre_cap_keys),
        "gold_in_enriched": len(gold & enriched_keys),
        "gold_in_final_output": len(gold & final_keys),
        "final_included_count": len(final_included),
    }


async def main() -> int:
    gold = _gold_set()
    print(f"HIV gold size: {len(gold)}")
    baseline = _baseline_overlap()
    print(f"\nBaseline ('{BASELINE_QUERY}') K=5 cap=500 bare:")
    print(f"  mean pre-cap pool size:        {baseline['mean_pre_cap_pool']:.1f}")
    print(f"  mean gold in pre-cap pool:     {baseline['mean_gold_in_pre_cap_pool']:.1f} / {len(gold)}")
    print(f"  mean gold in final output:     {baseline['mean_gold_in_final_output']:.1f}")

    print(f"\nRunning expanded query (1 shot, cap=500 bare):")
    print(f"  {EXPANDED_QUERY!r}")
    expanded = await _run_expanded(gold)
    print(f"  pre-cap pool size:             {expanded['pre_cap_size']}")
    print(f"  post-merger-cap pool size:     {expanded['post_cap_size']}")
    print(f"  post-UMLS-cap pool size:       {expanded['post_umls_size']}")
    print(f"  gold in pre-cap pool:          {expanded['gold_in_pre_cap']} / {len(gold)}")
    print(f"  gold in enriched (post-caps):  {expanded['gold_in_enriched']}")
    print(f"  gold in final LLM-included:    {expanded['gold_in_final_output']}")
    print(f"  final included codes:          {expanded['final_included_count']}")

    delta_pre = expanded["gold_in_pre_cap"] - baseline["mean_gold_in_pre_cap_pool"]
    delta_final = expanded["gold_in_final_output"] - baseline["mean_gold_in_final_output"]
    print(f"\nDelta vs baseline:")
    print(f"  gold lift in pre-cap pool:     {delta_pre:+.1f}")
    print(f"  gold lift in final output:     {delta_final:+.1f}")

    out_path = BENCH / "_cap_sensitivity" / "test_hiv_expanded_query.json"
    out = {
        "baseline_query": BASELINE_QUERY,
        "expanded_query": EXPANDED_QUERY,
        "gold_size": len(gold),
        "baseline_k5": baseline,
        "expanded_k1": expanded,
        "delta_gold_in_pre_cap": delta_pre,
        "delta_gold_in_final_output": delta_final,
    }
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

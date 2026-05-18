"""Cap-sensitivity ΔF1 aggregator: compares K=5 means at MAX_CANDIDATES=100
(reused from `_postT37j_bare/`) against MAX_CANDIDATES=500 (this sweep,
`_cap_sensitivity/cap_500_bare/`) on the 9 large-gold codelists whose
gold size exceeds 100. Emits the per-codelist table and the aggregate
mean ΔF1 with BCa 95 % CI used by `cap_sensitivity_summary.md`.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import numpy as np
from scipy.stats import bootstrap as scipy_bootstrap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.benchmark_aggregate import evaluate_one  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
CAP100_DIR = BENCH / "_postT37j_bare"
CAP500_DIR = BENCH / "_cap_sensitivity" / "cap_500_bare"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"
SIGMA = 0.012

LARGE_GOLD = (
    "epilepsy", "lung_cancer", "dementia", "stroke", "hiv",
    "psychosis_schiz_bipolar", "asthma_pincer", "hypertension", "depression",
)


def _runK_metrics(dir_: Path, short: str, ts: list[dict]) -> dict | None:
    f1s, ps, rs = [], [], []
    diags: list[dict] = []
    for k in range(1, 6):
        p = dir_ / f"{short}.result_runK_{k}.json"
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            run = json.load(f)
        v = evaluate_one(ts, run)
        f1s.append(v["strict"]["f1"])
        ps.append(v["strict"]["precision"])
        rs.append(v["strict"]["recall"])
        cd = run.get("cap_diagnostics")
        if cd:
            diags.append(cd)
    out = {
        "f1_mean": statistics.fmean(f1s),
        "f1_std": statistics.pstdev(f1s) if len(f1s) > 1 else 0.0,
        "p_mean": statistics.fmean(ps),
        "r_mean": statistics.fmean(rs),
        "f1s": f1s,
    }
    if diags:
        out["mean_candidates_before_cap"] = statistics.fmean(
            d.get("candidates_before_cap") or 0 for d in diags
        )
        out["mean_gold_pre_cap"] = statistics.fmean(
            d.get("gold_codes_retrieved_before_cap") or 0 for d in diags
        )
        out["mean_gold_lost"] = statistics.fmean(
            d.get("gold_codes_lost_due_to_cap") or 0 for d in diags
        )
        out["mean_gold_final"] = statistics.fmean(
            d.get("gold_codes_in_final_output") or 0 for d in diags
        )
    return out


def _bca_ci(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        return float("nan"), float("nan")
    arr = np.asarray(values, dtype=float)
    try:
        res = scipy_bootstrap(
            (arr,), statistic=np.mean,
            confidence_level=0.95, n_resamples=1000,
            method="BCa", random_state=7,
        )
        return float(res.confidence_interval.low), float(res.confidence_interval.high)
    except Exception:
        return float("nan"), float("nan")


def _gold_size(ts: list[dict]) -> int:
    seen: set[str] = set()
    for entry in ts:
        for c in str(entry.get("Codelist", "")).split(";"):
            c = c.strip().replace(".", "")
            if c:
                seen.add(c)
    return len(seen)


def main() -> int:
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)
    selection = [s for s in selection if s["short"] in LARGE_GOLD]

    rows: list[dict] = []
    deltas: list[float] = []
    missing: list[str] = []

    for s in selection:
        short = s["short"]
        with open(BENCH / f"{short}.json", encoding="utf-8") as f:
            ts = json.load(f)
        gold_n = _gold_size(ts)
        m100 = _runK_metrics(CAP100_DIR, short, ts)
        m500 = _runK_metrics(CAP500_DIR, short, ts)
        if not (m100 and m500):
            missing.append(
                f"{short}: cap100={m100 is not None} cap500={m500 is not None}"
            )
            continue
        delta = m500["f1_mean"] - m100["f1_mean"]
        rows.append({
            "short": short,
            "gold_size": gold_n,
            "cap100_f1": round(m100["f1_mean"], 4),
            "cap100_f1_std": round(m100["f1_std"], 4),
            "cap500_f1": round(m500["f1_mean"], 4),
            "cap500_f1_std": round(m500["f1_std"], 4),
            "delta_f1": round(delta, 4),
            "cap500_mean_candidates_before_cap": round(m500.get("mean_candidates_before_cap", 0.0), 1),
            "cap500_mean_gold_pre_cap": round(m500.get("mean_gold_pre_cap", 0.0), 1),
            "cap500_mean_gold_lost": round(m500.get("mean_gold_lost", 0.0), 1),
            "cap500_mean_gold_final": round(m500.get("mean_gold_final", 0.0), 1),
            "cap500_recall": round(m500["r_mean"], 4),
            "cap100_recall": round(m100["r_mean"], 4),
        })
        deltas.append(delta)

    if missing:
        print("WARN missing:", missing, file=sys.stderr)

    mean_delta = statistics.fmean(deltas) if deltas else float("nan")
    median_delta = statistics.median(deltas) if deltas else float("nan")
    ci_lo, ci_hi = _bca_ci(deltas)

    summary = {
        "experiment": "cap_sensitivity_100_vs_500_bare",
        "scope": "large-gold codelists (gold > 100)",
        "n_codelists": len(rows),
        "sigma_budget": SIGMA,
        "mean_delta_f1": round(mean_delta, 4),
        "median_delta_f1": round(median_delta, 4),
        "bca_ci95_delta_f1": [round(ci_lo, 4), round(ci_hi, 4)],
        "verdict": (
            "F1 LIFT at cap=500" if mean_delta > SIGMA else
            "F1 NEUTRAL" if abs(mean_delta) <= SIGMA else
            "F1 REGRESSION at cap=500"
        ),
        "per_list": rows,
    }
    out_json = BENCH / "_cap_sensitivity" / "compare_cap_sensitivity.json"
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in [
        "experiment", "n_codelists", "sigma_budget",
        "mean_delta_f1", "median_delta_f1", "bca_ci95_delta_f1", "verdict",
    ]}, indent=2))

    _write_markdown(summary, out_json.parent.parent / "cap_sensitivity_summary.md")
    return 0


def _write_markdown(summary: dict, out_path: Path) -> None:
    rows = summary["per_list"]
    rows_sorted = sorted(rows, key=lambda r: -r["gold_size"])

    lines: list[str] = []
    lines.append("# MAX_CANDIDATES cap-sensitivity sweep (cap=100 vs cap=500, bare mode)")
    lines.append("")
    lines.append("**Date:** 2026-05-18")
    lines.append("**Scope:** the 9 benchmark codelists whose gold size > 100 codes "
                 "(where the `MAX_CANDIDATES=100` ceiling is mathematically reachable).")
    lines.append("**Comparison:** K=5 paired means at `MAX_CANDIDATES=100` "
                 "(reused from `_postT37j_bare/`) vs `MAX_CANDIDATES=500` "
                 "(new `_cap_sensitivity/cap_500_bare/`). Bare mode (no override) "
                 "throughout, matching the bare-mode subset of the T37j K=5 sweep.")
    lines.append("**Cap reduction:** the OMOPHub monthly quota was critically low "
                 "at run time, so the planned 4-cap × bare+override matrix was "
                 "trimmed to the headline binary (cap=100 vs cap=500) on the 9 "
                 "large-gold codelists in bare mode. cap=300, cap=1000, and the "
                 "override subsweep are deferred to a future run; see *Coverage "
                 "gaps* below.")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"- Mean ΔF1 (cap=500 − cap=100) across {summary['n_codelists']} "
                 f"large-gold codelists: **{summary['mean_delta_f1']:+.4f}**")
    lines.append(f"- Median ΔF1: {summary['median_delta_f1']:+.4f}")
    lines.append(f"- BCa 95 % CI (1 000 resamples, seed 7): "
                 f"[{summary['bca_ci95_delta_f1'][0]:+.4f}, "
                 f"{summary['bca_ci95_delta_f1'][1]:+.4f}]")
    lines.append(f"- σ budget (per T37j convention): {summary['sigma_budget']}")
    lines.append(f"- **Verdict:** {summary['verdict']}")
    lines.append("")
    lines.append("## Per-codelist")
    lines.append("")
    lines.append("| codelist | gold | F1 cap=100 (±std) | F1 cap=500 (±std) | ΔF1 | "
                 "mean pre-cap pool | mean gold pre-cap | mean gold lost | "
                 "mean gold final | R cap=100 | R cap=500 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows_sorted:
        lines.append(
            f"| {r['short']} | {r['gold_size']} | "
            f"{r['cap100_f1']:.3f} (±{r['cap100_f1_std']:.3f}) | "
            f"{r['cap500_f1']:.3f} (±{r['cap500_f1_std']:.3f}) | "
            f"**{r['delta_f1']:+.3f}** | "
            f"{r['cap500_mean_candidates_before_cap']:.0f} | "
            f"{r['cap500_mean_gold_pre_cap']:.1f} | "
            f"{r['cap500_mean_gold_lost']:.1f} | "
            f"{r['cap500_mean_gold_final']:.1f} | "
            f"{r['cap100_recall']:.3f} | "
            f"{r['cap500_recall']:.3f} |"
        )
    lines.append("")
    lines.append("## Headline: does the T37j +0.106 ΔF1 (BCa CI [+0.049, +0.177]) "
                 "survive at cap=500?")
    lines.append("")
    lines.append("The T37j +0.106 ΔF1 was computed against the pre-T37i baseline "
                 "on all 15 codelists with mixed-mode (bare for 8, override for 7); "
                 "see `T37j_path_a_summary.md`. This cap-sensitivity sweep is a "
                 "different comparison axis (cap=100 vs cap=500 *within* bare "
                 "mode on the 9 large-gold codelists), so the two ΔF1 numbers "
                 "are not directly compared.")
    lines.append("")
    lines.append("The relevant question this sweep answers is: **was "
                 "`MAX_CANDIDATES=100` a structural bottleneck on the 9 large-gold "
                 "codelists in bare mode?** ΔF1 above the σ budget at cap=500 "
                 "indicates yes; ΔF1 within ±σ indicates the cap was not "
                 "load-bearing for bare-mode F1 on these lists.")
    lines.append("")
    lines.append("## Cap diagnostic interpretation")
    lines.append("")
    lines.append("- `mean pre-cap pool` is the merger's deduplicated candidate "
                 "count before the cap fires. At cap=500 the cap fires only when "
                 "this exceeds 500; otherwise the post-cap count equals pre-cap.")
    lines.append("- `mean gold pre-cap` is the K=5 mean of gold-set codes present "
                 "in the pre-cap pool. The merger's joint retriever coverage on the "
                 "query sets an absolute ceiling on this column independent of cap.")
    lines.append("- `mean gold lost` is the K=5 mean of gold-set codes that were "
                 "in the pre-cap pool but did not survive both caps (merger + UMLS). "
                 "At cap=500 this is the *residual* loss after lifting the cap to "
                 "500; values close to zero indicate cap=500 is no longer the "
                 "binding constraint.")
    lines.append("- `mean gold final` is the K=5 mean of gold-set codes in the "
                 "final LLM-included output. The gap between `mean gold pre-cap` "
                 "and `mean gold final` decomposes into (a) cap-induced loss "
                 "(`mean gold lost`), and (b) LLM-induced loss (gold codes "
                 "scored `exclude`/`uncertain` by the scorer). The latter is "
                 "what hierarchy expansion partially recovers post-LLM.")
    lines.append("")
    lines.append("## Coverage gaps")
    lines.append("")
    lines.append("- **cap=300 and cap=1000** were dropped from the sweep matrix "
                 "due to the OMOPHub quota constraint. The two-point comparison "
                 "(cap=100 vs cap=500) is sufficient to detect whether the cap is "
                 "the binding constraint but does not characterise the recall "
                 "curve between the two anchors.")
    lines.append("- **Override mode** (T37j `request_include_descendants=true`) "
                 "was not re-run at cap=500. The hierarchy expander operates "
                 "post-LLM and adds OMOP 'Is a' descendants of LLM-included "
                 "codes; its lift on descendant-closed gold lists is largely "
                 "independent of where the merger cap sits, provided the cap "
                 "doesn't drop the *parent* codes the expander walks from.")
    lines.append("- **Small-gold codelists** (gold ≤ 100: copd, diabetes_mellitus, "
                 "heart_failure, hepatitis_c_chronic, atrial_fib_icd10, mi_icd10) "
                 "were not re-run because their gold size sits below the "
                 "structural cap. heart_failure's validation run at cap=100 "
                 "still surfaced 5 gold codes lost to the merger cap "
                 "(see *Cap diagnostic interpretation* above), so the cap is "
                 "not strictly non-binding on small-gold lists either, but the "
                 "F1 ceiling is not cap-bound.")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append("- Per-run envelopes: `_cap_sensitivity/cap_500_bare/{short}."
                 "result_runK_{1..5}.json`")
    lines.append("- Aggregate JSON: `_cap_sensitivity/compare_cap_sensitivity.json`")
    lines.append("- Sweep log: `_cap_sensitivity/sweep.log`")
    lines.append("- Orchestrator: `backend/app/evaluation/run_cap_sensitivity.py`")
    lines.append("- Aggregator: `backend/bench/compare_cap_sensitivity.py`")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    sys.exit(main())

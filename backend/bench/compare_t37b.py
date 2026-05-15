"""T37b: ΔF1 between pre-T37 (_preT37/) and post-T37 K=5 baselines.

Run: cd backend && python -m bench.compare_t37b
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
PRE = BENCH / "_preT37"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"
SIGMA = 0.012  # K=5 run-to-run F1 std (T07 finding).


def _runK_f1s(dir_: Path, short: str, ts: list[dict]) -> list[float]:
    f1s: list[float] = []
    for k in range(1, 6):
        p = dir_ / f"{short}.result_runK_{k}.json"
        if not p.exists():
            return []
        with open(p, encoding="utf-8") as f:
            run = json.load(f)
        view = evaluate_one(ts, run)
        f1s.append(view["strict"]["f1"])
    return f1s


def _bca_ci(values: list[float], confidence: float = 0.95) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    try:
        res = scipy_bootstrap(
            (arr,),
            statistic=np.mean,
            confidence_level=confidence,
            n_resamples=10000,
            method="BCa",
            random_state=7,
        )
        return float(res.confidence_interval.low), float(res.confidence_interval.high)
    except Exception:
        return float("nan"), float("nan")


def main() -> int:
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)

    per_list: list[dict] = []
    deltas: list[float] = []
    missing_pre: list[str] = []
    missing_post: list[str] = []

    for s in selection:
        short = s["short"]
        with open(BENCH / f"{short}.json", encoding="utf-8") as f:
            ts = json.load(f)
        pre = _runK_f1s(PRE, short, ts)
        post = _runK_f1s(BENCH, short, ts)
        if not pre:
            missing_pre.append(short)
            continue
        if not post:
            missing_post.append(short)
            continue
        pre_mean = statistics.fmean(pre)
        post_mean = statistics.fmean(post)
        delta = post_mean - pre_mean
        pre_std = statistics.stdev(pre) if len(pre) >= 2 else float("nan")
        post_std = statistics.stdev(post) if len(post) >= 2 else float("nan")
        per_list.append({
            "short": short,
            "pre_f1_mean": round(pre_mean, 4),
            "pre_f1_std": round(pre_std, 4),
            "post_f1_mean": round(post_mean, 4),
            "post_f1_std": round(post_std, 4),
            "delta_f1": round(delta, 4),
            "abs_delta_within_sigma": abs(delta) <= SIGMA,
            "n_runs_pre": len(pre),
            "n_runs_post": len(post),
        })
        deltas.append(delta)

    if missing_pre or missing_post:
        print(f"WARN: missing pre={missing_pre} post={missing_post}", file=sys.stderr)

    n = len(deltas)
    mean_delta = statistics.fmean(deltas) if deltas else float("nan")
    median_delta = statistics.median(deltas) if deltas else float("nan")
    max_abs = max(abs(d) for d in deltas) if deltas else float("nan")
    over_sigma = [p for p in per_list if not p["abs_delta_within_sigma"]]
    ci_lo, ci_hi = _bca_ci(deltas) if deltas else (float("nan"), float("nan"))

    summary = {
        "ticket": "T37b",
        "n_codelists": n,
        "sigma_budget": SIGMA,
        "delta_f1_mean": round(mean_delta, 4),
        "delta_f1_median": round(median_delta, 4),
        "delta_f1_max_abs": round(max_abs, 4),
        "delta_f1_bca_ci95": [round(ci_lo, 4), round(ci_hi, 4)],
        "n_codelists_outside_sigma": len(over_sigma),
        "outside_sigma": [{"short": p["short"], "delta_f1": p["delta_f1"]} for p in over_sigma],
        "per_list": per_list,
        "verdict": "PASS" if max_abs <= SIGMA else "FAIL",
        "pre_baseline_commit": "f4c9556 (T07 / pre-T28/29/30/31/32/33/36/37)",
        "post_baseline_branch": "develop (T37 + T37g)",
    }

    out = BENCH / "result_postfix_t37.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({
        k: summary[k] for k in [
            "ticket", "n_codelists", "sigma_budget",
            "delta_f1_mean", "delta_f1_median", "delta_f1_max_abs",
            "delta_f1_bca_ci95", "n_codelists_outside_sigma", "verdict",
        ]
    }, indent=2))
    if over_sigma:
        print("\nOutside sigma:", json.dumps(summary["outside_sigma"], indent=2))
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

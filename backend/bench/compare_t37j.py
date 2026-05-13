"""T37j: 4-way ΔF1 comparison across pre-T37i / post-T37i / post-T37j
bare / post-T37j with-override K=5 baselines."""
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
PRE_T37I = BENCH / "_preT37i"
POST_T37I = BENCH  # tracked-in-git T37i baseline at top level (compare_t37i.py convention)
POST_T37J_BARE = BENCH / "_postT37j_bare"
POST_T37J_OVERRIDE = BENCH / "_postT37j_override"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"
SIGMA = 0.012

# The 7 descendant-closed gold lists where T37i lifted F1 by +0.16 to
# +0.38. Sweep 2 forces include_descendants=True on these.
DESCENDANT_CLOSED = {
    "epilepsy", "dementia", "copd", "lung_cancer",
    "psychosis_schiz_bipolar", "stroke", "asthma_pincer",
}


def _runK_metrics(dir_: Path, short: str, ts: list[dict]) -> dict | None:
    f1s, ps, rs = [], [], []
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
    return {
        "f1": statistics.fmean(f1s),
        "precision": statistics.fmean(ps),
        "recall": statistics.fmean(rs),
    }


def _bca_ci(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        return float("nan"), float("nan")
    arr = np.asarray(values, dtype=float)
    try:
        res = scipy_bootstrap(
            (arr,), statistic=np.mean,
            confidence_level=0.95, n_resamples=10000, method="BCa", random_state=7,
        )
        return float(res.confidence_interval.low), float(res.confidence_interval.high)
    except Exception:
        return float("nan"), float("nan")


def _resolved_t37j(short: str) -> tuple[Path, str]:
    """Pick the right post-T37j directory for a codelist: with-override
    on the 7 descendant-closed gold lists, bare for everyone else."""
    if short in DESCENDANT_CLOSED:
        return POST_T37J_OVERRIDE, "override"
    return POST_T37J_BARE, "bare"


def main() -> int:
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)

    rows: list[dict] = []
    deltas_vs_prei: list[float] = []
    deltas_vs_posti: list[float] = []
    missing: list[str] = []

    for s in selection:
        short = s["short"]
        with open(BENCH / f"{short}.json", encoding="utf-8") as f:
            ts = json.load(f)
        pre = _runK_metrics(PRE_T37I, short, ts)
        post_i = _runK_metrics(POST_T37I, short, ts)
        t37j_dir, t37j_mode = _resolved_t37j(short)
        post_j = _runK_metrics(t37j_dir, short, ts)
        if not (pre and post_i and post_j):
            missing.append(
                f"{short}: pre={pre is not None} "
                f"post_i={post_i is not None} post_j={post_j is not None}"
            )
            continue
        d_vs_prei = post_j["f1"] - pre["f1"]
        d_vs_posti = post_j["f1"] - post_i["f1"]
        rows.append({
            "short": short,
            "t37j_mode": t37j_mode,
            "pre_t37i_f1": round(pre["f1"], 4),
            "post_t37i_f1": round(post_i["f1"], 4),
            "post_t37j_f1": round(post_j["f1"], 4),
            "delta_vs_pre_t37i": round(d_vs_prei, 4),
            "delta_vs_post_t37i": round(d_vs_posti, 4),
            "abs_delta_vs_pre_within_sigma": abs(d_vs_prei) <= SIGMA,
            "abs_delta_vs_post_within_sigma": abs(d_vs_posti) <= SIGMA,
        })
        deltas_vs_prei.append(d_vs_prei)
        deltas_vs_posti.append(d_vs_posti)

    if missing:
        print("WARN missing:", missing, file=sys.stderr)

    mean_vs_prei = statistics.fmean(deltas_vs_prei) if deltas_vs_prei else float("nan")
    mean_vs_posti = statistics.fmean(deltas_vs_posti) if deltas_vs_posti else float("nan")
    ci_pre_lo, ci_pre_hi = _bca_ci(deltas_vs_prei)
    ci_post_lo, ci_post_hi = _bca_ci(deltas_vs_posti)

    summary = {
        "ticket": "T37j",
        "n_codelists": len(rows),
        "sigma_budget": SIGMA,
        "mean_delta_vs_pre_t37i": round(mean_vs_prei, 4),
        "mean_delta_vs_post_t37i": round(mean_vs_posti, 4),
        "median_delta_vs_pre_t37i": round(statistics.median(deltas_vs_prei), 4) if deltas_vs_prei else float("nan"),
        "bca_ci95_vs_pre_t37i": [round(ci_pre_lo, 4), round(ci_pre_hi, 4)],
        "bca_ci95_vs_post_t37i": [round(ci_post_lo, 4), round(ci_post_hi, 4)],
        "verdict_vs_pre_t37i": (
            "F1 LIFT" if mean_vs_prei > SIGMA else
            "F1 NEUTRAL" if abs(mean_vs_prei) <= SIGMA else
            "F1 REGRESSION"
        ),
        "verdict_vs_post_t37i": (
            "F1 LIFT" if mean_vs_posti > SIGMA else
            "F1 NEUTRAL" if abs(mean_vs_posti) <= SIGMA else
            "F1 REGRESSION"
        ),
        "per_list": rows,
    }
    (BENCH / "result_postfix_t37j.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in [
        "ticket", "n_codelists", "sigma_budget",
        "mean_delta_vs_pre_t37i", "mean_delta_vs_post_t37i",
        "median_delta_vs_pre_t37i",
        "bca_ci95_vs_pre_t37i", "bca_ci95_vs_post_t37i",
        "verdict_vs_pre_t37i", "verdict_vs_post_t37i",
    ]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

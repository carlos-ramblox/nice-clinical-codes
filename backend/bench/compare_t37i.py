"""T37i: ΔF1 between pre-T37i (_preT37i/) and post-T37i K=5 baselines."""
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
PRE = BENCH / "_preT37i"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"
SIGMA = 0.012


def _runK_f1s(dir_: Path, short: str, ts: list[dict]) -> list[float]:
    out: list[float] = []
    for k in range(1, 6):
        p = dir_ / f"{short}.result_runK_{k}.json"
        if not p.exists():
            return []
        with open(p, encoding="utf-8") as f:
            run = json.load(f)
        view = evaluate_one(ts, run)
        out.append(view["strict"]["f1"])
    return out


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


def main() -> int:
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)

    rows: list[dict] = []
    deltas: list[float] = []
    p_deltas: list[float] = []
    r_deltas: list[float] = []
    missing: list[str] = []

    for s in selection:
        short = s["short"]
        with open(BENCH / f"{short}.json", encoding="utf-8") as f:
            ts = json.load(f)
        pre = _runK_f1s(PRE, short, ts)
        post = _runK_f1s(BENCH, short, ts)
        if not (pre and post):
            missing.append(f"{short}: pre={len(pre)} post={len(post)}")
            continue
        pre_mean = statistics.fmean(pre); post_mean = statistics.fmean(post)
        delta = post_mean - pre_mean

        # also compute precision/recall mean for diagnostic colour
        def _pr(dir_: Path) -> tuple[float, float]:
            ps, rs = [], []
            for k in range(1, 6):
                with open(dir_ / f"{short}.result_runK_{k}.json", encoding="utf-8") as f:
                    run = json.load(f)
                v = evaluate_one(ts, run)
                ps.append(v["strict"]["precision"]); rs.append(v["strict"]["recall"])
            return statistics.fmean(ps), statistics.fmean(rs)
        pre_p, pre_r = _pr(PRE); post_p, post_r = _pr(BENCH)

        rows.append({
            "short": short,
            "pre_f1": round(pre_mean, 4), "post_f1": round(post_mean, 4),
            "delta_f1": round(delta, 4),
            "delta_precision": round(post_p - pre_p, 4),
            "delta_recall": round(post_r - pre_r, 4),
            "abs_delta_f1_within_sigma": abs(delta) <= SIGMA,
        })
        deltas.append(delta)
        p_deltas.append(post_p - pre_p)
        r_deltas.append(post_r - pre_r)

    if missing:
        print("WARN missing:", missing, file=sys.stderr)

    mean_d = statistics.fmean(deltas) if deltas else float("nan")
    ci_lo, ci_hi = _bca_ci(deltas)
    summary = {
        "ticket": "T37i",
        "n_codelists": len(rows),
        "sigma_budget": SIGMA,
        "delta_f1_mean": round(mean_d, 4),
        "delta_f1_median": round(statistics.median(deltas), 4) if deltas else float("nan"),
        "delta_f1_max_abs": round(max(abs(d) for d in deltas), 4) if deltas else float("nan"),
        "delta_f1_bca_ci95": [round(ci_lo, 4), round(ci_hi, 4)],
        "delta_precision_mean": round(statistics.fmean(p_deltas), 4) if p_deltas else float("nan"),
        "delta_recall_mean": round(statistics.fmean(r_deltas), 4) if r_deltas else float("nan"),
        "verdict": (
            "F1 LIFT" if mean_d > SIGMA else
            "F1 NEUTRAL" if abs(mean_d) <= SIGMA else
            "F1 REGRESSION"
        ),
        "per_list": rows,
    }
    (BENCH / "result_postfix_t37i.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in [
        "ticket", "n_codelists", "sigma_budget",
        "delta_f1_mean", "delta_f1_median", "delta_f1_max_abs",
        "delta_f1_bca_ci95", "delta_precision_mean", "delta_recall_mean", "verdict",
    ]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

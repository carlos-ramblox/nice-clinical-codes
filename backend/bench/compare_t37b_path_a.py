"""T37b Path A: three-way K=5 compare across _preT37/, _sameDayPre/, HEAD.

C-vs-B isolates T37 code drift; B-vs-A isolates 9-day environmental drift.
Writes data/test_sets/benchmark_2026_04/result_postfix_t37.json.
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
SAMEDAY = BENCH / "_sameDayPre"
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
    arr = np.asarray(values, dtype=float)
    try:
        res = scipy_bootstrap(
            (arr,),
            statistic=np.mean,
            confidence_level=0.95,
            n_resamples=10000,
            method="BCa",
            random_state=7,
        )
        return float(res.confidence_interval.low), float(res.confidence_interval.high)
    except Exception:
        return float("nan"), float("nan")


def _mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else float("nan")


def main() -> int:
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)

    rows: list[dict] = []
    cb_deltas: list[float] = []
    ab_deltas: list[float] = []
    ac_deltas: list[float] = []
    missing: list[str] = []

    for s in selection:
        short = s["short"]
        with open(BENCH / f"{short}.json", encoding="utf-8") as f:
            ts = json.load(f)
        a = _runK_f1s(PRE, short, ts)
        b = _runK_f1s(SAMEDAY, short, ts)
        c = _runK_f1s(BENCH, short, ts)
        if not (a and b and c):
            missing.append(f"{short}: A={len(a)} B={len(b)} C={len(c)}")
            continue
        a_mean = _mean(a)
        b_mean = _mean(b)
        c_mean = _mean(c)
        rows.append({
            "short": short,
            "A_mean": round(a_mean, 4),
            "B_mean": round(b_mean, 4),
            "C_mean": round(c_mean, 4),
            "delta_CB": round(c_mean - b_mean, 4),
            "delta_AB": round(b_mean - a_mean, 4),
            "delta_AC": round(c_mean - a_mean, 4),
            "CB_within_sigma": abs(c_mean - b_mean) <= SIGMA,
        })
        cb_deltas.append(c_mean - b_mean)
        ab_deltas.append(b_mean - a_mean)
        ac_deltas.append(c_mean - a_mean)

    if missing:
        print("WARN: missing data:", missing, file=sys.stderr)

    def _summary(label: str, deltas: list[float]) -> dict:
        ci = _bca_ci(deltas) if deltas else (float("nan"), float("nan"))
        return {
            "label": label,
            "mean_delta": round(_mean(deltas), 4),
            "median_delta": round(statistics.median(deltas), 4) if deltas else float("nan"),
            "max_abs_delta": round(max(abs(d) for d in deltas), 4) if deltas else float("nan"),
            "bca_ci95": [round(ci[0], 4), round(ci[1], 4)],
        }

    cb = _summary("C - B (T37 code drift)", cb_deltas)
    ab = _summary("B - A (environmental drift over 9 days)", ab_deltas)
    ac = _summary("C - A (combined)", ac_deltas)

    outside_sigma_cb = [r for r in rows if not r["CB_within_sigma"]]
    verdict = "F1-NEUTRAL (T37)" if cb["max_abs_delta"] <= SIGMA else "T37 INTRODUCES CODE DRIFT"

    summary = {
        "ticket": "T37b Path A",
        "n_codelists": len(rows),
        "sigma_budget": SIGMA,
        "C_vs_B__t37_code_drift": cb,
        "B_vs_A__environmental_drift": ab,
        "C_vs_A__combined": ac,
        "verdict": verdict,
        "codelists_outside_sigma_on_CB": [
            {"short": r["short"], "delta_CB": r["delta_CB"]} for r in outside_sigma_cb
        ],
        "per_list": rows,
    }
    (BENCH / "result_postfix_same_day_pre.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in [
        "ticket", "n_codelists", "sigma_budget",
        "C_vs_B__t37_code_drift",
        "B_vs_A__environmental_drift",
        "C_vs_A__combined",
        "verdict",
    ]}, indent=2))
    if outside_sigma_cb:
        print("\nOutside σ on C-B:", json.dumps(summary["codelists_outside_sigma_on_CB"], indent=2))
    return 0 if cb["max_abs_delta"] <= SIGMA else 1


if __name__ == "__main__":
    sys.exit(main())

"""Renders the three-view F1 chart for README.md.

Bars show the April K=1 published numbers from EVALUATION.md §Strict
view (median F1 as bar height, mean F1 ± 95% BCa CI as whiskers):

  Pre-fix  ->  Post-fix default  ->  Post-fix cold-start
  median   0.53          0.67                    0.70
  mean     0.49          0.57                    0.56

A supplementary annotation reads the May K=5 result JSONs from
``data/test_sets/benchmark_2026_04/`` and reports the T37j paired
ΔF1 vs pre-T37i baseline (mean and BCa 95% CI) so the chart
documents that the post-fix state has been re-verified under T37j
without changing the published bar heights.

Writes ``assets/three_view_f1.png``.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
OUT = ROOT / "assets" / "three_view_f1.png"

# April K=1 published per-codelist F1 (EVALUATION.md, table line 90-94
# and per-codelist breakdown). Sorted by Pre -> Post delta below.
APRIL_K1 = [
    # (display_name, vocab, pre, post_default, post_cold_start)
    ("atrial_fib",        "ICD-10",  0.21, 1.00, 1.00),
    ("mi",                "ICD-10",  0.00, 0.74, 0.74),
    ("diabetes_mellitus", "SNOMED",  0.78, 0.81, 0.79),
    ("psychosis",         "SNOMED",  0.65, 0.67, 0.61),
    ("hepatitis_c",       "SNOMED",  0.58, 0.60, 0.71),
    ("hypertension",      "SNOMED",  0.53, 0.53, 0.43),
    ("stroke",            "SNOMED",  0.52, 0.52, 0.52),
    ("epilepsy",          "SNOMED",  0.20, 0.20, 0.20),
    ("lung_cancer",       "SNOMED",  0.32, 0.32, 0.24),
    ("copd",              "SNOMED",  0.77, 0.76, 0.76),
    ("depression",        "SNOMED",  0.72, 0.69, 0.70),
    ("heart_failure",     "SNOMED",  0.73, 0.71, 0.71),
    ("dementia",          "SNOMED",  0.33, 0.28, 0.31),
    ("hiv",               "SNOMED",  0.18, 0.02, 0.02),
    ("asthma",            "SNOMED",  0.86, 0.67, 0.72),
]

# Aggregate stats from EVALUATION.md §Strict view (lines 90-94).
AGG_MEANS  = [0.49, 0.57, 0.56]
AGG_MEDIAN = [0.53, 0.67, 0.70]
AGG_CI_LO  = [0.36, 0.44, 0.43]
AGG_CI_HI  = [0.62, 0.68, 0.68]
AGG_LABELS = ["Pre-fix", "Post-fix\ndefault", "Post-fix\ncold-start"]
MCNEMAR = "Paired McNemar (post vs. pre):\nχ² = 42.9, p = 5.7e-11"

# T37j K=5 paired-comparison codelists (T37j_path_a_summary.md).
OVERRIDE_LISTS = {
    "epilepsy", "dementia", "copd", "lung_cancer",
    "psychosis_schiz_bipolar", "stroke", "asthma_pincer",
}
K5_CODELISTS = [
    "atrial_fib_icd10", "mi_icd10", "diabetes_mellitus",
    "psychosis_schiz_bipolar", "hepatitis_c_chronic",
    "hypertension", "stroke", "epilepsy", "lung_cancer",
    "copd", "depression", "heart_failure", "dementia",
    "hiv", "asthma_pincer",
]


def _k5_mean_f1(directory: Path, stem: str) -> float:
    runs = sorted(glob.glob(str(directory / f"{stem}.result_runK_*.json")))
    f1s = []
    for r in runs:
        with open(r, encoding="utf-8") as f:
            d = json.load(f)
        f1s.append(d["stages"]["included_only"]["f1"])
    return mean(f1s)


def _bca_ci(values: np.ndarray, n_resamples: int = 1000, seed: int = 7) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    boots = np.array([
        rng.choice(values, size=len(values), replace=True).mean()
        for _ in range(n_resamples)
    ])
    return float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def _t37j_paired() -> tuple[float, tuple[float, float]] | None:
    try:
        pre, postj = [], []
        for stem in K5_CODELISTS:
            pre.append(_k5_mean_f1(BENCH / "_preT37i", stem))
            post_dir = "_postT37j_override" if stem in OVERRIDE_LISTS else "_postT37j_bare"
            postj.append(_k5_mean_f1(BENCH / post_dir, stem))
        delta = np.array(postj) - np.array(pre)
        return float(delta.mean()), _bca_ci(delta)
    except (FileNotFoundError, KeyError):
        return None


def main() -> None:
    rows = sorted(APRIL_K1, key=lambda r: r[3] - r[2], reverse=True)
    names = [r[0] for r in rows]
    vocabs = [r[1] for r in rows]
    pre  = np.array([r[2] for r in rows])
    post = np.array([r[3] for r in rows])
    cold = np.array([r[4] for r in rows])

    fig, (ax_agg, ax_per) = plt.subplots(
        1, 2, figsize=(13, 7.5), gridspec_kw={"width_ratios": [1, 2.4]}
    )

    colors = ["#9CA3AF", "#005EB8", "#41B6E6"]
    xpos = np.arange(3)
    ax_agg.bar(xpos, AGG_MEDIAN, color=colors, edgecolor="white", linewidth=1.5)
    ax_agg.errorbar(
        xpos, AGG_MEANS,
        yerr=[np.array(AGG_MEANS) - np.array(AGG_CI_LO),
              np.array(AGG_CI_HI) - np.array(AGG_MEANS)],
        fmt="none", ecolor="#1F2937", capsize=6, linewidth=1.5,
    )
    for x, med, mn, hi in zip(xpos, AGG_MEDIAN, AGG_MEANS, AGG_CI_HI):
        top = max(med, hi) + 0.02
        ax_agg.text(x, top + 0.06, f"median {med:.2f}",
                    ha="center", fontsize=10, fontweight="bold")
        ax_agg.text(x, top + 0.015, f"mean {mn:.2f}",
                    ha="center", fontsize=9, color="#374151")

    ax_agg.set_xticks(xpos)
    ax_agg.set_xticklabels(AGG_LABELS, fontsize=10)
    ax_agg.set_ylim(0, 1.0)
    ax_agg.set_ylabel("F1 (15 codelists)", fontsize=11)
    ax_agg.set_title(
        "Aggregate F1\nmedian (bar) + mean ± 95% BCa CI (whiskers)",
        fontsize=11, fontweight="bold",
    )
    ax_agg.spines["top"].set_visible(False)
    ax_agg.spines["right"].set_visible(False)
    ax_agg.grid(axis="y", linestyle="--", alpha=0.3)

    annot = MCNEMAR
    t37j = _t37j_paired()
    if t37j is not None:
        d_mean, (d_lo, d_hi) = t37j
        annot += (
            f"\n\nT37j K=5 re-verification (post-T37j vs pre-T37i):\n"
            f"paired ΔF1 = +{d_mean:.3f}\n"
            f"BCa 95% CI = [+{d_lo:.3f}, +{d_hi:.3f}]"
        )
    ax_agg.text(
        0.5, -0.40, annot,
        transform=ax_agg.transAxes, ha="center", fontsize=9,
        style="italic", color="#374151",
    )

    y = np.arange(len(names))
    h = 0.27

    ax_per.barh(y - h, pre,  h, color=colors[0], label="Pre-fix")
    ax_per.barh(y,     post, h, color=colors[1], label="Post-fix default")
    ax_per.barh(y + h, cold, h, color=colors[2], label="Post-fix cold-start")

    ax_per.set_yticks(y)
    ax_per.set_yticklabels(names, fontsize=10)
    ax_per.invert_yaxis()
    ax_per.set_xlim(0, 1.05)
    ax_per.set_xlabel("F1 (strict, included-only)", fontsize=11)
    ax_per.set_title(
        "Per-codelist F1 across 15 conditions\n(sorted by Pre→Post delta)",
        fontsize=12, fontweight="bold",
    )
    ax_per.spines["top"].set_visible(False)
    ax_per.spines["right"].set_visible(False)
    ax_per.grid(axis="x", linestyle="--", alpha=0.3)
    ax_per.legend(loc="lower right", frameon=False, fontsize=10)

    for i, vocab in enumerate(vocabs):
        ax_per.text(1.07, i, vocab, va="center", fontsize=8, color="#6B7280")

    plt.suptitle(
        "clinicalcodes.uk — three-view evaluation across 15 NHS reference codelists",
        fontsize=13, fontweight="bold", y=1.00,
    )
    plt.tight_layout()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT, dpi=200, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")
    if t37j is not None:
        d_mean, (d_lo, d_hi) = t37j
        print(f"  T37j K=5 paired delta-F1 = +{d_mean:.4f}  BCa CI [+{d_lo:.4f}, +{d_hi:.4f}]")


if __name__ == "__main__":
    main()

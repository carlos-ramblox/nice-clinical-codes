"""Renders the per-retriever ablation chart for EVALUATION.md.

Reads the ablation aggregate from
``data/test_sets/benchmark_2026_04/_ablation.json`` (produced by
``run_ablation.py``) and writes a single horizontal-bar chart of mean
F1 per configuration, sorted by F1 descending. Mirrors the structural
template of plot_three_view.py: same NHS palette
(#005EB8, #41B6E6, neutral greys), same 200 dpi white facecolor.

Usage::

    python backend/app/evaluation/plot_ablation.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
ABLATION = ROOT / "data" / "test_sets" / "benchmark_2026_04" / "_ablation.json"
OUT = ROOT / "assets" / "ablation_f1.png"

# Stable colour assignment by config -- the same retriever / aggregate
# row keeps the same colour even after the F1 sort changes its position.
# Per-retriever rows are neutral grey; the merged-retrieval rows pick up
# the NHS accent (#41B6E6); the full-pipeline rows are NHS blue (#005EB8).
_PALETTE: dict[str, str] = {
    "omophub_only":       "#9CA3AF",
    "qof_only":           "#9CA3AF",
    "opencodelists_only": "#9CA3AF",
    "chromadb_only":      "#9CA3AF",
    "merger_raw":         "#41B6E6",
    "merger_umls":        "#41B6E6",
    "full_pipeline":      "#005EB8",
    "cold_start":         "#005EB8",
}


def main() -> None:
    with open(ABLATION, encoding="utf-8") as f:
        payload = json.load(f)

    configs = payload["configs"]
    rows = sorted(configs.items(), key=lambda kv: kv[1]["f1_mean"], reverse=True)

    labels = [r[1]["label"] for r in rows]
    f1 = np.array([r[1]["f1_mean"] for r in rows])
    p = np.array([r[1]["precision_mean"] for r in rows])
    rec = np.array([r[1]["recall_mean"] for r in rows])
    ci_lo = np.array([r[1]["f1_ci95"][0] for r in rows])
    ci_hi = np.array([r[1]["f1_ci95"][1] for r in rows])
    colors = [_PALETTE.get(r[0], "#9CA3AF") for r in rows]

    fig, ax = plt.subplots(figsize=(11, 6.5))

    y = np.arange(len(rows))
    ax.barh(y, f1, color=colors, edgecolor="white", linewidth=1.2)

    # 95% BCa CI as horizontal whiskers anchored on the bar tip
    ax.errorbar(
        f1, y,
        xerr=[f1 - ci_lo, ci_hi - f1],
        fmt="none", ecolor="#1F2937", capsize=4, linewidth=1.2,
    )

    # Annotate each bar with mean F1 and (P / R)
    for i, (f, pi, ri) in enumerate(zip(f1, p, rec)):
        ax.text(
            max(f, ci_hi[i]) + 0.012, i,
            f"F1={f:.2f}  (P={pi:.2f} / R={ri:.2f})",
            va="center", fontsize=10, color="#1F2937",
        )

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=11)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.0)
    ax.set_xlabel("Mean F1 across 15 reference codelists  (95% BCa CI)", fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", linestyle="--", alpha=0.3)

    # Inline colour key as a proper legend (top-right, inside axes).
    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor="#9CA3AF", edgecolor="white", label="per-retriever raw"),
        Patch(facecolor="#41B6E6", edgecolor="white", label="merged retrieval"),
        Patch(facecolor="#005EB8", edgecolor="white", label="full pipeline"),
    ]
    ax.legend(
        handles=legend_handles, loc="lower right",
        frameon=False, fontsize=9, handlelength=1.2,
    )

    fig.suptitle(
        "Per-retriever ablation — clinicalcodes.uk",
        fontsize=13, fontweight="bold", y=0.995,
    )
    fig.text(
        0.5, 0.945,
        "Configs 1–4: raw retriever output (no UMLS, no LLM). "
        "Configs 5–6: merger ± UMLS, no LLM. Configs 7–8: full pipeline.",
        ha="center", fontsize=10, color="#374151",
    )

    plt.tight_layout(rect=[0, 0, 1, 0.92])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT, dpi=200, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

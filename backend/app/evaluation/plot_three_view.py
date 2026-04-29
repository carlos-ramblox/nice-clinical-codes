"""
Renders the three-view F1 chart for README.md from the numbers in
EVALUATION.md. Outputs assets/three_view_f1.png.
"""
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

# Per-codelist F1 (EVALUATION.md, lines 269-285).
# Sorted by Pre→Post delta so the headline movers are at the top.
codelists = [
    # (name, vocab, pre, post_default, cold)
    ("atrial_fib",        "ICD-10",   0.21, 1.00, 1.00),
    ("mi",                "ICD-10",   0.00, 0.74, 0.74),
    ("diabetes_mellitus", "SNOMED",   0.78, 0.81, 0.79),
    ("psychosis",         "SNOMED",   0.65, 0.67, 0.61),
    ("hepatitis_c",       "SNOMED",   0.58, 0.60, 0.71),
    ("hypertension",      "SNOMED",   0.53, 0.53, 0.43),
    ("stroke",            "SNOMED",   0.52, 0.52, 0.52),
    ("epilepsy",          "SNOMED",   0.20, 0.20, 0.20),
    ("lung_cancer",       "SNOMED",   0.32, 0.32, 0.24),
    ("copd",              "SNOMED",   0.77, 0.76, 0.76),
    ("depression",        "SNOMED",   0.72, 0.69, 0.70),
    ("heart_failure",     "SNOMED",   0.73, 0.71, 0.71),
    ("dementia",          "SNOMED",   0.33, 0.28, 0.31),
    ("hiv",               "SNOMED",   0.18, 0.02, 0.02),
    ("asthma",            "SNOMED",   0.86, 0.67, 0.72),
]
names      = [c[0] for c in codelists]
pre        = np.array([c[2] for c in codelists])
post_def   = np.array([c[3] for c in codelists])
cold       = np.array([c[4] for c in codelists])

# Aggregate (EVALUATION.md, line 19-21, post-fix rows).
agg_means  = [0.49, 0.57, 0.56]
agg_ci_lo  = [0.36, 0.44, 0.43]
agg_ci_hi  = [0.62, 0.68, 0.68]
agg_labels = ["Pre-fix", "Post-fix\ndefault", "Post-fix\ncold-start"]

fig, (ax_agg, ax_per) = plt.subplots(
    1, 2, figsize=(13, 6.5), gridspec_kw={"width_ratios": [1, 2.4]}
)

# ---- Left panel: aggregate means with 95% BCa CI ----
colors = ["#9CA3AF", "#005EB8", "#41B6E6"]   # neutral / NHS blue / accent
xpos = np.arange(3)
ax_agg.bar(xpos, agg_means, color=colors, edgecolor="white", linewidth=1.5)
ax_agg.errorbar(
    xpos, agg_means,
    yerr=[np.array(agg_means) - np.array(agg_ci_lo),
          np.array(agg_ci_hi) - np.array(agg_means)],
    fmt="none", ecolor="#1F2937", capsize=6, linewidth=1.5,
)
for x, m in zip(xpos, agg_means):
    ax_agg.text(x, m + 0.025, f"{m:.2f}", ha="center", fontsize=11, fontweight="bold")

ax_agg.set_xticks(xpos)
ax_agg.set_xticklabels(agg_labels, fontsize=10)
ax_agg.set_ylim(0, 0.85)
ax_agg.set_ylabel("Mean F1 (15 codelists)", fontsize=11)
ax_agg.set_title("Aggregate F1\nmean ± 95% BCa CI", fontsize=12, fontweight="bold")
ax_agg.spines["top"].set_visible(False)
ax_agg.spines["right"].set_visible(False)
ax_agg.grid(axis="y", linestyle="--", alpha=0.3)

# Annotate McNemar p-value
ax_agg.text(
    0.5, -0.22,
    "Paired McNemar (post vs. pre):\nχ² = 42.9, p = 5.7e-11",
    transform=ax_agg.transAxes, ha="center", fontsize=9,
    style="italic", color="#374151",
)

# ---- Right panel: per-codelist grouped bars ----
y = np.arange(len(names))
h = 0.27

ax_per.barh(y - h, pre,      h, color=colors[0], label="Pre-fix")
ax_per.barh(y,     post_def, h, color=colors[1], label="Post-fix default")
ax_per.barh(y + h, cold,     h, color=colors[2], label="Post-fix cold-start")

ax_per.set_yticks(y)
ax_per.set_yticklabels(names, fontsize=10)
ax_per.invert_yaxis()
ax_per.set_xlim(0, 1.05)
ax_per.set_xlabel("F1 (strict, included-only)", fontsize=11)
ax_per.set_title("Per-codelist F1 across 15 conditions\n(sorted by Pre→Post delta)",
                 fontsize=12, fontweight="bold")
ax_per.spines["top"].set_visible(False)
ax_per.spines["right"].set_visible(False)
ax_per.grid(axis="x", linestyle="--", alpha=0.3)
ax_per.legend(loc="lower right", frameon=False, fontsize=10)

# Vocabulary tag column on the right edge
for i, (_, vocab, *_) in enumerate(codelists):
    ax_per.text(1.07, i, vocab, va="center", fontsize=8, color="#6B7280")

plt.suptitle(
    "clinicalcodes.uk — three-view evaluation across 15 NHS reference codelists",
    fontsize=13, fontweight="bold", y=1.02,
)
plt.tight_layout()

out = Path(__file__).resolve().parents[3] / "assets" / "three_view_f1.png"
out.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(out, dpi=200, bbox_inches="tight", facecolor="white")
print(f"wrote {out}")

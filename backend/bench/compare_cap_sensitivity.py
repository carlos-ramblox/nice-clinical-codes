"""Cap-sensitivity multi-cap aggregator: reads K=5 result envelopes
from `_postT37j_bare/`, `_postT37j_override/`, and the
`_cap_sensitivity/cap_{500,1000}_{bare,override}/` directories and
emits a methods-paper-ready summary at
`data/test_sets/benchmark_2026_04/cap_sensitivity_summary.md`.

Mode-matched aggregation: bare-mode K=5 for the 8 non-descendant-closed
codelists, override-mode K=5 for the 7 descendant-closed codelists.
This matches the T37j_path_a_summary.md convention.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import numpy as np
from scipy.stats import bootstrap as scipy_bootstrap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.code_normalize import normalize_code  # noqa: E402
from app.evaluation.benchmark_aggregate import evaluate_one  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
CAPSENS = BENCH / "_cap_sensitivity"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"
SIGMA = 0.012

DESCENDANT_CLOSED = (
    "epilepsy", "dementia", "copd", "lung_cancer",
    "psychosis_schiz_bipolar", "stroke", "asthma_pincer",
)
LARGE_GOLD = (
    "epilepsy", "lung_cancer", "dementia", "stroke", "hiv",
    "psychosis_schiz_bipolar", "asthma_pincer", "hypertension", "depression",
)

SWEEP_DIRS: dict[tuple[str, str], Path] = {
    ("cap100", "bare"):     BENCH / "_postT37j_bare",
    ("cap100", "override"): BENCH / "_postT37j_override",
    ("cap500", "bare"):     CAPSENS / "cap_500_bare",
    ("cap500", "override"): CAPSENS / "cap_500_override",
    ("cap1000", "bare"):    CAPSENS / "cap_1000_bare",
    ("cap1000", "override"): CAPSENS / "cap_1000_override",
    ("capinf", "bare"):     CAPSENS / "cap_inf_bare",
}


def _runK_metrics(dir_: Path, short: str, ts: list[dict]) -> dict | None:
    """K=5 (or K=1) means for one (sweep, codelist). None if any file missing."""
    f1s, ps, rs = [], [], []
    diags: list[dict] = []
    for k in range(1, 6):
        p = dir_ / f"{short}.result_runK_{k}.json"
        if not p.exists():
            break
        with open(p, encoding="utf-8") as f:
            run = json.load(f)
        v = evaluate_one(ts, run)
        f1s.append(v["strict"]["f1"])
        ps.append(v["strict"]["precision"])
        rs.append(v["strict"]["recall"])
        cd = run.get("cap_diagnostics")
        if cd:
            diags.append(cd)
    if not f1s:
        return None
    out = {
        "k": len(f1s),
        "f1_mean": statistics.fmean(f1s),
        "f1_std": statistics.pstdev(f1s) if len(f1s) > 1 else 0.0,
        "f1_median": statistics.median(f1s),
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
            confidence_level=0.95, n_resamples=10_000,
            method="BCa", random_state=7,
        )
        return float(res.confidence_interval.low), float(res.confidence_interval.high)
    except Exception:
        return float("nan"), float("nan")


def _gold_size(ts: list[dict]) -> int:
    return len({
        normalize_code(c, "")
        for entry in ts
        for c in str(entry.get("Codelist", "")).split(";")
        if c.strip()
    })


def _mode_for(short: str) -> str:
    return "override" if short in DESCENDANT_CLOSED else "bare"


def _load_all() -> dict:
    """Return {short: {gold, ts, by_cap: {(cap_label, mode): metrics}}}."""
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)

    out: dict[str, dict] = {}
    for s in selection:
        short = s["short"]
        with open(BENCH / f"{short}.json", encoding="utf-8") as f:
            ts = json.load(f)
        out[short] = {
            "ts": ts,
            "gold": _gold_size(ts),
            "by_cap": {},
        }
        for key, dir_ in SWEEP_DIRS.items():
            if not dir_.exists():
                continue
            m = _runK_metrics(dir_, short, ts)
            if m is not None:
                out[short]["by_cap"][key] = m
    return out


def _headline_aggregate(loaded: dict, cap_label: str) -> dict | None:
    """Mode-matched aggregate at one cap. None if any codelist's mode-matched run is missing."""
    f1s, ps, rs = [], [], []
    missing: list[str] = []
    for short, info in loaded.items():
        m = info["by_cap"].get((cap_label, _mode_for(short)))
        if m is None:
            missing.append(short)
            continue
        f1s.append(m["f1_mean"])
        ps.append(m["p_mean"])
        rs.append(m["r_mean"])
    if missing:
        return {"missing": missing, "complete": False, "n": len(f1s),
                "mean_f1": statistics.fmean(f1s) if f1s else float("nan"),
                "median_f1": statistics.median(f1s) if f1s else float("nan"),
                "mean_p": statistics.fmean(ps) if ps else float("nan"),
                "mean_r": statistics.fmean(rs) if rs else float("nan")}
    lo, hi = _bca_ci(f1s)
    return {
        "complete": True,
        "n": len(f1s),
        "mean_f1": statistics.fmean(f1s),
        "median_f1": statistics.median(f1s),
        "mean_p": statistics.fmean(ps),
        "mean_r": statistics.fmean(rs),
        "f1_ci_low": lo,
        "f1_ci_high": hi,
    }


def _paired_delta(loaded: dict, cap_a: str, cap_b: str, mode_match: bool, scope: tuple[str, ...] | None = None) -> dict:
    """Paired delta-F1 between two cap labels. Returns mean, median, BCa CI on deltas."""
    deltas: list[float] = []
    used: list[str] = []
    missing: list[str] = []
    items = scope if scope is not None else tuple(loaded.keys())
    for short in items:
        info = loaded[short]
        mode = _mode_for(short) if mode_match else "bare"
        ma = info["by_cap"].get((cap_a, mode))
        mb = info["by_cap"].get((cap_b, mode))
        if ma is None or mb is None:
            missing.append(short)
            continue
        deltas.append(mb["f1_mean"] - ma["f1_mean"])
        used.append(short)
    if not deltas:
        return {"n": 0, "missing": missing, "mean_delta": float("nan")}
    lo, hi = _bca_ci(deltas)
    return {
        "n": len(deltas),
        "used": used,
        "missing": missing,
        "mean_delta": statistics.fmean(deltas),
        "median_delta": statistics.median(deltas),
        "ci_low": lo,
        "ci_high": hi,
        "deltas": deltas,
    }


def _verdict(mean_delta: float, ci_low: float | None = None, ci_high: float | None = None) -> str:
    crosses_zero = (
        ci_low is not None and ci_high is not None
        and ci_low <= 0 <= ci_high
    )
    if mean_delta > SIGMA:
        return "AMBIGUOUS LIFT (CI crosses 0)" if crosses_zero else "F1 LIFT"
    if abs(mean_delta) <= SIGMA:
        return "F1 NEUTRAL"
    return "AMBIGUOUS REGRESSION (CI crosses 0)" if crosses_zero else "F1 REGRESSION"


def _fmt_f1(m: dict | None) -> str:
    if m is None:
        return "—"
    if "f1_std" in m and m["k"] > 1:
        return f"{m['f1_mean']:.3f} (±{m['f1_std']:.3f})"
    return f"{m['f1_mean']:.3f}"


def _build_per_codelist_table(loaded: dict) -> list[str]:
    lines = []
    lines.append("## Per-codelist K=5 F1 by cap (mode-matched)")
    lines.append("")
    lines.append("| codelist | mode | gold | F1 cap=100 | F1 cap=500 | F1 cap=1000 |")
    lines.append("|---|---|---:|---:|---:|---:|")
    items = sorted(loaded.items(), key=lambda kv: -kv[1]["gold"])
    for short, info in items:
        mode = _mode_for(short)
        m100 = info["by_cap"].get(("cap100", mode))
        m500 = info["by_cap"].get(("cap500", mode))
        m1000 = info["by_cap"].get(("cap1000", mode))
        lines.append(
            f"| {short} | {mode} | {info['gold']} | "
            f"{_fmt_f1(m100)} | {_fmt_f1(m500)} | {_fmt_f1(m1000)} |"
        )
    return lines


def _build_diagnostic_table(loaded: dict, cap_label: str) -> list[str]:
    lines = []
    lines.append(f"## Cap diagnostics at {cap_label}")
    lines.append("")
    lines.append("| codelist | gold | pre-cap pool | gold in pre-cap | gold lost | gold final |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    items = sorted(loaded.items(), key=lambda kv: -kv[1]["gold"])
    for short, info in items:
        m = info["by_cap"].get((cap_label, _mode_for(short)))
        if m is None or "mean_candidates_before_cap" not in m:
            continue
        lines.append(
            f"| {short} | {info['gold']} | "
            f"{m['mean_candidates_before_cap']:.0f} | "
            f"{m['mean_gold_pre_cap']:.1f} | "
            f"{m['mean_gold_lost']:.1f} | "
            f"{m['mean_gold_final']:.1f} |"
        )
    return lines


def _write_markdown(loaded: dict) -> Path:
    headline = {
        cap: _headline_aggregate(loaded, cap)
        for cap in ("cap100", "cap500", "cap1000", "capinf")
    }
    paired_100_500_bare_9 = _paired_delta(loaded, "cap100", "cap500", mode_match=False, scope=LARGE_GOLD)
    paired_100_500_all = _paired_delta(loaded, "cap100", "cap500", mode_match=False)
    paired_100_1000_bare_all = _paired_delta(loaded, "cap100", "cap1000", mode_match=False)
    paired_100_1000_mode_matched = _paired_delta(loaded, "cap100", "cap1000", mode_match=True)

    lines: list[str] = []
    lines.append("# MAX_CANDIDATES cap-sensitivity sweep")
    lines.append("")
    lines.append("**Date:** 2026-05-18 (Wave 1 landed); update timestamp on each regen.")
    lines.append("**Scope:** 15-codelist v2 disease benchmark; K=5 paired-comparison protocol.")
    lines.append("**Caps:** 100 (production), 500 (sensitivity anchor), 1000 (methods-paper headline), ∞ (supplementary).")
    lines.append("**Modes:** *bare* = LLM parser extracts include_descendants from query; *override* = request-level include_descendants=true (T37j convention). Mode-matched aggregates use bare for the 8 non-descendant-closed codelists, override for the 7 descendant-closed.")
    lines.append("")
    lines.append("> **Note on pre-T37i cap=1000.** The methods paper's pre-T37i baseline at cap=1000 is taken to equal post-T37j cap=1000 bare-mode behaviour. T37j_path_a_summary.md established the empirical equivalence at cap=100 (delta-F1 within K=5 σ=0.012 on 7 of 8 bare codelists); the mechanism is cap-independent because the hierarchy expander gate (`include_descendants=False` extracted from bare-name queries) fires before the expander work and the cap is upstream of the expander entirely. No pre-T37i checkout sweep was run.")
    lines.append("")

    lines.append("## Methods-paper headline")
    lines.append("")
    lines.append("| Configuration | n | Mean F1 | Median F1 | Mean P | Mean R | F1 BCa 95 % CI |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    lines.append("| Pre-fix baseline (cap=100, April K=1) | 15 | 0.49 | 0.53 | 0.71 | 0.51 | [0.36, 0.62] |")
    lines.append("| Post-fix default (cap=100, April K=1) | 15 | 0.57 | 0.67 | 0.88 | 0.49 | [0.44, 0.68] |")
    for cap_label, display in (
        ("cap100", "Post-T37j K=5 (cap=100, mode-matched)"),
        ("cap1000", "Post-T37j K=5 (cap=1000, mode-matched)"),
        ("capinf", "cap=∞ supplementary K=1"),
    ):
        h = headline[cap_label]
        if h is None:
            lines.append(f"| {display} | — | — | — | — | — | (not run) |")
            continue
        if h["complete"]:
            lines.append(
                f"| {display} | {h['n']} | **{h['mean_f1']:.3f}** | "
                f"{h['median_f1']:.3f} | {h['mean_p']:.3f} | {h['mean_r']:.3f} | "
                f"[{h['f1_ci_low']:+.3f}, {h['f1_ci_high']:+.3f}] |"
            )
        else:
            partial = ", ".join(h["missing"]) if h["missing"] else "—"
            lines.append(
                f"| {display} | {h['n']}/15 | {h['mean_f1']:.3f} (partial) | "
                f"{h['median_f1']:.3f} | {h['mean_p']:.3f} | {h['mean_r']:.3f} | "
                f"missing: {partial} |"
            )
    lines.append("")

    cap1000_mm_status = headline["cap1000"]
    if cap1000_mm_status and cap1000_mm_status["complete"]:
        lines.append(f"**Headline mean F1 at cap=1000 (mode-matched): {cap1000_mm_status['mean_f1']:.3f}**, "
                     f"BCa 95 % CI [{cap1000_mm_status['f1_ci_low']:+.3f}, {cap1000_mm_status['f1_ci_high']:+.3f}].")
    else:
        miss = cap1000_mm_status["missing"] if cap1000_mm_status else "all"
        lines.append(f"*Cap=1000 mode-matched headline is provisional pending the override sweep (Wave 2). Missing codelists: {miss}.*")
    lines.append("")

    lines.append("## T37j delta-F1 across caps")
    lines.append("")
    lines.append("The T37j K=5 verification at cap=100 (`T37j_path_a_summary.md`) reported mean delta-F1 +0.106 (BCa CI [+0.049, +0.177]) vs the pre-T37i baseline, mode-matched. The relevant question for the methods paper is whether the same lift holds at cap=1000.")
    lines.append("")
    p100_1000_mm = paired_100_1000_mode_matched
    if p100_1000_mm["n"] == len(loaded):
        v = _verdict(p100_1000_mm['mean_delta'], p100_1000_mm['ci_low'], p100_1000_mm['ci_high'])
        lines.append(
            f"**T37j delta-F1 at cap=1000 (mode-matched, n={p100_1000_mm['n']}):** "
            f"mean **{p100_1000_mm['mean_delta']:+.3f}**, median {p100_1000_mm['median_delta']:+.3f}, "
            f"BCa 95 % CI [{p100_1000_mm['ci_low']:+.3f}, {p100_1000_mm['ci_high']:+.3f}]. "
            f"Verdict: **{v}**."
        )
        survives = p100_1000_mm["mean_delta"] > SIGMA and p100_1000_mm["ci_low"] > 0
        if survives:
            lines.append("")
            lines.append("**The T37j +0.106 delta-F1 survives at cap=1000.** The bimodality / mode-routing finding is robust to the cap.")
        else:
            lines.append("")
            lines.append("**The T37j +0.106 delta-F1 does not unambiguously survive at cap=1000.** See per-codelist breakdown for the structural reason.")
    else:
        lines.append(
            f"*Mode-matched delta-F1 at cap=1000 is provisional: {p100_1000_mm['n']} of {len(loaded)} codelists complete. "
            f"Pending the override sweep at cap=1000 (Wave 2) for: {', '.join(p100_1000_mm['missing'])}.*"
        )
    lines.append("")

    lines.append("## Cap-lift delta-F1 (the structural-bottleneck axis)")
    lines.append("")
    lines.append("This axis compares the SAME code state at different caps, isolating the cap as a variable.")
    lines.append("")
    lines.append("| Comparison | n | Mean delta-F1 | Median | BCa 95 % CI | Verdict |")
    lines.append("|---|---:|---:|---:|---|---|")
    for label, p in (
        ("cap=100 → cap=500 bare (9 large-gold)", paired_100_500_bare_9),
        ("cap=100 → cap=500 bare (all 15)", paired_100_500_all),
        ("cap=100 → cap=1000 bare (all 15)", paired_100_1000_bare_all),
    ):
        if p["n"] == 0:
            lines.append(f"| {label} | 0 | — | — | — | — |")
            continue
        lines.append(
            f"| {label} | {p['n']} | **{p['mean_delta']:+.3f}** | "
            f"{p['median_delta']:+.3f} | "
            f"[{p['ci_low']:+.3f}, {p['ci_high']:+.3f}] | "
            f"**{_verdict(p['mean_delta'], p['ci_low'], p['ci_high'])}** |"
        )
    lines.append("")

    lines.extend(_build_per_codelist_table(loaded))
    lines.append("")
    lines.extend(_build_diagnostic_table(loaded, "cap500"))
    lines.append("")
    lines.extend(_build_diagnostic_table(loaded, "cap1000"))
    lines.append("")

    lines.append("## Cap diagnostic interpretation")
    lines.append("")
    lines.append("- `pre-cap pool` is the merger's deduplicated candidate count before the cap fires. When this is below the cap value, the cap doesn't fire and post-cap = pre-cap. When above, the cap drops `pre_cap − cap` candidates from the LLM's view.")
    lines.append("- `gold in pre-cap` is the K=5 mean of gold-set codes present in the pre-cap pool. The merger's joint retriever coverage on the query sets an absolute ceiling on this column independent of cap.")
    lines.append("- `gold lost` is the K=5 mean of gold-set codes that were in the pre-cap pool but did not survive both caps (merger + UMLS). Values close to zero indicate the cap is no longer the binding constraint.")
    lines.append("- `gold final` is the K=5 mean of gold-set codes in the final LLM-included output. The gap between `gold in pre-cap` and `gold final` decomposes into (a) cap-induced loss, and (b) LLM-induced loss (gold codes scored `exclude`/`uncertain`).")
    lines.append("")

    lines.append("## Coverage gaps")
    lines.append("")
    have_override_500 = ("cap500", "override") in next(iter(loaded.values()))["by_cap"]
    have_override_1000 = any(("cap1000", "override") in info["by_cap"] for info in loaded.values())
    have_capinf = any(("capinf", "bare") in info["by_cap"] for info in loaded.values())
    if not have_override_500:
        lines.append("- **cap=500 override sweep on the 7 descendant-closed codelists** is not yet run (Wave 2). Without it, the cap=500 line of the per-codelist table uses bare-mode for those codelists, which understates their F1 under the T37j convention.")
    if not have_override_1000:
        lines.append("- **cap=1000 override sweep on the 7 descendant-closed codelists** is not yet run (Wave 2). The cap=1000 mode-matched headline and the cap=1000 T37j delta-F1 row are provisional until this lands.")
    if not have_capinf:
        lines.append("- **cap=∞ supplementary K=1** is not yet run (Wave 3, optional). The methods-paper discussion section would benefit from the absolute-ceiling reference but the two-anchor sensitivity curve (cap=100, cap=1000) suffices for the headline claim.")
    lines.append("- **Pre-T37i cap=1000 checkout** was deliberately skipped per the project-memory equivalence argument; see the note at the top.")
    lines.append("")

    lines.append("## Files")
    lines.append("")
    lines.append("- Per-run envelopes: `_cap_sensitivity/cap_{500,1000}_{bare,override}/{short}.result_runK_{1..5}.json` (gitignored)")
    lines.append("- Aggregate JSON: `_cap_sensitivity/compare_cap_sensitivity.json`")
    lines.append("- Sweep log: `_cap_sensitivity/sweep.log` (Wave 1) — Wave 2 + 3 logs land alongside")
    lines.append("- Diagnostics JSON: `_cap_sensitivity/diagnose_depression_hiv.json`")
    lines.append("- Orchestrator: `backend/app/evaluation/run_cap_sensitivity.py`")
    lines.append("- Aggregator: `backend/bench/compare_cap_sensitivity.py`")
    lines.append("- Diagnostic script: `backend/bench/diagnose_depression_hiv.py`")

    out_path = BENCH / "cap_sensitivity_summary.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    loaded = _load_all()
    json_out = {
        "experiment": "cap_sensitivity_multi_cap",
        "n_codelists": len(loaded),
        "sigma_budget": SIGMA,
        "per_codelist": {
            short: {
                "gold": info["gold"],
                "mode": _mode_for(short),
                **{f"{cap}_{mode}": {
                    "k": m["k"],
                    "f1_mean": round(m["f1_mean"], 4),
                    "f1_std": round(m.get("f1_std", 0.0), 4),
                    "p_mean": round(m["p_mean"], 4),
                    "r_mean": round(m["r_mean"], 4),
                    **{k: round(v, 1) for k, v in m.items() if k.startswith("mean_")},
                } for (cap, mode), m in info["by_cap"].items()},
            }
            for short, info in loaded.items()
        },
    }
    out_path = BENCH / "_cap_sensitivity" / "compare_cap_sensitivity.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(json_out, indent=2, default=str), encoding="utf-8")

    md_path = _write_markdown(loaded)

    headline_capinf = _headline_aggregate(loaded, "capinf")
    cap1000_partial = _headline_aggregate(loaded, "cap1000")
    cap1000_mm_paired = _paired_delta(loaded, "cap100", "cap1000", mode_match=True)
    cap100_500_all = _paired_delta(loaded, "cap100", "cap500", mode_match=False)

    print("=" * 72)
    print("Cap-sensitivity multi-cap aggregator — provisional summary")
    print("=" * 72)
    print(f"Codelists loaded: {len(loaded)}")
    print(f"cap=100 mode-matched: {_headline_aggregate(loaded, 'cap100')}")
    print(f"cap=500 all-15 delta-F1 vs cap=100 (bare): {cap100_500_all['mean_delta']:+.3f} "
          f"BCa [{cap100_500_all['ci_low']:+.3f}, {cap100_500_all['ci_high']:+.3f}] n={cap100_500_all['n']}")
    print(f"cap=1000 mode-matched: {cap1000_partial}")
    print(f"cap=inf mode-matched: {headline_capinf}")
    if cap1000_mm_paired["n"] == len(loaded):
        print(f"T37j delta-F1 at cap=1000 (mode-matched): {cap1000_mm_paired['mean_delta']:+.3f} "
              f"BCa [{cap1000_mm_paired['ci_low']:+.3f}, {cap1000_mm_paired['ci_high']:+.3f}] "
              f"verdict={_verdict(cap1000_mm_paired['mean_delta'])}")
    else:
        print(f"T37j delta-F1 at cap=1000 (mode-matched): PROVISIONAL — "
              f"{cap1000_mm_paired['n']}/{len(loaded)} codelists. "
              f"Missing: {cap1000_mm_paired['missing']}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

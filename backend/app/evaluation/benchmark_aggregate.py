"""
benchmark_aggregate.py

Aggregates per-codelist /api/evaluate responses into the cross-codelist
metrics reported in EVALUATION.md.

Why this exists separately from evaluator.py:
- evaluator.py runs inside the live API and produces a metrics block
  per request. This script reads the saved per-codelist responses,
  recomputes metrics with the same code normalization (so numbers
  agree), and adds cross-codelist views the live evaluator does not
  compute: aggregate mean/median/IQR/95% CI, stratified breakdowns
  by vocabulary / condition area / reference size, and a
  "vocabulary-filtered" companion view used to characterise multi-
  vocabulary output behaviour. Both code paths apply the same
  ``code.strip().replace(".", "")`` rule (see ``normalize_code`` and
  ``evaluator._norm``).

Usage:
    python -m app.evaluation.benchmark_aggregate

Reads:
    data/test_sets/benchmark_2026_04/<short>.json          (test sets)
    data/test_sets/benchmark_2026_04/<short>.result.json   (raw API output)
    data/raw/opencodelists/selection.json                  (selection metadata)

Writes:
    data/test_sets/benchmark_2026_04/_aggregate.json
    data/test_sets/benchmark_2026_04/_per_list.csv
"""
from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

import numpy as np
from scipy.stats import bootstrap as scipy_bootstrap
from statsmodels.stats.contingency_tables import mcnemar

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
SELECTION = ROOT / "data" / "raw" / "opencodelists" / "selection.json"


def normalize_code(code: str, vocabulary: str) -> str:
    """Code normalization for fair set comparison.

    Strips whitespace and all dots, vocabulary-blind. The same
    transformation is applied to both reference and output codes, so
    OPCS-4 codes that carry dots (like "K40.1") are mutated
    symmetrically — set membership is preserved either way. SNOMED CT
    has no dots so the strip is a no-op.

    This matches ``evaluator._norm`` exactly so the live
    ``/api/evaluate`` and the offline aggregator agree on every
    metric. Earlier divergence (vocab-aware vs. vocab-blind dot
    stripping) caused OPCS-4 codes to map differently between the
    two paths; the rule is now uniform.

    The ``vocabulary`` parameter is retained for API compatibility
    and may be used by future per-vocabulary normalization rules,
    but is currently ignored.
    """
    return (code or "").strip().replace(".", "")


def metrics(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return p, r, f1


def _vocab_match(out_vocab: str, ref_vocab: str) -> bool:
    """Loose vocabulary equivalence — handles ICD-10 (WHO) vs ICD-10."""
    o = (out_vocab or "").lower()
    r = (ref_vocab or "").lower()
    if "icd" in o and "icd" in r:
        return True
    if "snomed" in o and "snomed" in r:
        return True
    return o == r


def _compute(ref_lookup_norm: dict, out_lookup_norm: dict) -> dict:
    ref_set = set(ref_lookup_norm)
    out_set = set(out_lookup_norm)
    tp = ref_set & out_set
    fp = out_set - ref_set
    fn = ref_set - out_set
    p, r, f1 = metrics(len(tp), len(fp), len(fn))
    return {
        "precision": round(p, 4),
        "recall": round(r, 4),
        "f1": round(f1, 4),
        "tp_count": len(tp),
        "fp_count": len(fp),
        "fn_count": len(fn),
        "n_ref": len(ref_set),
        "n_out": len(out_set),
        "tp_codes": [{"code": ref_lookup_norm[k][0], "term": ref_lookup_norm[k][1]} for k in sorted(tp)],
        "fp_codes": [{"code": out_lookup_norm[k].get("code"), "term": out_lookup_norm[k].get("term", ""),
                      "rationale": out_lookup_norm[k].get("rationale", "")} for k in sorted(fp)],
        "fn_codes": [{"code": ref_lookup_norm[k][0], "term": ref_lookup_norm[k][1]} for k in sorted(fn)],
    }


def evaluate_one(test_set: list[dict], result: dict) -> dict:
    """Recompute included-only metrics with proper code normalization.

    Returns two views:
      strict: every included code counts as a candidate.
      vocab_filtered: only included codes whose vocabulary matches the
        reference list's declared vocabulary count. This isolates concept
        recall from multi-vocabulary output behaviour.
    """
    entry = test_set[0]
    ref_codes_raw = str(entry.get("Codelist", "")).split(";")
    ref_terms_raw = str(entry.get("Codelist_terms", "")).split(";")
    vocab = entry.get("Codelist_vocabulary", "")

    ref_codes = [c.strip() for c in ref_codes_raw if c.strip()]
    ref_terms = [t.strip() for t in ref_terms_raw if t.strip()]
    ref_lookup_norm = {}
    for i, code in enumerate(ref_codes):
        nc = normalize_code(code, vocab)
        term = ref_terms[i] if i < len(ref_terms) else ""
        ref_lookup_norm[nc] = (code, term)

    scored = result.get("scored_codes", [])
    included = [c for c in scored if c.get("decision") == "include"]

    out_lookup_strict = {}
    out_lookup_filtered = {}
    for c in included:
        out_v = c.get("vocabulary", vocab)
        nc = normalize_code(c.get("code", ""), out_v)
        out_lookup_strict[nc] = c
        if _vocab_match(out_v, vocab):
            out_lookup_filtered[nc] = c

    strict = _compute(ref_lookup_norm, out_lookup_strict)
    filtered = _compute(ref_lookup_norm, out_lookup_filtered)
    return {"strict": strict, "vocab_filtered": filtered}


def bootstrap_ci(values: list[float], n_resamples: int = 1000, seed: int = 7,
                 method: str = "BCa") -> tuple[float, float]:
    """95 % CI on the mean via SciPy's bootstrap.

    Default method is BCa (bias-corrected and accelerated; Efron 1987),
    which adjusts both the bias and the skewness of the bootstrap
    sampling distribution and typically produces tighter, less biased
    intervals than the percentile method on small samples (n=15 here).

    Falls back to a degenerate point interval when the sample has zero
    variance — BCa requires non-zero variance to compute the
    acceleration term via the jackknife.
    """
    if not values:
        return 0.0, 0.0
    arr = np.asarray(values, dtype=float)
    if len(arr) < 2 or float(arr.std(ddof=1)) == 0.0:
        v = round(float(arr[0]) if len(arr) else 0.0, 4)
        return v, v
    rng = np.random.default_rng(seed)
    res = scipy_bootstrap(
        (arr,),
        np.mean,
        n_resamples=n_resamples,
        method=method,
        confidence_level=0.95,
        rng=rng,
    )
    lo = float(res.confidence_interval.low)
    hi = float(res.confidence_interval.high)
    return round(lo, 4), round(hi, 4)


def _mcnemar_pre_post(pre_view: dict | None, post_view: dict | None) -> dict | None:
    """McNemar's test on per-code paired (pre-fix, post-fix) correctness.

    For every (codelist, code) pair where the code appears in either the
    reference list or the included output of either run, we record:
      truth        = code is in the reference list
      pre_correct  = (code in pre-fix output) == truth
      post_correct = (code in post-fix output) == truth
    The 2x2 contingency over (pre_correct, post_correct) feeds McNemar's
    test. Codes correctly handled by both runs (concordant "both right")
    drop into the diagonal and don't affect the test statistic; only the
    discordant pairs (b: pre right / post wrong; c: pre wrong /
    post right) carry information about the change.

    Reports both the chi-squared form with continuity correction
    (Edwards 1948) and the binomial-exact form, choosing the exact form
    when ``b + c < 25`` (where the chi-squared approximation is poor).
    """
    if pre_view is None or post_view is None:
        return None

    pre_lookup = {r["short"]: r for r in pre_view["per_list"]}
    post_lookup = {r["short"]: r for r in post_view["per_list"]}

    a = b = c = d = 0  # both right / pre right post wrong / pre wrong post right / both wrong
    per_list_breakdown: list[dict] = []

    def _norm_codes(items: list[dict]) -> set[str]:
        return {normalize_code(it["code"], "") for it in items}

    for short, pre_row in pre_lookup.items():
        post_row = post_lookup.get(short)
        if post_row is None:
            continue

        # Reference is fixed by the test-set file, so pre and post must
        # produce the same gold standard. Assert it; silently unioning
        # would turn a schema-divergence bug into mis-attributed
        # regressions on the McNemar contingency.
        ref_pre = _norm_codes(pre_row["tp_codes"]) | _norm_codes(pre_row["fn_codes"])
        ref_post = _norm_codes(post_row["tp_codes"]) | _norm_codes(post_row["fn_codes"])
        if ref_pre != ref_post:
            raise ValueError(
                f"Reference set diverges between pre and post for codelist "
                f"{short!r}: |pre|={len(ref_pre)}, |post|={len(ref_post)}, "
                f"symmetric difference={len(ref_pre ^ ref_post)}. "
                f"Check that both result files were scored against the same test-set."
            )
        ref = ref_pre

        out_pre = _norm_codes(pre_row["tp_codes"]) | _norm_codes(pre_row["fp_codes"])
        out_post = _norm_codes(post_row["tp_codes"]) | _norm_codes(post_row["fp_codes"])
        universe = ref | out_pre | out_post

        list_b = list_c = 0
        for code in universe:
            truth = code in ref
            pre_correct = (code in out_pre) == truth
            post_correct = (code in out_post) == truth
            if pre_correct and post_correct:
                a += 1
            elif pre_correct and not post_correct:
                b += 1
                list_b += 1
            elif post_correct and not pre_correct:
                c += 1
                list_c += 1
            else:
                d += 1

        per_list_breakdown.append({
            "short": short,
            "regressions_b": list_b,
            "improvements_c": list_c,
            "net_c_minus_b": list_c - list_b,
        })

    if b + c == 0:
        return {
            "n_pairs_compared": a + b + c + d,
            "concordant_a_both_right": a,
            "concordant_d_both_wrong": d,
            "discordant_b_pre_right_post_wrong": b,
            "discordant_c_pre_wrong_post_right": c,
            "test": "no discordant pairs",
            "statistic": 0.0,
            "pvalue": 1.0,
            "per_list": per_list_breakdown,
        }

    table = np.array([[a, b], [c, d]])
    use_exact = (b + c) < 25
    res = mcnemar(table, exact=use_exact, correction=not use_exact)

    return {
        "n_pairs_compared": a + b + c + d,
        "concordant_a_both_right": a,
        "concordant_d_both_wrong": d,
        "discordant_b_pre_right_post_wrong": b,
        "discordant_c_pre_wrong_post_right": c,
        "test": "binomial exact" if use_exact else "chi-squared with continuity correction",
        "statistic": round(float(res.statistic), 4),
        "pvalue": float(res.pvalue),
        "per_list": per_list_breakdown,
    }


def iqr(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    s = sorted(values)
    n = len(s)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    return round(q1, 4), round(q3, 4)


def _build_view(selection: list[dict], suffix: str) -> dict | None:
    """Read per-codelist results with the given filename suffix, recompute
    metrics, and return a view dict with per_list, aggregate, by_vocab,
    by_area, by_size. Returns ``None`` if no result files for this view
    exist (so callers can skip cleanly when only one of the three runs
    has happened).

    suffix examples: ``"result"`` (pre-fix baseline), ``"result_postfix"``
    (post-fix default), ``"result_coldstart"`` (post-fix cold-start).
    """
    rows = []
    for s in selection:
        with open(BENCH / f"{s['short']}.json", encoding="utf-8") as f:
            ts = json.load(f)
        res_path = BENCH / f"{s['short']}.{suffix}.json"
        if not res_path.exists():
            continue
        with open(res_path, encoding="utf-8") as f:
            result = json.load(f)
        if "scored_codes" not in result:
            continue
        both = evaluate_one(ts, result)
        m = both["strict"]
        mf = both["vocab_filtered"]
        rows.append({
            "short": s["short"],
            "name": s["name"],
            "vocabulary": ts[0].get("Codelist_vocabulary", ""),
            "area": s["area"],
            "organisation": s["version"].split("/")[0],
            "n_ref": m["n_ref"],
            "n_out": m["n_out"],
            "tp": m["tp_count"],
            "fp": m["fp_count"],
            "fn": m["fn_count"],
            "precision": m["precision"],
            "recall": m["recall"],
            "f1": m["f1"],
            "n_out_filtered": mf["n_out"],
            "tp_filtered": mf["tp_count"],
            "fp_filtered": mf["fp_count"],
            "fn_filtered": mf["fn_count"],
            "precision_filtered": mf["precision"],
            "recall_filtered": mf["recall"],
            "f1_filtered": mf["f1"],
            "elapsed_seconds": result.get("elapsed_seconds"),
            "tp_codes": m["tp_codes"],
            "fp_codes": m["fp_codes"],
            "fn_codes": m["fn_codes"],
            "fp_codes_filtered": mf["fp_codes"],
        })

    if not rows:
        return None

    def agg(field):
        vals = [r[field] for r in rows]
        return {
            "mean": round(statistics.mean(vals), 4),
            "median": round(statistics.median(vals), 4),
            "iqr": iqr(vals),
            "ci95": bootstrap_ci(vals),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
        }

    aggregate = {
        "n": len(rows),
        "strict": {
            "precision": agg("precision"),
            "recall": agg("recall"),
            "f1": agg("f1"),
        },
        "vocab_filtered": {
            "precision": agg("precision_filtered"),
            "recall": agg("recall_filtered"),
            "f1": agg("f1_filtered"),
        },
    }

    def strata(key):
        groups = {}
        for r in rows:
            groups.setdefault(r[key], []).append(r)
        out = {}
        for k, grp in groups.items():
            ps = [g["precision"] for g in grp]
            rs = [g["recall"] for g in grp]
            fs = [g["f1"] for g in grp]
            out[k] = {
                "n": len(grp),
                "precision_mean": round(statistics.mean(ps), 4),
                "recall_mean": round(statistics.mean(rs), 4),
                "f1_mean": round(statistics.mean(fs), 4),
            }
        return out

    size_groups = {"small (<50)": [], "medium (50-200)": [], "large (>200)": []}
    for r in rows:
        if r["n_ref"] < 50:
            size_groups["small (<50)"].append(r)
        elif r["n_ref"] <= 200:
            size_groups["medium (50-200)"].append(r)
        else:
            size_groups["large (>200)"].append(r)
    by_size = {}
    for k, grp in size_groups.items():
        if not grp:
            continue
        by_size[k] = {
            "n": len(grp),
            "precision_mean": round(statistics.mean(g["precision"] for g in grp), 4),
            "recall_mean": round(statistics.mean(g["recall"] for g in grp), 4),
            "f1_mean": round(statistics.mean(g["f1"] for g in grp), 4),
        }

    return {
        "aggregate": aggregate,
        "by_vocabulary": strata("vocabulary"),
        "by_condition_area": strata("area"),
        "by_reference_size": by_size,
        "per_list": rows,
    }


def _write_legacy_outputs(view: dict | None) -> None:
    """Preserve the original v1 output files (_aggregate.json,
    _per_list.csv) so anything that consumed them still works."""
    if view is None:
        return
    (BENCH / "_aggregate.json").write_text(
        json.dumps(view, indent=2), encoding="utf-8"
    )
    fields = ["short", "name", "vocabulary", "area", "organisation",
              "n_ref", "n_out", "tp", "fp", "fn", "precision", "recall", "f1",
              "n_out_filtered", "tp_filtered", "fp_filtered", "fn_filtered",
              "precision_filtered", "recall_filtered", "f1_filtered",
              "elapsed_seconds"]
    with open(BENCH / "_per_list.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in view["per_list"]:
            w.writerow({k: r[k] for k in fields})


def _write_v2_outputs(views: dict[str, dict], mcnemar_results: dict | None = None) -> None:
    """Write the three-view aggregate JSON and a wide per-list CSV with
    pre/post/cold columns side-by-side."""
    payload: dict = {name: view for name, view in views.items() if view is not None}
    if mcnemar_results:
        payload["mcnemar"] = {k: v for k, v in mcnemar_results.items() if v is not None}
    (BENCH / "_aggregate_v2.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Build wide CSV: one row per codelist with columns for each view.
    by_short: dict[str, dict] = {}
    view_order = ["pre_fix", "post_fix", "coldstart"]
    for name in view_order:
        view = views.get(name)
        if view is None:
            continue
        for r in view["per_list"]:
            short = r["short"]
            if short not in by_short:
                by_short[short] = {
                    "short": short,
                    "name": r["name"],
                    "vocabulary": r["vocabulary"],
                    "area": r["area"],
                    "organisation": r["organisation"],
                    "n_ref": r["n_ref"],
                }
            for metric in ("precision", "recall", "f1", "tp", "fp", "fn", "n_out"):
                by_short[short][f"{name}_{metric}"] = r[metric]

    base_fields = ["short", "name", "vocabulary", "area", "organisation", "n_ref"]
    metric_fields = []
    for name in view_order:
        if views.get(name) is None:
            continue
        for metric in ("precision", "recall", "f1", "tp", "fp", "fn", "n_out"):
            metric_fields.append(f"{name}_{metric}")

    with open(BENCH / "_per_list_v2.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=base_fields + metric_fields)
        w.writeheader()
        for short, row in sorted(by_short.items()):
            w.writerow({k: row.get(k, "") for k in base_fields + metric_fields})


def main():
    with open(SELECTION, encoding="utf-8") as f:
        selection = json.load(f)

    views = {
        "pre_fix":   _build_view(selection, "result"),
        "post_fix":  _build_view(selection, "result_postfix"),
        "coldstart": _build_view(selection, "result_coldstart"),
    }

    # Paired McNemar's tests on per-code (pre, post) correctness — the
    # principled paired-comparison test for the headline pre→post lift.
    # Overlapping CIs is a known-conservative substitute for this
    # (Schenker & Gentleman, 2001), so we report McNemar alongside.
    mcnemar_results = {
        "post_fix_vs_pre_fix": _mcnemar_pre_post(views["pre_fix"], views["post_fix"]),
        "coldstart_vs_pre_fix": _mcnemar_pre_post(views["pre_fix"], views["coldstart"]),
    }

    # Legacy v1 outputs continue to reflect the pre-fix baseline so the
    # original EVALUATION.md numbers stay reproducible from the same files.
    _write_legacy_outputs(views["pre_fix"])
    _write_v2_outputs(views, mcnemar_results)

    # Concise stdout summary so callers can eyeball headline deltas.
    for name, view in views.items():
        if view is None:
            print(f"{name}: <no result files>")
            continue
        agg = view["aggregate"]["strict"]
        print(f"{name:10s}  n={view['aggregate']['n']:2d}  "
              f"P_mean={agg['precision']['mean']:.3f}  "
              f"R_mean={agg['recall']['mean']:.3f}  "
              f"F1_mean={agg['f1']['mean']:.3f}  "
              f"F1_med={agg['f1']['median']:.3f}  "
              f"F1_BCa95=[{agg['f1']['ci95'][0]:.3f},{agg['f1']['ci95'][1]:.3f}]")

    for label, mc in mcnemar_results.items():
        if mc is None:
            continue
        print(
            f"\nMcNemar ({label}): n_pairs={mc['n_pairs_compared']}  "
            f"a={mc['concordant_a_both_right']} d={mc['concordant_d_both_wrong']} "
            f"b={mc['discordant_b_pre_right_post_wrong']} c={mc['discordant_c_pre_wrong_post_right']}\n"
            f"  test={mc['test']}  statistic={mc['statistic']}  p={mc['pvalue']:.4g}"
        )


if __name__ == "__main__":
    main()

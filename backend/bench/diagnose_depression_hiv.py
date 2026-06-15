"""Diagnostic analysis on the cap-sensitivity sweep JSONs.

Two analyses, both run on existing on-disk JSON envelopes (no new
pipeline runs, no OMOPHub calls):

1. depression FP analysis — for the −0.123 ΔF1 regression at cap=500
   in bare mode. Identifies the FPs that appear at cap=500 but not
   cap=100 and groups them by keyword bucket so the LIMITATIONS.md
   write-up can name the precision failure mode.

2. HIV retriever-bound analysis — the gold list has 243 codes; only ~9
   surface at any cap. Catalogues the missing 234 by category so the
   LIMITATIONS.md write-up can name what's actually missing.

Both produce JSON + markdown-ready prose printed to stdout.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "data" / "test_sets" / "benchmark_2026_04"
CAP100_DIR = BENCH / "_postT37j_bare"
CAP500_DIR = BENCH / "_cap_sensitivity" / "cap_500_bare"


def _read_k5_included(dir_: Path, short: str) -> list[dict]:
    """Return list of (k, scored_codes) for K=1..5."""
    runs = []
    for k in range(1, 6):
        p = dir_ / f"{short}.result_runK_{k}.json"
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
        runs.append(j)
    return runs


_DEPRESSION_BUCKETS: list[tuple[str, re.Pattern]] = [
    ("perinatal / postnatal", re.compile(r"\b(perinatal|postnatal|postpartum|maternal)\b", re.I)),
    ("reactive / situational", re.compile(r"\b(reactive|situational|adjustment)\b", re.I)),
    ("personality / chronic", re.compile(r"\b(personality|dysthymi|persistent|chronic)\b", re.I)),
    ("bipolar / manic", re.compile(r"\b(bipolar|manic|mania|mixed)\b", re.I)),
    ("anxiety / mixed", re.compile(r"\banxiety|mixed anxiety", re.I)),
    ("seasonal", re.compile(r"\bseasonal\b", re.I)),
    ("psychotic features", re.compile(r"\bpsychotic\b", re.I)),
    ("ICD-10 cross-vocab", re.compile(r"^F\d", re.I)),
    ("severity qualifier only", re.compile(r"^(mild|moderate|severe)\b", re.I)),
]


def _bucket_depression(code: str, term: str) -> str:
    for label, pattern in _DEPRESSION_BUCKETS:
        if pattern.search(term) or pattern.search(code):
            return label
    return "other depression-adjacent"


def diagnose_depression() -> dict:
    cap100 = _read_k5_included(CAP100_DIR, "depression")
    cap500 = _read_k5_included(CAP500_DIR, "depression")
    if not cap100 or not cap500:
        return {"error": "missing K=5 depression JSONs"}

    k5_fp_counts: list[int] = []
    k5_tp_counts: list[int] = []
    cap500_fps_pooled: Counter = Counter()
    cap500_fp_terms: dict[str, str] = {}

    for run in cap500:
        inc = run["stages"]["included_only"]
        k5_fp_counts.append(inc["fp_count"])
        k5_tp_counts.append(inc["tp_count"])
        for fp in inc["fp_codes"]:
            key = fp["code"]
            cap500_fps_pooled[key] += 1
            cap500_fp_terms[key] = fp["term"]

    cap100_fp_codes: set[str] = set()
    for run in cap100:
        for fp in run["stages"]["included_only"]["fp_codes"]:
            cap100_fp_codes.add(fp["code"])

    new_fps = [c for c in cap500_fps_pooled if c not in cap100_fp_codes]
    buckets: Counter = Counter()
    for c in new_fps:
        buckets[_bucket_depression(c, cap500_fp_terms.get(c, ""))] += 1

    return {
        "codelist": "depression",
        "k5_fp_mean": statistics.fmean(k5_fp_counts),
        "k5_fp_min": min(k5_fp_counts),
        "k5_fp_max": max(k5_fp_counts),
        "k5_tp_mean": statistics.fmean(k5_tp_counts),
        "cap100_fp_codes_unique": len(cap100_fp_codes),
        "cap500_fp_codes_pooled_unique": len(cap500_fps_pooled),
        "new_fps_cap500_only": len(new_fps),
        "bucket_breakdown": dict(buckets.most_common()),
        "sample_new_fps": [
            {"code": c, "term": cap500_fp_terms.get(c, ""),
             "bucket": _bucket_depression(c, cap500_fp_terms.get(c, ""))}
            for c in sorted(new_fps, key=lambda x: -cap500_fps_pooled[x])[:20]
        ],
    }


_HIV_BUCKETS: list[tuple[str, re.Pattern]] = [
    ("AIDS-defining illness", re.compile(r"\b(AIDS|acquired immune deficiency)\b", re.I)),
    ("opportunistic infection", re.compile(r"\b(pneumocystis|cryptococc|toxoplasm|mycobacter|candidiasis|cryptosporid|histoplasm|coccidioid|tuberculosis)\b", re.I)),
    ("Kaposi / lymphoma / cancer", re.compile(r"\b(kaposi|lymphoma|cervical cancer|sarcoma|carcinoma)\b", re.I)),
    ("HIV-associated condition", re.compile(r"\b(HIV|human immunodeficiency virus|retroviral)\b", re.I)),
    ("encephalopathy / neuro", re.compile(r"\b(encephalopathy|dementia|neuropath|leukoencephalopathy|myelopathy)\b", re.I)),
    ("wasting / systemic", re.compile(r"\b(wasting|cachexia|failure to thrive)\b", re.I)),
    ("perinatal / vertical transmission", re.compile(r"\b(perinatal|maternal|congenital|vertical)\b", re.I)),
    ("screening / monitoring", re.compile(r"\b(screen|monitor|viral load|CD4|test)\b", re.I)),
]


def _bucket_hiv(term: str) -> str:
    for label, pattern in _HIV_BUCKETS:
        if pattern.search(term):
            return label
    return "other"


def diagnose_hiv() -> dict:
    cap500 = _read_k5_included(CAP500_DIR, "hiv")
    if not cap500:
        return {"error": "missing K=5 hiv JSONs at cap=500"}

    run0 = cap500[0]
    inc = run0["stages"]["included_only"]
    fn_codes = inc["fn_codes"]
    tp_codes = inc["tp_codes"]
    fp_codes = inc["fp_codes"]

    cd = run0.get("cap_diagnostics") or {}
    pre_cap_pool = cd.get("candidates_before_cap")
    gold_in_pre_cap = cd.get("gold_codes_retrieved_before_cap")

    fn_buckets: Counter = Counter()
    for fn in fn_codes:
        fn_buckets[_bucket_hiv(fn["term"])] += 1

    return {
        "codelist": "hiv",
        "gold_size": len(fn_codes) + len(tp_codes),
        "tp_count": len(tp_codes),
        "fn_count": len(fn_codes),
        "fp_count": len(fp_codes),
        "pre_cap_pool_at_500": pre_cap_pool,
        "gold_in_pre_cap_pool_at_500": gold_in_pre_cap,
        "tp_codes_surfaced": [
            {"code": c["code"], "term": c["term"]} for c in tp_codes
        ],
        "fn_bucket_breakdown": dict(fn_buckets.most_common()),
        "sample_fn_terms": [
            {"code": fn["code"], "term": fn["term"], "bucket": _bucket_hiv(fn["term"])}
            for fn in fn_codes[:30]
        ],
    }


def main() -> int:
    out = {
        "depression": diagnose_depression(),
        "hiv": diagnose_hiv(),
    }
    out_path = BENCH / "_cap_sensitivity" / "diagnose_depression_hiv.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("=" * 72)
    print("DEPRESSION FP analysis (cap=500 bare K=5)")
    print("=" * 72)
    d = out["depression"]
    print(f"K=5 mean FP: {d['k5_fp_mean']:.1f} (range {d['k5_fp_min']}-{d['k5_fp_max']})")
    print(f"K=5 mean TP: {d['k5_tp_mean']:.1f}")
    print(f"FPs at cap=100 (pooled): {d['cap100_fp_codes_unique']}")
    print(f"FPs at cap=500 (pooled): {d['cap500_fp_codes_pooled_unique']}")
    print(f"FPs unique to cap=500: {d['new_fps_cap500_only']}")
    print("Bucket breakdown of cap=500-only FPs:")
    for bucket, n in d["bucket_breakdown"].items():
        print(f"  {bucket:<35} {n:>3}")

    print()
    print("=" * 72)
    print("HIV retriever-bound analysis (cap=500 bare K=1)")
    print("=" * 72)
    h = out["hiv"]
    print(f"Gold size: {h['gold_size']}")
    print(f"TP: {h['tp_count']}  FN: {h['fn_count']}  FP: {h['fp_count']}")
    print(f"Pre-cap pool at cap=500: {h['pre_cap_pool_at_500']} "
          f"(of which {h['gold_in_pre_cap_pool_at_500']} are gold)")
    print("TP codes that DID surface:")
    for tp in h["tp_codes_surfaced"]:
        print(f"  {tp['code']:<20} {tp['term']}")
    print()
    print(f"FN bucket breakdown ({h['fn_count']} codes total):")
    for bucket, n in h["fn_bucket_breakdown"].items():
        print(f"  {bucket:<40} {n:>3}")

    print()
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

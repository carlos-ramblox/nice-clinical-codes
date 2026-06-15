"""Wood-8 diagnostic: why OMOPHub contributes 0 codes to the merged pool.

Production probes (``_planning/api_probes/SUMMARY.md``) showed OMOPHub in
``sources_queried`` but 0 codes in the final source distribution on all
three queries, including Type 2 diabetes where it should surface dozens
of standard concepts.

This script traces a single seed concept end-to-end and pinpoints where
the codes are lost:

  stage 0  raw ``client.search.bulk_basic`` call  -> response shape
  stage 1  ``search_omophub`` DataFrame           -> row count
  stage 2  ``omophub_to_retrieved_codes``         -> RetrievedCode count
  stage 3  ``merge_and_dedup``                     -> codes reaching merger

Run from ``backend/``::

    python -m bench.diagnose_omophub
    python -m bench.diagnose_omophub --insecure   # local TLS-broken env only

``--insecure`` disables TLS verification for environments whose system
trust store can't validate api.omophub.com (corporate MITM proxies);
never use it in production. The root cause this script documents is
shape-parsing, independent of TLS.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

SEED = "Type 2 diabetes mellitus"


def _maybe_disable_tls(insecure: bool) -> None:
    if not insecure:
        return
    import httpx

    _orig = httpx.Client
    httpx.Client = lambda **kw: _orig(**{**kw, "verify": False})  # type: ignore[assignment]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", default=SEED, help="seed concept to probe")
    ap.add_argument("--insecure", action="store_true",
                    help="disable TLS verification (local TLS-broken envs only)")
    args = ap.parse_args()
    _maybe_disable_tls(args.insecure)

    import logging
    logging.disable(logging.CRITICAL)

    from app.config import OMOPHUB_API_KEY, OMOPHUB_VOCABULARIES
    from omophub import OMOPHub
    from app.graph.nodes.omophub_retriever import (
        search_omophub,
        omophub_to_retrieved_codes,
    )
    from app.graph.nodes.result_merger import merge_and_dedup

    if not OMOPHUB_API_KEY:
        print("OMOPHUB_API_KEY not set; cannot probe.")
        return 2

    vocabs = {k: OMOPHUB_VOCABULARIES[k] for k in ("SNOMED", "ICD10")}
    bar = "=" * 72

    # --- stage 0: raw SDK response shape ------------------------------------
    print(bar)
    print(f"STAGE 0  raw bulk_basic response shape  (seed: {args.seed!r})")
    print(bar)
    client = OMOPHub(api_key=OMOPHUB_API_KEY)
    searches = [
        {"search_id": f"{vid}::{args.seed}", "query": args.seed,
         "vocabulary_ids": [vid], "page_size": 20}
        for vid in vocabs
    ]
    try:
        raw = client.search.bulk_basic(searches)
    except Exception as exc:  # noqa: BLE001 - diagnostic surfaces any failure
        print(f"  bulk_basic raised {type(exc).__name__}: {exc}")
        print("  -> in production this is swallowed by search_omophub's "
              "except->fallback path; the fallback shares the same client "
              "and fails identically, so OMOPHub returns 0 with only a "
              "logger.warning. (If this is a local TLS error, retry with "
              "--insecure to reach the real shape-parsing root cause.)")
        return 1

    print(f"  return type: {type(raw).__name__}")
    if isinstance(raw, list):
        total = sum(len(it.get("results", []) or []) for it in raw)
        statuses = Counter(it.get("status") for it in raw)
        print(f"  shape: LIST of {len(raw)} search items "
              f"(SDK unwrapped the API's data: [...] envelope)")
        print(f"  per-item statuses: {dict(statuses)}")
        print(f"  total concepts across items: {total}")
        # Reproduce the pre-fix parser branch to show the silent drop.
        pre_fix_items = raw.get("results", []) if isinstance(raw, dict) else []
        print(f"  pre-fix parser ('isinstance dict else []') would see "
              f"{len(pre_fix_items)} items  <-- THE BUG")
    elif isinstance(raw, dict):
        print(f"  shape: DICT with keys {list(raw.keys())}")
        print(f"  items via .get('results'): {len(raw.get('results', []))}")
    else:
        print(f"  unexpected shape: {raw!r:.200}")

    # --- stage 1: search_omophub DataFrame ----------------------------------
    print()
    print(bar)
    print("STAGE 1  search_omophub() DataFrame")
    print(bar)
    df = search_omophub(args.seed, vocabularies=vocabs, page_size=20)
    print(f"  rows: {len(df)}")
    if len(df):
        print(f"  by vocab: {dict(Counter(df['_vocabulary_label']))}")

    # --- stage 2: RetrievedCode dicts ---------------------------------------
    print()
    print(bar)
    print("STAGE 2  omophub_to_retrieved_codes()")
    print(bar)
    codes = omophub_to_retrieved_codes(df)
    print(f"  RetrievedCode dicts: {len(codes)}")
    for c in codes[:5]:
        print(f"    {c['code']:<18} {c['vocabulary']:<14} {c['term'][:40]}")

    # --- stage 3: what reaches the merger -----------------------------------
    print()
    print(bar)
    print("STAGE 3  merge_and_dedup() with OMOPHub-only input")
    print(bar)
    merged = merge_and_dedup({"retrieved_codes": codes, "parsed_conditions": []})
    enriched = merged.get("enriched_codes", [])
    omophub_sourced = sum(1 for e in enriched if "OMOPHub" in e.get("sources", []))
    print(f"  enriched_codes: {len(enriched)}")
    print(f"  of which OMOPHub-sourced: {omophub_sourced}")

    print()
    print(bar)
    verdict = "PASS - OMOPHub codes flow" if codes else "FAIL - OMOPHub returns 0"
    print(f"VERDICT: {verdict}")
    print(bar)
    return 0 if codes else 1


if __name__ == "__main__":
    sys.exit(main())

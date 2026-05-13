"""T37 e2e smoke: drug query surfaces dm+d/BNF + dmd_level; disease query doesn't.

Run: cd backend && python -m bench.smoke_t37_e2e
"""
from __future__ import annotations

import sys
from collections import Counter

import truststore  # Avast TLS-interception workaround (see verify_t37 probe)
truststore.inject_into_ssl()

import httpx  # noqa: E402

BASE_URL = "http://localhost:8000"
SEARCH = f"{BASE_URL}/api/search"

DRUG_QUERY = "metformin"
DISEASE_QUERY = "type 2 diabetes"

DMD_TAG = "OpenCodelists (dm+d)"
BNF_TAG = "OpenCodelists (BNF)"
VALID_LEVELS = {"Ingredient", "VTM", "VMP", "AMP"}


def _search(query: str) -> dict:
    r = httpx.post(SEARCH, json={"query": query}, timeout=120)
    r.raise_for_status()
    return r.json()


def _assert(cond: bool, msg: str, failures: list[str]) -> None:
    flag = "ok  " if cond else "FAIL"
    print(f"  [{flag}] {msg}")
    if not cond:
        failures.append(msg)


def _search_isolated(query: str) -> dict:
    """Hit /api/search with the disease retrievers disabled, so only the
    dm+d and BNF retrievers (plus UMLS enrichment) reach the merger.
    Isolates the T37 surface from the source_count-cap displacement
    that masks dm+d rows when SNOMED candidates dominate (see follow-up
    T37g)."""
    params = (
        "disable_omophub=true&disable_chroma=true"
        "&disable_qof=true&cold_start=true"
    )
    r = httpx.post(f"{SEARCH}?{params}", json={"query": query}, timeout=120)
    r.raise_for_status()
    return r.json()


def check_drug_query_isolated() -> list[str]:
    """Positive case: dm+d/BNF wiring + dmd_level inference end-to-end.

    Run with disease retrievers disabled so the source_count-cap
    displacement doesn't mask the dm+d rows. This validates the T37
    integration surface itself; the cap-displacement issue is a
    separate concern tracked as T37g."""
    print(f"\n>>> Drug query (isolated): {DRUG_QUERY!r}")
    fails: list[str] = []
    resp = _search_isolated(DRUG_QUERY)
    results = resp.get("results", [])
    print(f"  parsed conditions: {[c.get('name') for c in resp.get('conditions_parsed', [])]}")
    print(f"  total candidates : {len(results)}")

    dmd_rows = [r for r in results if DMD_TAG in r.get("sources", [])]
    bnf_rows = [r for r in results if BNF_TAG in r.get("sources", [])]
    dmd_levels = [r["dmd_level"] for r in dmd_rows if r.get("dmd_level")]
    level_dist = Counter(dmd_levels)

    print(f"  dm+d candidates  : {len(dmd_rows)}")
    print(f"  BNF candidates   : {len(bnf_rows)}")
    print(f"  dmd_level dist   : {dict(level_dist)}")

    _assert(len(dmd_rows) > 0,
            f"dm+d retriever produced >=1 candidate (got {len(dmd_rows)})", fails)
    _assert(len(bnf_rows) > 0,
            f"BNF retriever produced >=1 candidate (got {len(bnf_rows)})", fails)
    _assert(len(dmd_levels) > 0,
            f">=1 dm+d candidate carries dmd_level (got {len(dmd_levels)})", fails)
    _assert(all(lv in VALID_LEVELS for lv in dmd_levels),
            f"every dmd_level is one of {sorted(VALID_LEVELS)} "
            f"(got {sorted(set(dmd_levels))})", fails)
    _assert(len(level_dist) >= 2,
            f">=2 distinct dm+d levels exercised (got {sorted(level_dist)})", fails)
    return fails


def check_drug_query_full_pipeline() -> list[str]:
    """Production-mode probe: the per-vocab quota (T37g) must keep dm+d
    and BNF rows in the top-MAX_CANDIDATES even when OMOPHub + ChromaDB
    saturate the SNOMED pool."""
    print(f"\n>>> Drug query (full pipeline, all retrievers): {DRUG_QUERY!r}")
    fails: list[str] = []
    resp = _search(DRUG_QUERY)
    results = resp.get("results", [])
    dmd_rows = [r for r in results if DMD_TAG in r.get("sources", [])]
    bnf_rows = [r for r in results if BNF_TAG in r.get("sources", [])]
    print(f"  total candidates : {len(results)}")
    print(f"  dm+d candidates  : {len(dmd_rows)}")
    print(f"  BNF candidates   : {len(bnf_rows)}")
    _assert(len(dmd_rows) > 0,
            f"dm+d survives the cap on the full pipeline (got {len(dmd_rows)})", fails)
    _assert(len(bnf_rows) > 0,
            f"BNF survives the cap on the full pipeline (got {len(bnf_rows)})", fails)
    return fails


def check_disease_query() -> list[str]:
    print(f"\n>>> Disease query (FR-008 negative case): {DISEASE_QUERY!r}")
    fails: list[str] = []
    resp = _search(DISEASE_QUERY)
    results = resp.get("results", [])
    print(f"  parsed conditions: {[c.get('name') for c in resp.get('conditions_parsed', [])]}")
    print(f"  total candidates : {len(results)}")

    drug_tagged = [r for r in results
                   if DMD_TAG in r.get("sources", []) or BNF_TAG in r.get("sources", [])]
    with_level = [r for r in results if r.get("dmd_level")]

    print(f"  dm+d/BNF rows    : {len(drug_tagged)}")
    print(f"  rows w/ dmd_level: {len(with_level)}")

    _assert(len(drug_tagged) == 0,
            f"no candidate sourced from dm+d/BNF retrievers (got {len(drug_tagged)})", fails)
    _assert(len(with_level) == 0,
            f"no candidate carries a dmd_level value (got {len(with_level)})", fails)
    return fails


def main() -> int:
    print(f"Smoke testing T37 against {BASE_URL}")
    all_fails: list[str] = []
    all_fails += check_drug_query_isolated()
    all_fails += check_drug_query_full_pipeline()
    all_fails += check_disease_query()
    print()
    if all_fails:
        print(f"FAIL: {len(all_fails)} assertion(s) failed:")
        for f in all_fails:
            print(f"  - {f}")
        return 1
    print("PASS: T37 end-to-end smoke checks all green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

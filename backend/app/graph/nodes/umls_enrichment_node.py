"""LangGraph node wrapper for UMLS enrichment."""

import logging
import pandas as pd

from app.config import UMLS_API_KEY, UMLS_EXPAND, MAX_CANDIDATES
from app.graph.nodes.umls_enrichment import UMLSEnricher

logger = logging.getLogger(__name__)


def enrich_with_umls(state: dict) -> dict:
    """
    LangGraph node: expand enriched codes with UMLS synonyms,
    narrower terms, and siblings. Passes through unchanged if
    UMLS_EXPAND is disabled or API key is missing.
    """
    codes = state.get("enriched_codes", [])

    if not UMLS_EXPAND:
        logger.info("UMLS enrichment disabled, passing through")
        return {"candidates_after_umls_cap_count": len(codes)}

    if not UMLS_API_KEY:
        logger.warning("UMLS_API_KEY not set, skipping enrichment")
        return {"candidates_after_umls_cap_count": len(codes)}

    if not codes:
        return {"candidates_after_umls_cap_count": 0}

    # build a DataFrame that UMLSEnricher expects
    rows = []
    for c in codes:
        rows.append({
            "concept_id": c.get("code", ""),
            "concept_name": c.get("term", ""),
            "_query_vocabulary": c.get("vocabulary", ""),
        })

    df = pd.DataFrame(rows)

    try:
        enricher = UMLSEnricher()
        suggestions = enricher.enrich(df)
    except Exception as exc:
        logger.error("UMLS enrichment failed: %s", exc)
        return {"errors": [f"UMLS enrichment failed: {exc}"]}

    if suggestions.empty:
        logger.info("UMLS: no new suggestions found")
        return {"candidates_after_umls_cap_count": len(codes)}

    # add new codes from suggestions back into enriched_codes
    existing_keys = {(c["code"], c["vocabulary"]) for c in codes}
    new_codes = []

    for _, row in suggestions.iterrows():
        # suggestions don't have a SNOMED code directly, but have a name + CUI
        # we add them as "UMLS suggestion" entries for the LLM to evaluate
        suggested_name = row.get("suggested_name", "")
        suggested_cui = row.get("suggested_cui", "")
        suggestion_type = row.get("suggestion_type", "")

        if not suggested_name:
            continue

        key = (suggested_cui, "UMLS")
        if key in existing_keys:
            continue
        existing_keys.add(key)

        new_codes.append({
            "code": suggested_cui,
            "term": suggested_name,
            "vocabulary": "UMLS",
            "source": f"UMLS ({suggestion_type})",
            "sources": [f"UMLS ({suggestion_type})"],
            "source_count": 1,
            "domain": "Condition",
            "similarity_score": None,
            # UMLS suggestions have a CUI, not a SNOMED/ICD-10/OPCS-4
            # code, so the OpenCodeCounts lookup has nothing to match
            # against. Leave the T31 fields as not_in_dataset so the UI
            # shows the same em-dash as a real miss — no false signal.
            "usage_frequency": None,
            "usage_status": "not_in_dataset",
            "usage_source": None,
            "usage_setting": None,
            "concept_id": None,
        })

    if new_codes:
        # UMLSEnricher.enrich() returns suggestions in
        # ThreadPoolExecutor.as_completed order, so without a stable
        # sort here the MAX_CANDIDATES re-cap below would drop a
        # thread-scheduling-dependent subset of suggestions, breaking
        # the temperature=0 reproducibility claim. Sort by
        # (cui, term) before the concat so identical inputs always
        # yield identical batches downstream.
        new_codes.sort(key=lambda c: (c.get("code") or "", c.get("term") or ""))
        updated = codes + new_codes
        # re-cap to MAX_CANDIDATES so LLM scoring stays affordable
        if len(updated) > MAX_CANDIDATES:
            logger.info("UMLS: capping %d codes back to %d", len(updated), MAX_CANDIDATES)
            updated = updated[:MAX_CANDIDATES]
        logger.info("UMLS: added %d new codes (%d total)", len(new_codes), len(updated))
        return {
            "enriched_codes": updated,
            "candidates_after_umls_cap_count": len(updated),
        }

    logger.info("UMLS: all suggestions already in code list")
    return {"candidates_after_umls_cap_count": len(codes)}

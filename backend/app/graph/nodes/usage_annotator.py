"""LangGraph node: stamp NHS Digital usage frequencies onto every
deduped code (T31).

Runs between ``result_merger`` and ``umls_enrichment`` so:

  - the lookup is only paid once per unique (code, vocabulary), not
    once per source-retriever copy of the same code,
  - the post-cap candidate set is fully annotated before LLM scoring
    sees it (so a future scoring prompt can reference usage as a
    signal — currently it doesn't, but the option is open),
  - UMLS-suggested codes added *after* this node correctly stay
    None/None/None (UMLS suggestions don't have a SNOMED code we can
    look up against the primary-care dataset).

The annotator never fails the graph: a missing or unbuilt
``code_usage`` table just leaves every code with
``usage_status="not_in_dataset"`` and the rest of the pipeline runs
exactly as before T31.
"""

from __future__ import annotations

import logging

from app.db.code_usage import lookup as lookup_usage

logger = logging.getLogger(__name__)


def annotate_usage(state: dict) -> dict:
    codes = state.get("enriched_codes", [])
    if not codes:
        return {}

    counted = 0
    withheld = 0
    missing = 0

    for c in codes:
        result = lookup_usage(c.get("vocabulary", ""), c.get("code", ""))
        c["usage_frequency"] = result["usage_frequency"]
        c["usage_status"] = result["usage_status"]
        c["usage_source"] = result["usage_source"]
        c["usage_setting"] = result["usage_setting"]

        status = result["usage_status"]
        if status == "counted":
            counted += 1
        elif status == "withheld_below_5":
            withheld += 1
        else:
            missing += 1

    logger.info(
        "usage_annotator: %d counted, %d withheld, %d not_in_dataset (of %d codes)",
        counted, withheld, missing, len(codes),
    )

    return {"enriched_codes": codes}

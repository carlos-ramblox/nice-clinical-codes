import logging

logger = logging.getLogger(__name__)


def merge_and_dedup(state: dict) -> dict:
    """
    LangGraph node: merge retrieved_codes from all parallel retrievers,
    deduplicate by (code, vocabulary), and tag each code with all sources
    that returned it.
    """
    codes = state.get("retrieved_codes", [])
    if not codes:
        return {"enriched_codes": []}

    # group by (code, vocabulary)
    merged: dict[tuple[str, str], dict] = {}

    for c in codes:
        key = (c["code"], c["vocabulary"])

        if key not in merged:
            merged[key] = {
                "code": c["code"],
                "term": c["term"],
                "vocabulary": c["vocabulary"],
                "source": c["source"],
                "domain": c.get("domain", ""),
                "similarity_score": c.get("similarity_score"),
                "usage_frequency": c.get("usage_frequency"),
                "sources": [c["source"]],
                "source_count": 1,
            }
        else:
            existing = merged[key]
            # add source if not already tracked
            if c["source"] not in existing["sources"]:
                existing["sources"].append(c["source"])
                existing["source_count"] += 1

            # keep the best similarity score
            new_score = c.get("similarity_score")
            if new_score is not None:
                old_score = existing.get("similarity_score")
                if old_score is None or new_score > old_score:
                    existing["similarity_score"] = new_score

            # keep usage frequency if we get one
            if c.get("usage_frequency") and not existing.get("usage_frequency"):
                existing["usage_frequency"] = c["usage_frequency"]

            # prefer longer/more descriptive term
            if len(c.get("term", "")) > len(existing.get("term", "")):
                existing["term"] = c["term"]

    deduped = list(merged.values())

    # sort: more sources first, then by similarity score
    deduped.sort(
        key=lambda x: (x["source_count"], x.get("similarity_score") or 0),
        reverse=True,
    )

    multi_source = sum(1 for d in deduped if d["source_count"] > 1)
    logger.info(
        "Merged %d codes → %d unique (%d from multiple sources)",
        len(codes), len(deduped), multi_source,
    )

    return {"enriched_codes": deduped}

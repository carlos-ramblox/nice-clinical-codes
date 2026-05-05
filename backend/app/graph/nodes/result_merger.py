import logging

from app.config import MAX_CANDIDATES
from app.graph.vocab_matching import requested_vocab_set

logger = logging.getLogger(__name__)


def merge_and_dedup(state: dict) -> dict:
    """
    LangGraph node: merge retrieved_codes from all parallel retrievers,
    deduplicate by (code, vocabulary), and tag each code with all sources
    that returned it.

    When the parsed query pins a single vocabulary (e.g. user typed
    "Myocardial infarction (ICD10)"), candidates whose vocabulary doesn't
    match are filtered out *before* the source_count-based cap. Without
    this filter, single-source candidates in the requested vocabulary
    can be ranked below multi-source candidates in unrequested
    vocabularies and pushed past the cap, surfacing as a downstream
    empty result.
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
                # T31: usage_status / usage_source / usage_setting are
                # populated by the usage_annotator node downstream;
                # retrievers leave them None. Initialise here so
                # downstream nodes (and tests that bypass the
                # annotator) see a stable shape.
                "usage_status": c.get("usage_status"),
                "usage_source": c.get("usage_source"),
                "usage_setting": c.get("usage_setting"),
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

    # Vocabulary-constraint filter (pairs with output_assembly's filter).
    # Only triggers when every parsed condition shares a single
    # coding_system; multi-vocab queries pass through unfiltered.
    conditions = state.get("parsed_conditions", [])
    allowed_vocabs = requested_vocab_set(conditions)
    if allowed_vocabs:
        before = len(deduped)
        deduped = [d for d in deduped if d.get("vocabulary", "") in allowed_vocabs]
        logger.info(
            "Vocabulary constraint: kept %d of %d candidates matching %s "
            "(parsed coding_systems pin a single vocabulary)",
            len(deduped), before, allowed_vocabs,
        )

    # sort: more sources first, then by similarity score
    deduped.sort(
        key=lambda x: (x["source_count"], x.get("similarity_score") or 0),
        reverse=True,
    )

    # cap to top candidates to control LLM scoring cost
    total_unique = len(deduped)
    if total_unique > MAX_CANDIDATES:
        logger.info("Capping %d candidates to top %d", total_unique, MAX_CANDIDATES)
        deduped = deduped[:MAX_CANDIDATES]

    multi_source = sum(1 for d in deduped if d["source_count"] > 1)
    logger.info(
        "Merged %d codes → %d unique → %d after cap (%d from multiple sources)",
        len(codes), total_unique, len(deduped), multi_source,
    )

    return {"enriched_codes": deduped}

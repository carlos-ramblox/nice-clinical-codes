import logging

from app.config import MAX_CANDIDATES, DRUG_VOCAB_QUOTA
from app.graph.vocab_matching import requested_vocab_set

logger = logging.getLogger(__name__)

_DRUG_VOCABS: tuple[str, ...] = ("dm+d", "BNF")


def _stable_sort_key(c: dict) -> tuple:
    """Deterministic tiebreaker per determinism-invariants.md."""
    return (
        -c["source_count"],
        -(c.get("similarity_score") or 0.0),
        c["vocabulary"],
        c["code"],
    )


def _apply_drug_quota(
    deduped: list[dict], parsed_conditions: list[dict], cap: int,
) -> list[dict]:
    """Reserve quota slots per drug vocab when the query has a Drug condition."""
    has_drug = any(c.get("domain") == "Drug" for c in parsed_conditions)
    if not has_drug:
        return sorted(deduped, key=_stable_sort_key)[:cap]

    by_vocab: dict[str, list[dict]] = {}
    for c in deduped:
        by_vocab.setdefault(c["vocabulary"], []).append(c)
    for rows in by_vocab.values():
        rows.sort(key=_stable_sort_key)

    reserved: list[dict] = []
    for vocab in _DRUG_VOCABS:
        reserved.extend(by_vocab.get(vocab, [])[:DRUG_VOCAB_QUOTA])

    other_pool = [c for c in deduped if c["vocabulary"] not in _DRUG_VOCABS]
    other_pool.sort(key=_stable_sort_key)
    drug_pool_remainder = [
        c for vocab in _DRUG_VOCABS for c in by_vocab.get(vocab, [])[DRUG_VOCAB_QUOTA:]
    ]

    headroom = cap - len(reserved)
    fill_pool = sorted(other_pool + drug_pool_remainder, key=_stable_sort_key)
    fill = fill_pool[:headroom] if headroom > 0 else []

    combined = reserved + fill
    return sorted(combined, key=_stable_sort_key)[:cap]


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
                "concept_id": c.get("concept_id"),
                "dmd_level": c.get("dmd_level"),
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

            # upgrade None -> non-None when a later source supplies
            # a concept_id (typically OMOPHub for a code first seen
            # via QOF / OpenCodelists / ChromaDB)
            if existing.get("concept_id") is None and c.get("concept_id") is not None:
                existing["concept_id"] = c["concept_id"]

            if existing.get("dmd_level") is None and c.get("dmd_level") is not None:
                existing["dmd_level"] = c["dmd_level"]

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

    total_unique = len(deduped)
    candidates_pre_cap = [
        {"code": c["code"], "vocabulary": c["vocabulary"]} for c in deduped
    ]
    deduped = _apply_drug_quota(deduped, conditions, MAX_CANDIDATES)
    if total_unique > MAX_CANDIDATES:
        logger.info("Capping %d candidates to top %d", total_unique, MAX_CANDIDATES)

    multi_source = sum(1 for d in deduped if d["source_count"] > 1)
    drug_kept = sum(1 for d in deduped if d["vocabulary"] in _DRUG_VOCABS)
    logger.info(
        "Merged %d codes -> %d unique -> %d after cap "
        "(%d from multiple sources, %d drug-vocab)",
        len(codes), total_unique, len(deduped), multi_source, drug_kept,
    )

    return {
        "enriched_codes": deduped,
        "candidates_pre_cap": candidates_pre_cap,
        "candidates_before_cap_count": total_unique,
        "candidates_after_merger_cap_count": len(deduped),
        "max_candidates_setting": MAX_CANDIDATES,
    }

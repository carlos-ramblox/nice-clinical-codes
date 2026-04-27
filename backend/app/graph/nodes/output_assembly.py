import datetime
import logging

from app.graph.vocab_matching import requested_vocab_set

logger = logging.getLogger(__name__)


def assemble_output(state: dict) -> dict:
    """
    LangGraph node: structure the final output from scored codes.
    Sorts by confidence, builds summary stats and provenance trail.

    When the parsed query pins a single vocabulary (e.g. user typed
    "Myocardial infarction (ICD10)"), output is filtered to that
    vocabulary. Cross-vocabulary equivalents are dropped from the final
    list rather than carried as low-precision noise.

    This filter is not pure belt-and-braces: the merger applies the
    same filter before scoring, but the UMLS enrichment node runs
    *between* the merger and scoring and can introduce codes tagged
    ``vocabulary="UMLS"`` (CUI synonyms, narrower concepts) that
    bypass the merger filter. Without the second pass here, those
    UMLS-tagged codes would surface in the final list when the user
    has explicitly pinned ICD-10, SNOMED, or OPCS-4.
    """
    scored = state.get("scored_codes", [])
    conditions = state.get("parsed_conditions", [])
    run_ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds") + "Z"

    allowed = requested_vocab_set(conditions)
    if allowed:
        before = len(scored)
        scored = [c for c in scored if c.get("vocabulary", "") in allowed]
        logger.info(
            "Vocabulary filter: kept %d of %d scored codes (allowed=%s)",
            len(scored), before, allowed,
        )

    # sort: included first, then by confidence descending
    order = {"include": 0, "uncertain": 1, "exclude": 2}
    final = sorted(
        scored,
        key=lambda x: (order.get(x["decision"], 3), -x.get("confidence", 0)),
    )

    included = [c for c in final if c["decision"] == "include"]
    excluded = [c for c in final if c["decision"] == "exclude"]
    uncertain = [c for c in final if c["decision"] == "uncertain"]

    summary = {
        "total_candidates": len(final),
        "included": len(included),
        "excluded": len(excluded),
        "uncertain": len(uncertain),
        "sources_queried": state.get("sources_queried", []),
    }

    provenance = [
        {
            "code": c["code"],
            "source": ", ".join(c.get("sources", [])),
            "source_url": None,
            "retrieved_at": run_ts,
            "enrichment_path": None,
        }
        for c in final
    ]

    logger.info(
        "Output: %d codes (%d include, %d exclude, %d uncertain)",
        len(final), len(included), len(excluded), len(uncertain),
    )

    return {
        "final_code_list": final,
        "provenance_trail": provenance,
        "summary": summary,
    }

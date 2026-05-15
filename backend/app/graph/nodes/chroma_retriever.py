import logging

from app.config import OMOPHUB_VOCABULARIES, RETRIEVAL_TOP_K
from app.db.code_store import get_concept_id_for
from app.db.vector_store import search

logger = logging.getLogger(__name__)


def retrieve_from_chromadb(state: dict) -> dict:
    """
    LangGraph node: semantic search across ChromaDB for each parsed condition.
    Reads parsed_conditions from state, writes to retrieved_codes.

    Vocabulary filter strings come from :data:`config.OMOPHUB_VOCABULARIES`
    so that ChromaDB and OMOPHub use the same canonical vocabulary names.
    ChromaDB contains SNOMED CT (QOF + OpenCodelists), OPCS-4 (ingest_opcs),
    and ICD-10 5th Edition (ingest_icd10) — all written under the same
    strings OMOPHub uses for its labels, so the system has a local
    fallback when OMOPHub doesn't surface a query.
    """
    conditions = state.get("parsed_conditions", [])
    if not conditions:
        logger.warning("No conditions to search")
        return {"retrieved_codes": [], "sources_queried": []}

    all_codes = []
    for condition in conditions:
        name = condition.get("name", "")
        if not name:
            continue

        systems = condition.get("coding_systems", ["SNOMED", "ICD10"])

        before = len(all_codes)
        for sys_key in systems:
            vocab = OMOPHUB_VOCABULARIES.get(sys_key)
            if vocab is None:
                logger.warning("Unknown coding system '%s', searching unfiltered", sys_key)
            results = search(name, top_k=RETRIEVAL_TOP_K, vocabulary=vocab)
            # Tag source as ChromaDB so the merger can track which retriever
            # found this code. The rank field is the 1-based position within
            # *this* search() call's similarity-descending output — reset
            # per sub-query, not accumulated across (system, query) pairs.
            # The accumulated form (a previous version's bug) made the
            # second coding system's rank-1 hit report as rank 51+ and
            # systematically biased downstream rank-fusion toward whichever
            # system was searched first. The current merger does not yet
            # consume rank, but populating it correctly here keeps the
            # field meaningful for future rank-fusion work — see
            # _planning/T01_rrf_diagnostic.md and the deferred T01.
            for i, r in enumerate(results, start=1):
                r["source"] = "ChromaDB"
                r["rank"] = i
                r["concept_id"] = get_concept_id_for(
                    r.get("vocabulary", ""), r.get("code", "")
                )
                all_codes.append(r)

        logger.info("ChromaDB: '%s' returned %d codes", name, len(all_codes) - before)

    return {
        "retrieved_codes": all_codes,
        "sources_queried": ["ChromaDB"],
    }

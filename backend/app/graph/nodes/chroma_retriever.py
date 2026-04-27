import logging

from app.config import OMOPHUB_VOCABULARIES, RETRIEVAL_TOP_K
from app.db.vector_store import search

logger = logging.getLogger(__name__)


def retrieve_from_chromadb(state: dict) -> dict:
    """
    LangGraph node: semantic search across ChromaDB for each parsed condition.
    Reads parsed_conditions from state, writes to retrieved_codes.

    Vocabulary filter strings come from :data:`config.OMOPHUB_VOCABULARIES`
    so that ChromaDB and OMOPHub use the same canonical vocabulary names.
    Today ChromaDB only contains SNOMED CT (via QOF + OpenCodelists ingest)
    and OPCS-4 (via ingest_opcs); both are written under the same strings
    OMOPHub uses for its labels. No ICD-10 corpus is ingested locally,
    so an ICD-10-only query returns 0 codes from this retriever — the
    filter is still correct so no SNOMED/OPCS rows leak through.
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
            # tag source as ChromaDB so the merger can track which retriever found it
            for r in results:
                r["source"] = "ChromaDB"
            all_codes.extend(results)

        logger.info("ChromaDB: '%s' returned %d codes", name, len(all_codes) - before)

    return {
        "retrieved_codes": all_codes,
        "sources_queried": ["ChromaDB"],
    }

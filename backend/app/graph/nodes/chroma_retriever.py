import logging

from app.config import RETRIEVAL_TOP_K
from app.db.vector_store import search

logger = logging.getLogger(__name__)


def retrieve_from_chromadb(state: dict) -> dict:
    """
    LangGraph node: semantic search across ChromaDB for each parsed condition.
    Reads parsed_conditions from state, writes to retrieved_codes.
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

        # map our short names to ChromaDB vocabulary metadata values
        vocab_map = {"SNOMED": "SNOMED CT", "ICD10": "ICD-10"}

        before = len(all_codes)
        for sys_key in systems:
            vocab = vocab_map.get(sys_key)
            if vocab is None:
                logger.warning("Unknown coding system '%s', searching unfiltered", sys_key)
            results = search(name, top_k=RETRIEVAL_TOP_K, vocabulary=vocab)
            all_codes.extend(results)

        logger.info("ChromaDB: '%s' returned %d codes", name, len(all_codes) - before)

    return {
        "retrieved_codes": all_codes,
        "sources_queried": ["ChromaDB"],
    }

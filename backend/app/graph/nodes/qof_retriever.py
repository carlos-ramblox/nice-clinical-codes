import logging

from app.db.code_store import search_by_condition

logger = logging.getLogger(__name__)


def retrieve_from_qof(state: dict) -> dict:
    """
    LangGraph node: search QOF business rules in SQLite for each parsed condition.
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

        rows = search_by_condition(name)
        for r in rows:
            all_codes.append({
                "code": r["code"],
                "term": r["term"],
                "vocabulary": r["vocabulary"],
                "source": r["source"],
                "domain": r["domain"],
                "similarity_score": None,  # exact match, not semantic
                "usage_frequency": None,
            })

        logger.info("QOF: '%s' returned %d codes", name, len(rows))

    return {
        "retrieved_codes": all_codes,
        "sources_queried": ["QOF Business Rules 2024-25"],
    }

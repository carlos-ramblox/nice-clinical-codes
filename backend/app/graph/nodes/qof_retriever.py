import logging

from app.db.code_store import get_concept_id_for, search_by_condition

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
        # search_by_condition is a SQLite-wide LIKE search across all
        # ingested sources; filter to QOF-only here so we don't surface
        # OpenCodelists / OPCS-4 / ICD-10 rows under a "QOF" tag.
        qof_rows = [r for r in rows if r.get("source") == "QOF Business Rules 2024-25"]
        for r in qof_rows:
            all_codes.append({
                "code": r["code"],
                "term": r["term"],
                "vocabulary": r["vocabulary"],
                "source": r["source"],
                "domain": r["domain"],
                "similarity_score": None,  # exact match, not semantic
                "usage_frequency": None,
                "concept_id": get_concept_id_for(r["vocabulary"], r["code"]),
            })

        logger.info("QOF: '%s' returned %d codes (%d total before source filter)", name, len(qof_rows), len(rows))

    return {
        "retrieved_codes": all_codes,
        "sources_queried": ["QOF Business Rules 2024-25"],
    }

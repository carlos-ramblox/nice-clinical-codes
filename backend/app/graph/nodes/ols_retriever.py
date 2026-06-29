"""OLS4 retriever (issue #25): pure phenotype/adverse-event recall from EBI
OLS4. `xref_enricher` maps the concepts to SNOMED CT later."""

import logging

import requests

from app.config import OLS4_BASE_URL, OLS4_TIMEOUT, RETRIEVAL_TOP_K

logger = logging.getLogger(__name__)

# Ontologies to search. Adding a new one (e.g. "hp", "ordo") is a one-line
# entry here — xref_enricher maps whatever obo_ids come back.
ONTOLOGY_META = {
    "efo": {"source": "OLS4 (EFO)", "domain": "Phenotype"},
    "oae": {"source": "OLS4 (OAE)", "domain": "AdverseEvent"},
}

_FIELD_LIST = "iri,label,obo_id,short_form,ontology_name,is_obsolete"


def _vocab_from_obo_id(obo_id: str) -> str:
    """`MONDO:0007154` -> `MONDO`. OLS4's merged ontologies return mixed
    prefixes, so vocabulary is derived from the code, not the search ontology."""
    return obo_id.split(":", 1)[0] if ":" in obo_id else obo_id


def _search_ols(term: str, ontology: str, top_k: int = RETRIEVAL_TOP_K) -> list[dict]:
    """Query OLS4 search for one term against one ontology. Returns
    RetrievedCode dicts. Degrades gracefully: on timeout/HTTP/parse error
    logs a warning and returns []. Obsolete terms are dropped."""
    meta = ONTOLOGY_META[ontology]
    params = {"q": term, "ontology": ontology, "rows": top_k, "fieldList": _FIELD_LIST}
    try:
        r = requests.get(f"{OLS4_BASE_URL}/search", params=params, timeout=OLS4_TIMEOUT)
        r.raise_for_status()
        docs = r.json().get("response", {}).get("docs", [])
    except Exception as exc:  # noqa: BLE001 — graceful degradation
        logger.warning("OLS4 %s query failed for '%s': %s", ontology, term, exc)
        return []

    codes: list[dict] = []
    for doc in docs:
        if doc.get("is_obsolete"):
            continue
        code = doc.get("obo_id") or doc.get("short_form")
        label = doc.get("label")
        if not code or not label:
            continue
        codes.append({
            "code": code,
            "term": label,
            "vocabulary": _vocab_from_obo_id(code),
            "source": meta["source"],
            "domain": meta["domain"],
            "similarity_score": None,
            "usage_frequency": None,
            "concept_id": None,
            # IRI retained so xref_enricher can fetch term-detail without
            # reconstructing the URL from the obo_id (issue #25).
            "iri": doc.get("iri"),
        })
    logger.info("OLS4 %s: '%s' -> %d codes", ontology.upper(), term, len(codes))
    return codes


# Future vocab-gating: this retriever's effective output is SNOMED CT (via
# xref), not its raw ontology vocabularies, so a per-retriever vocab gate must
# key on the post-xref vocab or it would wrongly skip OLS4 on a SNOMED query.
def retrieve_from_ols(state: dict) -> dict:
    """LangGraph node: query OLS4 for EFO (all conditions) and OAE
    (comorbidities only). Reads parsed_conditions, writes retrieved_codes."""
    conditions = state.get("parsed_conditions", [])
    if not conditions:
        logger.warning("No conditions to search")
        return {"retrieved_codes": [], "sources_queried": []}

    all_codes: list[dict] = []
    queried_efo = False
    queried_oae = False

    for condition in conditions:
        name = condition.get("name", "")
        if not name:
            continue

        all_codes.extend(_search_ols(name, "efo"))
        queried_efo = True

        if condition.get("condition_type") == "comorbidity":
            all_codes.extend(_search_ols(name, "oae"))
            queried_oae = True

    sources_queried: list[str] = []
    if queried_efo:
        sources_queried.append("OLS4 (EFO)")
    if queried_oae:
        sources_queried.append("OLS4 (OAE)")

    return {"retrieved_codes": all_codes, "sources_queried": sources_queried}

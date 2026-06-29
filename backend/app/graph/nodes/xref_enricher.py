"""xref enricher: mints SNOMED CT codes from OLS4 concepts
via their `obo_xref`."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from app.config import (
    OLS4_BASE_URL,
    OLS4_TIMEOUT,
    OLS4_XREF_ENRICH,
    OLS4_XREF_PREDICATES,
    OLS4_XREF_MAX_CONCEPTS,
    OLS4_XREF_WORKERS,
    OLS4_XREF_VOCAB_MAP,
    MAX_CANDIDATES,
)
from app.graph.nodes.result_merger import _stable_sort_key

logger = logging.getLogger(__name__)

# Only enrich concepts that came from an OLS ontology search. Keyed on the
# source prefix so any future OLS ontology is covered automatically.
_OLS_SOURCE_PREFIX = "OLS4 ("

# SCTIDs are numeric (6-18 digits). Minted SNOMED codes come verbatim from an
# external API, so validate the shape before one enters the candidate pipeline
# as a trusted clinical code.
_SCTID_RE = re.compile(r"^\d{6,18}$")


def _predicate_ok(description: str) -> bool:
    """Per-ontology predicate parse. Formats differ:
       MONDO/HP : 'MONDO:equivalentTo'  -> token after last ':'
       Orphanet : 'Orphanet:46724/e'    -> suffix after '/' ('e'=Exact)
    Keep only predicates in the configured allow-set (lower-cased)."""
    if not description:
        return False
    if "/" in description:  # Orphanet / ORDO form
        pred = description.rsplit("/", 1)[-1]
    else:  # MONDO / HP / DOID form
        pred = description.rsplit(":", 1)[-1]
    return pred.strip().lower() in OLS4_XREF_PREDICATES


def _ontology_from_source(source: str) -> str:
    """`OLS4 (EFO)` -> `efo`. The term-detail endpoint is per-ontology."""
    return source.replace(_OLS_SOURCE_PREFIX, "").rstrip(")").lower()


def _fetch_xrefs(concept: dict) -> list[dict]:
    """Fetch obo_xref for one ontology concept; return minted SNOMED CT
    code dicts (SCTID only). Graceful on error (warn + [])."""
    iri = concept.get("iri")
    ontology = _ontology_from_source(str(concept.get("source", "")))
    if not iri or not ontology:
        return []
    url = f"{OLS4_BASE_URL}/ontologies/{ontology}/terms"
    try:
        r = requests.get(url, params={"iri": iri}, timeout=OLS4_TIMEOUT)
        r.raise_for_status()
        term = r.json()["_embedded"]["terms"][0]
        xrefs = term.get("obo_xref") or []
    except Exception as exc:  # noqa: BLE001 — graceful degradation
        logger.warning("OLS4 xref fetch failed for %s: %s", iri, exc)
        return []

    out: list[dict] = []
    for x in xrefs:
        db = x.get("database")
        xid = x.get("id")
        desc = x.get("description", "")
        vocab = OLS4_XREF_VOCAB_MAP.get(db)  # ICD-10 / UMLS / etc. excluded by the map
        if not vocab or not xid or not _predicate_ok(desc):
            continue
        if vocab == "SNOMED CT" and not _SCTID_RE.match(str(xid)):
            continue  # reject a malformed SCTID rather than mint it as a code
        out.append({
            "code": xid,
            "term": concept["term"],  # phenotype label (derived)
            "vocabulary": vocab,
            "source": f"OLS4 xref ({db})",
            "domain": "Condition",
            "similarity_score": None,
        })
    return out


def enrich_with_xrefs(state: dict) -> dict:
    """LangGraph node: map OLS ontology concepts to SNOMED CT via obo_xref
    and add them as first-class candidates. ICD-10 and UMLS are not emitted
    here. Pass-through when disabled or when no OLS concepts are present."""
    codes = state.get("enriched_codes", [])
    if not OLS4_XREF_ENRICH or not codes:
        return {}

    ontology_concepts = [
        c for c in codes if str(c.get("source", "")).startswith(_OLS_SOURCE_PREFIX)
    ][:OLS4_XREF_MAX_CONCEPTS]
    if not ontology_concepts:
        return {}

    minted: list[dict] = []
    with ThreadPoolExecutor(max_workers=OLS4_XREF_WORKERS) as pool:
        futures = {pool.submit(_fetch_xrefs, c): c for c in ontology_concepts}
        for fut in as_completed(futures):
            try:
                minted.extend(fut.result())
            except Exception as exc:  # noqa: BLE001
                logger.warning("xref enrich failed: %s", exc)

    # Merge minted SNOMED codes into enriched_codes, deduping on
    # (code, vocabulary) and corroborating sources[] when already present.
    existing = {(c["code"], c["vocabulary"]): c for c in codes}
    added = 0
    for m in minted:
        key = (m["code"], m["vocabulary"])
        src = m.pop("source")
        if key in existing:  # retriever already found this code
            tgt = existing[key]
            tgt.setdefault("sources", [tgt.get("source", "")])
            if src not in tgt["sources"]:
                tgt["sources"].append(src)
                tgt["source_count"] = len(tgt["sources"])
            continue
        m.update({
            "source": src,
            "sources": [src],
            "source_count": 1,
            "usage_frequency": None,
            "usage_status": "not_in_dataset",
            "usage_source": None,
            "usage_setting": None,
            "concept_id": None,
            "dmd_level": None,
        })
        existing[key] = m
        codes.append(m)
        added += 1

    if added and len(codes) > MAX_CANDIDATES:
        # rank by the same key result_merger uses for its cap, so minting can't
        # evict a high-confidence candidate by alphabetical code order.
        codes.sort(key=_stable_sort_key)
        codes = codes[:MAX_CANDIDATES]

    logger.info(
        "xref_enricher: minted %d SNOMED codes from %d OLS concepts",
        added, len(ontology_concepts),
    )
    return {"enriched_codes": codes}

import time
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests
import pandas as pd

from app.config import UMLS_API_KEY

logger = logging.getLogger(__name__)

UMLS_BASE = "https://uts-ws.nlm.nih.gov/rest"
UMLS_SEARCH = f"{UMLS_BASE}/search/current"
UMLS_CONTENT = f"{UMLS_BASE}/content/current"

# RN = narrower (more specific), SIB = sibling (same level)
TARGET_RELATIONS = {"RN", "SIB"}
MAX_PER_RELATION = 10
MAX_SYNONYMS = 10
REQUEST_GAP_SECS = 0.05
MAX_ENRICH_CONCEPTS = 30  # only enrich the top-ranked candidates
ENRICH_WORKERS = 10       # parallel UMLS lookups


class UMLSEnricher:
    """
    Enriches clinical concepts with UMLS-derived suggestions:
    narrower terms, siblings, and synonyms.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or UMLS_API_KEY
        if not self.api_key:
            raise ValueError("UMLS_API_KEY not set")
        self._cui_cache: dict[str, dict] = {}
        self._rel_cache: dict[str, list] = {}
        self._atom_cache: dict[str, list] = {}
        self._cache_lock = Lock()

    def _enrich_one(self, row: dict) -> list[dict]:
        """Look up one concept in UMLS and return suggestion rows for it."""
        concept_id = row.get("concept_id")
        concept_name = row.get("concept_name", "")
        vocab = row.get("_query_vocabulary", "")

        cui, preferred_term = self._normalise(concept_name)
        if not cui:
            return []

        synonyms = self._get_synonyms(cui)
        relations = self._get_relations(cui)

        rows: list[dict] = []
        for syn in synonyms[:MAX_SYNONYMS]:
            rows.append({
                "source_concept_id": concept_id,
                "source_concept_name": concept_name,
                "source_vocabulary": vocab,
                "umls_cui": cui,
                "umls_preferred_term": preferred_term,
                "suggestion_type": "synonym",
                "suggested_name": syn["name"],
                "suggested_cui": cui,
                "suggested_source": syn.get("rootSource", ""),
                "relation_label": "SY",
            })

        for rel in relations[:MAX_PER_RELATION * len(TARGET_RELATIONS)]:
            rel_label = rel.get("relationLabel", "")
            if rel_label not in TARGET_RELATIONS:
                continue
            rows.append({
                "source_concept_id": concept_id,
                "source_concept_name": concept_name,
                "source_vocabulary": vocab,
                "umls_cui": cui,
                "umls_preferred_term": preferred_term,
                "suggestion_type": _rel_label_to_type(rel_label),
                "suggested_name": rel.get("relatedIdName", ""),
                "suggested_cui": _extract_cui(rel.get("relatedId", "")),
                "suggested_source": rel.get("rootSource", ""),
                "relation_label": rel_label,
            })

        return rows

    def enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Takes an OMOPHub results DataFrame, returns a suggestions DataFrame
        with narrower terms, siblings, and synonyms for each concept.

        Only the top MAX_ENRICH_CONCEPTS rows are enriched (the result_merger
        already ranks candidates by source count + similarity), and lookups
        run in parallel across ENRICH_WORKERS threads to avoid serializing
        ~3 HTTP round-trips per concept against the NLM API.
        """
        if df.empty:
            return pd.DataFrame()

        capped = df.head(MAX_ENRICH_CONCEPTS)
        total = len(capped)
        logger.info("UMLS: enriching top %d of %d candidates with %d workers",
                    total, len(df), ENRICH_WORKERS)

        rows_to_process = [r.to_dict() for _, r in capped.iterrows()]
        suggestion_rows: list[dict] = []

        with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
            futures = {pool.submit(self._enrich_one, r): r for r in rows_to_process}
            for fut in as_completed(futures):
                try:
                    suggestion_rows.extend(fut.result())
                except Exception as exc:
                    src = futures[fut].get("concept_name", "")
                    logger.warning("UMLS enrich failed for '%s': %s", src[:60], exc)

        suggestions_df = pd.DataFrame(suggestion_rows)

        if suggestions_df.empty:
            logger.info("No UMLS suggestions returned")
            return suggestions_df

        suggestions_df.drop_duplicates(
            subset=["source_concept_id", "suggested_name", "relation_label"],
            inplace=True,
        )

        logger.info(
            "Enrichment complete: %d suggestions for %d concepts",
            len(suggestions_df),
            suggestions_df["source_concept_id"].nunique(),
        )
        return suggestions_df

    def _get(self, url: str, params: dict) -> dict | None:
        """GET with API key injection, error handling, and rate limiting."""
        params["apiKey"] = self.api_key
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            time.sleep(REQUEST_GAP_SECS)
            return resp.json()
        except requests.HTTPError:
            if resp.status_code == 404:
                return None
            logger.warning("UMLS HTTP %d for %s", resp.status_code, url)
            return None
        except Exception as exc:
            logger.warning("UMLS request failed: %s", exc)
            return None

    def _normalise(self, concept_name: str) -> tuple[str, str]:
        """Search UMLS for a concept name, return (CUI, preferred_term)."""
        if concept_name in self._cui_cache:
            cached = self._cui_cache[concept_name]
            return cached["cui"], cached["preferred_term"]

        data = self._get(UMLS_SEARCH, {
            "string": concept_name,
            "searchType": "normalizedString",
            "pageSize": 1,
        })

        if not data:
            return "", ""

        results = data.get("result", {}).get("results", [])
        if not results or results[0].get("ui") == "NONE":
            # fall back to words search
            data = self._get(UMLS_SEARCH, {
                "string": concept_name,
                "searchType": "words",
                "pageSize": 1,
            })
            results = (data or {}).get("result", {}).get("results", [])

        if not results or results[0].get("ui") == "NONE":
            self._cui_cache[concept_name] = {"cui": "", "preferred_term": ""}
            return "", ""

        top = results[0]
        cui = top.get("ui", "")
        name = top.get("name", "")
        self._cui_cache[concept_name] = {"cui": cui, "preferred_term": name}
        return cui, name

    def _get_synonyms(self, cui: str) -> list[dict]:
        """Fetch atoms for a CUI — different string names are synonyms."""
        if cui in self._atom_cache:
            return self._atom_cache[cui]

        data = self._get(f"{UMLS_CONTENT}/CUI/{cui}/atoms", {
            "pageSize": 50,
            "language": "ENG",
        })

        if not data:
            self._atom_cache[cui] = []
            return []

        atoms = data.get("result", [])
        seen: set[str] = set()
        syns = []
        for atom in atoms:
            name = atom.get("name", "").strip()
            if name and name not in seen:
                seen.add(name)
                syns.append({
                    "name": name,
                    "rootSource": atom.get("rootSource", ""),
                })

        self._atom_cache[cui] = syns
        return syns

    def _get_relations(self, cui: str) -> list[dict]:
        """Fetch relations for a CUI, filtering out suppressed/obsolete."""
        if cui in self._rel_cache:
            return self._rel_cache[cui]

        data = self._get(f"{UMLS_CONTENT}/CUI/{cui}/relations", {
            "pageSize": 200,
        })

        if not data:
            self._rel_cache[cui] = []
            return []

        relations = [
            r for r in data.get("result", [])
            if not r.get("suppressible") and not r.get("obsolete")
        ]

        self._rel_cache[cui] = relations
        return relations


def _rel_label_to_type(label: str) -> str:
    return {"RN": "narrower", "SIB": "sibling", "SY": "synonym"}.get(label, label.lower())


def _extract_cui(uri: str) -> str:
    """Pull CUI from a UMLS URI like .../CUI/C0011849"""
    if not uri:
        return ""
    parts = uri.rstrip("/").split("/")
    for part in reversed(parts):
        if part.startswith("C") and part[1:].isdigit():
            return part
    return parts[-1] if parts else ""


def enrich_codes(omophub_df: pd.DataFrame, api_key: str | None = None) -> pd.DataFrame:
    """Standalone entry point for enriching OMOPHub results."""
    enricher = UMLSEnricher(api_key=api_key)
    return enricher.enrich(omophub_df)

"""HDR UK Phenotype Library retriever.

Queries the public Phenotype Library REST API directly via ``requests``.
We do not depend on the official ``pyconceptlibraryclient`` because:

* It is GPL-3.0; the rest of the backend is not.
* It is not on PyPI (git-URL only), which complicates reproducible builds.
* The two endpoints we need are tiny and stable, so a thin direct client
  is shorter and easier to keep working.

API surface used:

* ``GET /api/v1/phenotypes/?search=<term>`` — paginated phenotype search
  returning ``{page, total_pages, page_size, data: [phenotype...]}``.
* ``GET /api/v1/phenotypes/{phenotype_id}/export/codes/`` — flat list of
  codelist rows (not paginated). Each row has ``code``, ``description``
  and a nested ``coding_system: {id, name, description}``.

Authentication: anonymous public read works for these endpoints — the
JAMIA Open 2024 paper and the pyconceptlibraryclient docs both confirm
read access does not require registration. Only phenotype creation /
editing requires credentials, which we do not perform.

Rate limits: not officially documented. We apply a defensive backoff on
429 / 5xx responses and a small per-request gap between phenotype fetches
so we self-limit before the server has to.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.config import HDR_UK_BASE_URL, HDR_UK_TOP_K_PHENOTYPES

logger = logging.getLogger(__name__)

SOURCE_TAG = "HDR UK Phenotype Library"

_REQUEST_TIMEOUT_S = 20
_MAX_RETRIES = 3
_BACKOFF_BASE_S = 1.5
_INTER_REQUEST_GAP_S = 0.2


# --- Coding-system normalisation ------------------------------------------
#
# HDR UK labels coding systems with quirks ("SNOMED  CT codes" — note the
# double space — "ICD10 codes", "Read codes v2"). Map the three vocabularies
# we already speak to their canonical OMOPHub-aligned names so the
# downstream merger / vocabulary-cue filter sees a consistent label across
# retrievers. Other systems pass through with a light cleanup so we don't
# silently drop UK primary-care content (Read v2) or future additions.
_KNOWN_VOCAB_MAP: dict[str, str] = {
    "snomed ct codes":  "SNOMED CT",
    "snomed-ct codes":  "SNOMED CT",
    "icd10 codes":      "ICD-10 (WHO)",
    "icd-10 codes":     "ICD-10 (WHO)",
    "opcs4 codes":      "OPCS-4",
    "opcs-4 codes":     "OPCS-4",
}


def _normalise_vocabulary(name: str) -> str:
    """Map an HDR UK ``coding_system.name`` value to a canonical label.

    Collapses internal whitespace and lowercases for the lookup so
    ``"SNOMED  CT codes"`` (double space, as actually returned by the
    API) maps to the same canonical key as ``"SNOMED CT codes"``.
    Unknown systems are returned with whitespace collapsed and the
    trailing ``" codes"`` suffix stripped — preserving e.g. ``"Read v2"``
    rather than emitting ``"Read codes v2"``.
    """
    if not name:
        return ""
    squished = " ".join(name.split())
    key = squished.lower()
    if key in _KNOWN_VOCAB_MAP:
        return _KNOWN_VOCAB_MAP[key]
    if key.endswith(" codes"):
        squished = squished[: -len(" codes")]
    return squished


# --- HTTP helpers ----------------------------------------------------------

def _get_with_backoff(session: requests.Session, url: str, params: dict | None = None) -> Any:
    """Issue a GET with exponential backoff on 429 / 5xx.

    Raises the final ``requests.HTTPError`` when retries are exhausted so
    callers can decide whether to swallow it; the retriever node catches
    everything per-condition so a transient HDR UK failure never sinks
    the whole pipeline.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            r = session.get(url, params=params, timeout=_REQUEST_TIMEOUT_S)
        except requests.RequestException as exc:
            last_exc = exc
            wait = _BACKOFF_BASE_S * (2 ** attempt)
            logger.warning("HDR UK request error (%s); retry %d/%d in %.1fs", exc, attempt + 1, _MAX_RETRIES, wait)
            time.sleep(wait)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            wait = _BACKOFF_BASE_S * (2 ** attempt)
            logger.warning("HDR UK %d on %s; retry %d/%d in %.1fs", r.status_code, url, attempt + 1, _MAX_RETRIES, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    if last_exc is not None:
        raise last_exc
    raise requests.HTTPError(f"HDR UK request failed after {_MAX_RETRIES} retries: {url}")


# --- API wrappers ----------------------------------------------------------

def search_phenotypes(session: requests.Session, term: str, top_k: int) -> list[dict]:
    """Return up to ``top_k`` phenotype summaries matching ``term``.

    The API returns relevance-ranked results in ``data`` and we trust that
    ordering — we don't have a scalar relevance score to expose downstream.
    """
    if not term.strip():
        return []
    payload = _get_with_backoff(
        session,
        f"{HDR_UK_BASE_URL.rstrip('/')}/api/v1/phenotypes/",
        params={"search": term},
    )
    if isinstance(payload, dict):
        data = payload.get("data") or []
    elif isinstance(payload, list):
        data = payload
    else:
        data = []
    return list(data[:top_k])


def get_codelist(session: requests.Session, phenotype_id: str) -> list[dict]:
    """Fetch the flat codelist for a phenotype id (e.g. ``"PH12"``).

    Returns ``[]`` and logs at warning level on any error so a single
    bad phenotype doesn't sink the whole condition's retrieval.
    """
    if not phenotype_id:
        return []
    url = f"{HDR_UK_BASE_URL.rstrip('/')}/api/v1/phenotypes/{phenotype_id}/export/codes/"
    try:
        payload = _get_with_backoff(session, url)
    except Exception as exc:
        logger.warning("HDR UK codelist fetch failed for %s: %s", phenotype_id, exc)
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    return []


# --- Mapping to RetrievedCode ---------------------------------------------

def hdruk_rows_to_retrieved_codes(rows: list[dict], phenotype_rank: int) -> list[dict]:
    """Convert raw codelist rows from HDR UK into ``RetrievedCode`` dicts.

    ``phenotype_rank`` is the 1-based search position of the parent
    phenotype; we use it as the ``rank`` field so a future re-introduction
    of the rank-fusion merger (deferred T01) can treat HDR UK output the
    same way it treats ChromaDB's per-sub-query rank — see
    ``state.RetrievedCode.rank``.
    """
    out: list[dict] = []
    for row in rows:
        code = row.get("code")
        if not code:
            continue
        coding = row.get("coding_system") or {}
        vocab = _normalise_vocabulary(coding.get("name", ""))
        out.append({
            "code": str(code),
            "term": row.get("description", ""),
            "vocabulary": vocab,
            "source": SOURCE_TAG,
            "domain": "Condition",
            "similarity_score": None,
            "usage_frequency": None,
            "rank": phenotype_rank,
        })
    return out


# --- LangGraph node --------------------------------------------------------

def retrieve_from_hdruk(state: dict) -> dict:
    """LangGraph node: search HDR UK Phenotype Library for each parsed condition.

    Algorithm per condition:

    1. Search ``/api/v1/phenotypes/?search=<name>`` — take the top
       ``HDR_UK_TOP_K_PHENOTYPES`` (default 3) by relevance.
    2. For each phenotype, fetch
       ``/api/v1/phenotypes/{id}/export/codes/`` and flatten every
       (code, vocabulary, term) row into a ``RetrievedCode``.
    3. Tag rank = phenotype's search position (1-based). All codes
       within the same phenotype share that rank — there's no
       relevance signal at sub-phenotype granularity.
    """
    conditions = state.get("parsed_conditions", [])
    if not conditions:
        logger.warning("No conditions to search")
        return {"retrieved_codes": [], "sources_queried": []}

    all_codes: list[dict] = []
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    for condition in conditions:
        name = condition.get("name", "")
        if not name:
            continue

        try:
            phenotypes = search_phenotypes(session, name, HDR_UK_TOP_K_PHENOTYPES)
        except Exception as exc:
            logger.warning("HDR UK search failed for '%s': %s", name, exc)
            continue

        for rank, phenotype in enumerate(phenotypes, start=1):
            pid = phenotype.get("phenotype_id")
            if not pid:
                continue
            time.sleep(_INTER_REQUEST_GAP_S)
            rows = get_codelist(session, pid)
            mapped = hdruk_rows_to_retrieved_codes(rows, phenotype_rank=rank)
            all_codes.extend(mapped)
            logger.info(
                "HDR UK: '%s' → phenotype %s (%s) rank=%d, %d codes",
                name, pid, phenotype.get("name", "?"), rank, len(mapped),
            )

    return {
        "retrieved_codes": all_codes,
        "sources_queried": [SOURCE_TAG],
    }

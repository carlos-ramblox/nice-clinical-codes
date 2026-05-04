"""HDR UK Phenotype Library discovery endpoint (T34).

Surfaces 3-5 candidate phenotypes whose clinical scope fits the user's
free-text query, ranked by the persona-driven LLM scope-fit judge from
``app.services.phenotype_discovery``. Read-mode only -- no code-mixing,
no auto-import. Each row's ``hdruk_url`` is the authoritative HDR UK
detail page; clicking goes there.

Cost model: one HDR UK search request + one Haiku judge call per uncached
discovery hit. The 5-minute in-process TTL cache below + the frontend's
300 ms debounce + the ``min_length=3`` query gate are sufficient guards
at demo scale; if sustained load shows up in telemetry, swap the
in-process cache for redis.

Auth: this router is intentionally **unauthenticated**, mirroring the
search and evaluate endpoints. Discovery happens before the user logs
in or commits to generating a codelist; gating it behind login would
contradict the persona pre-flight (browse-and-adjudicate, then decide).
The router intentionally does not import from ``app.api.codelists`` so
no ``Depends(get_current_user)`` leaks in.
"""
from __future__ import annotations

import logging
import threading
import time

import requests
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from typing import Literal

from app.config import HDR_UK_BASE_URL
from app.services.phenotype_discovery import (
    rank_phenotypes_with_rationale,
    search_phenotypes,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_DISCOVERY_CACHE_TTL_S = 300  # 5 minutes
_DISCOVERY_CACHE: dict[tuple[str, int], tuple[float, list]] = {}
_DISCOVERY_CACHE_LOCK = threading.Lock()


def _hdruk_detail_url(phenotype_id: str) -> str:
    """Build the public HDR UK detail-page URL for a phenotype id.

    The canonical form is ``/phenotypes/{id}/detail/`` but the server
    redirects ``/phenotypes/{id}`` there with a 200 final response, so
    the shorter form is fine and reads cleaner in the UI.
    """
    base = HDR_UK_BASE_URL.rstrip("/")
    return f"{base}/phenotypes/{phenotype_id}"


def _first_publication(phenotype: dict) -> str:
    """Return a single-line citation for the phenotype's first publication, or ''."""
    pubs = phenotype.get("publications") or []
    if not pubs or not isinstance(pubs[0], dict):
        return ""
    details = (pubs[0].get("details") or "").strip()
    return details[:240]  # cap so a long citation list doesn't blow the response


class PhenotypeDiscoveryResult(BaseModel):
    """One row of the discovery sidebar."""

    phenotype_id: str = Field(description="HDR UK phenotype id, e.g. PH12")
    name: str
    type: list[str] = Field(default_factory=list, description="e.g. ['Disease or syndrome']")
    coding_systems: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    first_publication: str = Field(default="", description="First citation, capped at 240 chars")
    hdruk_url: str = Field(description="Authoritative HDR UK detail page; primary affordance")
    relevance_rationale: str = Field(
        description="One-sentence judge rationale, or a fallback string when the judge was skipped",
    )
    relevance_verdict: Literal["relevant", "uncertain"] = Field(
        description="'relevant' iff the judge ran and explicitly admitted the phenotype",
    )


def _cache_get(query: str, top_k: int) -> list[PhenotypeDiscoveryResult] | None:
    key = (query.lower().strip(), top_k)
    with _DISCOVERY_CACHE_LOCK:
        entry = _DISCOVERY_CACHE.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.time() - ts > _DISCOVERY_CACHE_TTL_S:
            del _DISCOVERY_CACHE[key]
            return None
    return value


def _cache_put(query: str, top_k: int, value: list[PhenotypeDiscoveryResult]) -> None:
    key = (query.lower().strip(), top_k)
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE[key] = (time.time(), value)


def _project(phenotype: dict, decision) -> PhenotypeDiscoveryResult:
    """Build the response row from a (phenotype, decision) tuple."""
    pid = phenotype.get("phenotype_id", "")
    if decision is not None:
        rationale = decision.reason
        verdict: Literal["relevant", "uncertain"] = "relevant"
    else:
        # Judge skipped or silently omitted this phenotype -- the row is
        # surfaced for transparency but flagged so the UI can hedge the
        # rationale ("(judge skipped)" rather than a misleading reason).
        rationale = "Phenotype admitted without explicit scope-fit verdict (judge unavailable)."
        verdict = "uncertain"
    return PhenotypeDiscoveryResult(
        phenotype_id=pid,
        name=phenotype.get("name", ""),
        type=[t.get("name", "") for t in (phenotype.get("type") or []) if t.get("name")],
        coding_systems=[c.get("name", "") for c in (phenotype.get("coding_system") or []) if c.get("name")],
        data_sources=[d.get("name", "") for d in (phenotype.get("data_sources") or []) if d.get("name")],
        first_publication=_first_publication(phenotype),
        hdruk_url=_hdruk_detail_url(pid),
        relevance_rationale=rationale,
        relevance_verdict=verdict,
    )


@router.get("/phenotypes/discover", response_model=list[PhenotypeDiscoveryResult])
def discover_phenotypes(
    query: str = Query(..., min_length=3, max_length=200, description="Free-text clinical query, min 3 chars"),
    top_k: int = Query(5, ge=1, le=10, description="Maximum number of phenotypes to return (1-10)"),
):
    """Return HDR UK phenotypes whose clinical scope fits ``query``.

    Each row links to the authoritative HDR UK detail page; the UI does
    NOT proxy or wrap that page. Code-fetching is out of scope for this
    endpoint (T34 ships read-mode only); T35 covers post-hoc
    cross-reference with code-overlap measurement.
    """
    cached = _cache_get(query, top_k)
    if cached is not None:
        return cached

    try:
        with requests.Session() as session:
            session.headers.update({"Accept": "application/json"})
            phenotypes = search_phenotypes(session, query, top_k)
    except Exception as exc:
        # Transient HDR UK failure -- return empty so the UI hides the
        # sidebar rather than showing a stale or partial result.
        logger.warning("HDR UK search failed for '%s': %s", query, exc)
        return []

    if not phenotypes:
        return []

    ranked = rank_phenotypes_with_rationale(query, phenotypes)
    out = [_project(p, decision) for p, decision in ranked if p.get("phenotype_id")]
    _cache_put(query, top_k, out)
    return out


@router.delete("/phenotypes/discover/cache", include_in_schema=False)
def _clear_discovery_cache():
    """Hidden test helper: blow away the in-process cache.

    Not advertised in the public OpenAPI schema. Used by the endpoint
    tests to isolate cache-hit vs cache-miss behaviour without sleeping
    out the 5-minute TTL.
    """
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE.clear()
    return {"cleared": True}

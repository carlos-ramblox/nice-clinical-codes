"""Phenotype-library discovery service for HDR UK.

Two responsibilities, both consumed by **read-side** features:

* ``search_phenotypes(...)`` -- thin wrapper over HDR UK's
  ``/api/v1/phenotypes/?search=`` endpoint; returns ``top_k`` candidate
  phenotype dicts.
* ``judge_phenotype_relevance(query, phenotypes)`` -- LLM scope-fit
  judge that filters the candidate list to those whose clinical scope
  fits the user query. Guards against HDR UK's full-text search-quality
  failure mode (e.g. an "HIV" query returning paediatric Asthma
  phenotypes by metadata keyword overlap).

This module replaces the retriever-shape integration that originally
lived at ``backend/app/graph/nodes/hdruk_retriever.py`` (T13). The
persona audit (`_planning/persona_audit_2026_05_04.md`) and the T36
revert established that phenotype libraries are designed for
**browse-and-adjudicate** use, not **retrieve-and-merge**: each
phenotype is curated for a specific study question and its codes
are not interchangeable code-bags. The judge is the right tool;
T13 used it in the wrong consumer position. T34 (discovery
sidebar) and T35 (cross-reference) consume this service in the
correct read-side shape.

External dependencies per call:

* HDR UK ``GET /api/v1/phenotypes/?search=<term>`` -- anonymous public
  read; the JAMIA Open 2024 paper and the pyconceptlibraryclient docs
  both confirm read access does not require registration.
* Anthropic Haiku 4.5 (or whatever ``HDR_UK_JUDGE_MODEL`` points to)
  -- one structured-output call per discovery request, ~$0.001/query.

Authentication: the HDR UK call is anonymous public read. The judge
call uses ``ANTHROPIC_API_KEY`` -- the same key the rest of the
pipeline uses.

Rate limits: HDR UK's policy is not officially documented; we apply
defensive backoff on 429 / 5xx responses.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from app.config import (
    ANTHROPIC_API_KEY,
    HDR_UK_BASE_URL,
    HDR_UK_JUDGE_MODEL,
    HDR_UK_TOP_K_PHENOTYPES,
    HDR_UK_USE_JUDGE,
)

logger = logging.getLogger(__name__)

SOURCE_TAG = "HDR UK Phenotype Library"

_REQUEST_TIMEOUT_S = 20
_MAX_RETRIES = 3
_BACKOFF_BASE_S = 1.5


# --- HTTP backoff ---------------------------------------------------------

def _get_with_backoff(session: requests.Session, url: str, params: dict | None = None) -> Any:
    """Issue a GET with exponential backoff on 429 / 5xx.

    Raises the final ``requests.HTTPError`` when retries are exhausted so
    callers can decide whether to swallow it; the discovery endpoint and
    the cross-reference panel both catch errors at the request boundary
    so a transient HDR UK failure never sinks the user-facing handler.
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


# --- API wrappers ---------------------------------------------------------

def search_phenotypes(session: requests.Session, term: str, top_k: int = HDR_UK_TOP_K_PHENOTYPES) -> list[dict]:
    """Return up to ``top_k`` phenotype summaries matching ``term``.

    The API returns relevance-ranked results in ``data`` and we trust that
    ordering -- we do not have a scalar relevance score to expose
    downstream. Callers should treat the list order as best-effort
    relevance, then run :func:`judge_phenotype_relevance` to filter for
    clinical-scope fit before surfacing to the user.
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


# --- Phenotype relevance judge --------------------------------------------

class _PhenotypeRelevance(BaseModel):
    phenotype_id: str = Field(description="Phenotype id, e.g. PH12")
    relevant: bool = Field(description="True if the phenotype's clinical scope fits the user query")
    reason: str = Field(description="One-sentence rationale (<=25 words)")


class _PhenotypeRelevanceBatch(BaseModel):
    decisions: list[_PhenotypeRelevance]


_JUDGE_SYSTEM_PROMPT = """You are reviewing candidate phenotypes from the HDR UK Phenotype Library.

Each candidate phenotype is a SPECIFIC clinical definition used in published research -- not a generic
concept label. Two phenotypes both named "Asthma" can be entirely different codelists (one paediatric,
one adult; one prescription-driven, one diagnosis-driven). The user has asked for codes matching a
clinical query, and your job is to judge whether each phenotype's clinical scope is close enough that
its codelist would be appropriate to use.

Mark relevant=true ONLY when a clinician would consider the phenotype's codes appropriate for the
user's research question. Mark relevant=false when the phenotype:
- Targets a different condition entirely (false-positive keyword match in metadata)
- Is scoped to the wrong population (e.g. paediatric phenotype when the query is adult-implied)
- Is a drug/treatment phenotype when the query is for a condition (or vice versa)
- Is a derived complication-only phenotype when the query is for the parent condition itself
- Is overly narrow or overly broad relative to the query intent

Beware especially of phenotypes whose name only superficially matches the query -- HDR UK's full-text
search returns hits based on metadata keyword overlap, not clinical-concept fit, so wrong matches are
frequent. When in doubt, prefer relevant=false: surfacing an off-target phenotype to the user is a
worse failure mode than missing a borderline-relevant one.
"""


def _format_phenotype_for_judge(phenotype: dict) -> str:
    """Render a phenotype's metadata into a compact block for the judge prompt."""
    pid = phenotype.get("phenotype_id", "?")
    name = phenotype.get("name", "?")
    types = ", ".join(t.get("name", "") for t in (phenotype.get("type") or []) if t.get("name"))
    coding_systems = ", ".join(c.get("name", "") for c in (phenotype.get("coding_system") or []) if c.get("name"))
    data_sources = ", ".join(d.get("name", "") for d in (phenotype.get("data_sources") or []) if d.get("name"))
    pubs = phenotype.get("publications") or []
    first_pub = ""
    if pubs and isinstance(pubs[0], dict):
        details = (pubs[0].get("details") or "").strip()
        if details:
            first_pub = details[:240]  # cap so a long citation list doesn't blow the prompt
    lines = [f"- {pid} :: {name}"]
    if types:
        lines.append(f"    type: {types}")
    if coding_systems:
        lines.append(f"    coding_systems: {coding_systems}")
    if data_sources:
        lines.append(f"    data_sources: {data_sources}")
    if first_pub:
        lines.append(f"    first_publication: {first_pub}")
    return "\n".join(lines)


def judge_phenotype_relevance(query: str, phenotypes: list[dict]) -> list[dict]:
    """Filter ``phenotypes`` to those whose clinical scope fits ``query``.

    Returns a subset of the input list, preserving original order. Falls
    through to the input unchanged if:

    * The judge is disabled via ``HDR_UK_USE_JUDGE=no``.
    * No Anthropic key is configured.
    * The Haiku call raises (network blip, parse failure, etc.) -- better
      to over-include than to silently drop the whole HDR UK contribution.

    The fallback semantics intentionally favour availability over
    precision: a single bad judge call is less damaging than a black-hole
    discovery panel, and the F1 cost of over-inclusion is bounded by the
    user's own browse-and-adjudicate review (T34/T35 are read-mode, not
    auto-merge).
    """
    if not phenotypes:
        return []
    if not HDR_UK_USE_JUDGE:
        return phenotypes
    if not ANTHROPIC_API_KEY:
        logger.info("HDR UK judge: ANTHROPIC_API_KEY not set; passing %d phenotype(s) through", len(phenotypes))
        return phenotypes

    block = "\n".join(_format_phenotype_for_judge(p) for p in phenotypes)
    user_message = (
        f"<query>{query}</query>\n\n"
        f"<phenotypes>\n{block}\n</phenotypes>\n\n"
        "For each phenotype above, decide relevant=true/false."
    )

    try:
        llm = ChatAnthropic(
            model=HDR_UK_JUDGE_MODEL,
            api_key=ANTHROPIC_API_KEY,
            max_tokens=1024,
            temperature=0,
        )
        structured_llm = llm.with_structured_output(_PhenotypeRelevanceBatch)
        result = structured_llm.invoke([
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ])
    except Exception as exc:
        logger.warning("HDR UK judge call failed (%s); passing %d phenotype(s) through", exc, len(phenotypes))
        return phenotypes

    decisions_by_id: dict[str, _PhenotypeRelevance] = {d.phenotype_id: d for d in result.decisions}
    kept: list[dict] = []
    for p in phenotypes:
        pid = p.get("phenotype_id", "")
        decision = decisions_by_id.get(pid)
        if decision is None:
            # Judge silently dropped this phenotype id from its output --
            # treat as a soft pass-through rather than a covert reject so
            # we do not punish the user for the model omitting an item.
            kept.append(p)
            continue
        if decision.relevant:
            kept.append(p)
        else:
            logger.info("HDR UK judge: dropping %s (%s) -- %s", pid, p.get("name", "?"), decision.reason)
    return kept

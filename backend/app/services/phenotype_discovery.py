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

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import requests
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from app.db.code_normalize import normalize_code

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


def _call_judge(query: str, phenotypes: list[dict]) -> dict[str, _PhenotypeRelevance] | None:
    """Run the LLM scope-fit judge once. Returns ``decisions_by_id`` or
    ``None`` when the judge is skipped (disabled / no API key) or fails
    (LLM error). The caller decides what to do with the None case --
    both public surfaces below choose to fall through to the unfiltered
    input rather than admit a black-hole HDR UK contribution.

    Internal helper that both the simple filter and the verbose surface
    delegate to so we make at most one Haiku call per discovery request.
    """
    if not HDR_UK_USE_JUDGE:
        return None
    if not ANTHROPIC_API_KEY:
        logger.info("HDR UK judge: ANTHROPIC_API_KEY not set; passing %d phenotype(s) through", len(phenotypes))
        return None

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
        return None
    return {d.phenotype_id: d for d in result.decisions}


def rank_phenotypes_with_rationale(
    query: str,
    phenotypes: list[dict],
) -> list[tuple[dict, _PhenotypeRelevance | None]]:
    """Return ``(phenotype, decision)`` pairs for phenotypes that pass the judge.

    ``decision`` is the judge's ``_PhenotypeRelevance`` verdict for kept
    phenotypes whose id appeared in the model's structured response.
    ``decision`` is ``None`` when the judge was skipped (disabled / no
    API key / LLM error) **or** when the judge silently omitted that id
    from its output -- in both fall-through cases the phenotype is
    admitted as a soft pass.

    Phenotypes the judge marked ``relevant=False`` are dropped.

    This is the verbose surface T34's discovery sidebar consumes: the
    endpoint surfaces ``decision.reason`` to the user as the "matches
    because..." caption, so the simple filter (which discards the
    rationale) costs UI signal.
    """
    if not phenotypes:
        return []
    decisions = _call_judge(query, phenotypes)
    if decisions is None:
        return [(p, None) for p in phenotypes]
    kept: list[tuple[dict, _PhenotypeRelevance | None]] = []
    for p in phenotypes:
        pid = p.get("phenotype_id", "")
        decision = decisions.get(pid)
        if decision is None:
            kept.append((p, None))
            continue
        if decision.relevant:
            kept.append((p, decision))
        else:
            logger.info("HDR UK judge: dropping %s (%s) -- %s", pid, p.get("name", "?"), decision.reason)
    return kept


def judge_phenotype_relevance(query: str, phenotypes: list[dict]) -> list[dict]:
    """Thin filter wrapper: drops phenotypes the judge marks irrelevant.

    Falls through to the input unchanged when the judge is disabled (the
    return is the caller's exact list reference) or when the LLM call
    fails / no API key (the return is a fresh list with the same
    contents). The discovery endpoint uses
    :func:`rank_phenotypes_with_rationale` instead so it can surface the
    judge's per-row reason; this wrapper pins behaviour for the existing
    tests and is available for future callers that just want the
    filtered list.
    """
    if not HDR_UK_USE_JUDGE:
        return phenotypes
    return [p for p, _ in rank_phenotypes_with_rationale(query, phenotypes)]


# --- Cached discovery (shared by the discovery endpoint and the
#     cross-reference endpoint so neither pays for a repeated search +
#     Haiku judge call within the in-process TTL window). ----------------

_DISCOVERY_CACHE_TTL_S = 300  # 5 minutes
_DISCOVERY_CACHE: dict[
    tuple[str, int],
    tuple[float, list[tuple[dict, _PhenotypeRelevance | None]]],
] = {}
_DISCOVERY_CACHE_LOCK = threading.Lock()


def _discovery_cache_get(query: str, top_k: int):
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


def _discovery_cache_put(query: str, top_k: int, value):
    key = (query.lower().strip(), top_k)
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE[key] = (time.time(), value)


def clear_discovery_cache() -> None:
    """Test helper: drop all cached discovery results."""
    with _DISCOVERY_CACHE_LOCK:
        _DISCOVERY_CACHE.clear()


def discover_phenotypes_ranked(
    query: str,
    top_k: int = HDR_UK_TOP_K_PHENOTYPES,
) -> list[tuple[dict, _PhenotypeRelevance | None]]:
    """Search HDR UK + run the relevance judge, with in-process caching.

    Returns ``(phenotype, decision)`` tuples for phenotypes the judge
    admitted. ``decision`` is ``None`` for fall-through cases (judge
    skipped / LLM error / silent omission).

    The 5-minute in-process cache is keyed on the lower-cased trimmed
    query string and ``top_k``; both the discovery sidebar endpoint and
    the post-hoc cross-reference panel share it so a researcher who
    discovers a phenotype and then later asks for cross-reference on the
    same query pays for at most one Haiku call across the pair.
    """
    cached = _discovery_cache_get(query, top_k)
    if cached is not None:
        return cached
    try:
        with requests.Session() as session:
            session.headers.update({"Accept": "application/json"})
            phenotypes = search_phenotypes(session, query, top_k)
    except Exception as exc:
        logger.warning("HDR UK search failed for '%s': %s", query, exc)
        return []
    if not phenotypes:
        empty: list[tuple[dict, _PhenotypeRelevance | None]] = []
        _discovery_cache_put(query, top_k, empty)
        return empty
    ranked = rank_phenotypes_with_rationale(query, phenotypes)
    _discovery_cache_put(query, top_k, ranked)
    return ranked


# --- Phenotype-codelist fetch + file cache (T35) -------------------------
#
# HDR UK phenotype codelists are versioned and immutable per version, so
# a 7-day file-cache TTL is safe; the user can bust it via ``?refresh=1``
# on the cross-reference endpoint when they want to pick up a new
# version. Cache files live under ``data/cache/hdruk_phenotype_codes/``
# (gitignored) and store the *normalised* code set so the cross-reference
# overlap computation is just set arithmetic at read time. The 7-day
# TTL is a pragmatic guess; phenotype-library updates are rare and the
# refresh affordance is the escape hatch.

_PHENOTYPE_CACHE_TTL_S = 7 * 24 * 3600  # 7 days
# parents[3] = repo root: this file lives at backend/app/services/, so
# parents[0] = services, parents[1] = app, parents[2] = backend,
# parents[3] = repo root. Cache lives at <repo_root>/data/cache/... and
# is gitignored.
_PHENOTYPE_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache" / "hdruk_phenotype_codes"


def _phenotype_cache_path(phenotype_id: str, version: int | None) -> Path:
    """Return the file path for a cached phenotype codelist.

    Encoded as ``{id}__{version}.json`` so callers that want a specific
    version (T35 cross-reference reproducibility) can pin one; ``None``
    means "the live default version" and is cached under
    ``{id}__live.json``.
    """
    suffix = "live" if version is None else str(version)
    return _PHENOTYPE_CACHE_DIR / f"{phenotype_id}__{suffix}.json"


def _load_phenotype_cache(path: Path) -> set[str] | None:
    """Return the cached normalised code set, or ``None`` if missing/expired."""
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > _PHENOTYPE_CACHE_TTL_S:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        logger.warning("HDR UK phenotype-cache read failed for %s: %s", path.name, exc)
        return None
    codes = payload.get("codes", [])
    return set(codes) if isinstance(codes, list) else None


def _save_phenotype_cache(path: Path, codes: set[str]) -> None:
    """Persist the normalised code set; cache directory is created on demand."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"codes": sorted(codes), "n": len(codes)}, f)
    except Exception as exc:
        logger.warning("HDR UK phenotype-cache write failed for %s: %s", path.name, exc)


def fetch_phenotype_codes(
    session: requests.Session,
    phenotype_id: str,
    version: int | None = None,
    refresh: bool = False,
) -> set[str]:
    """Return the normalised code set for an HDR UK phenotype.

    Set elements use the same normalisation rule the headline benchmark
    evaluator applies (``benchmark_aggregate.normalize_code``: strip
    whitespace + dots, vocabulary-blind) so the cross-reference overlap
    measurement is comparable to the project's existing F1 numbers.

    Reads through a 7-day file cache by default. ``refresh=True`` skips
    the cache lookup but still writes the result back, so a one-off
    refresh repopulates the cache for subsequent reads.
    """
    if not phenotype_id:
        return set()
    cache_path = _phenotype_cache_path(phenotype_id, version)
    if not refresh:
        cached = _load_phenotype_cache(cache_path)
        if cached is not None:
            return cached
    base = HDR_UK_BASE_URL.rstrip("/")
    if version is None:
        url = f"{base}/api/v1/phenotypes/{phenotype_id}/export/codes/"
    else:
        url = f"{base}/api/v1/phenotypes/{phenotype_id}/version/{version}/export/codes"
    try:
        payload = _get_with_backoff(session, url)
    except Exception as exc:
        logger.warning("HDR UK phenotype fetch failed for %s: %s", phenotype_id, exc)
        return set()
    rows: list[dict]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
    else:
        rows = []
    codes: set[str] = set()
    for row in rows:
        raw = row.get("code") if isinstance(row, dict) else None
        if not raw:
            continue
        coding = (row.get("coding_system") or {}) if isinstance(row, dict) else {}
        vocab = coding.get("name", "") if isinstance(coding, dict) else ""
        normalised = normalize_code(str(raw), vocab)
        if normalised:
            codes.add(normalised)
    _save_phenotype_cache(cache_path, codes)
    return codes


def compute_overlap(generated: set[str], phenotype: set[str]) -> dict[str, float | int]:
    """Return Jaccard + the two asymmetric overlap percentages.

    Jaccard = |A ∩ B| / |A ∪ B|. The asymmetric numbers answer the two
    questions a methods researcher actually wants ranked side-by-side:
    *"how much of my generated list is already in this phenotype?"* and
    *"how much of this phenotype is in my generated list?"*. Single
    Jaccard is the primary affordance; the asymmetric pair lives one
    click away in the UI.

    All-zero output for empty inputs is the explicit contract: an empty
    generated codelist (rare, but possible during draft) has zero
    overlap with anything; the caller decides how to surface that
    (typically: hide the row).
    """
    n_generated = len(generated)
    n_phenotype = len(phenotype)
    intersection = generated & phenotype
    n_intersection = len(intersection)
    union = generated | phenotype
    jaccard = (n_intersection / len(union)) if union else 0.0
    gen_in_phen = (n_intersection / n_generated) if n_generated else 0.0
    phen_in_gen = (n_intersection / n_phenotype) if n_phenotype else 0.0
    return {
        "overlap_jaccard": round(jaccard, 4),
        "overlap_generated_in_phenotype": round(gen_in_phen, 4),
        "overlap_phenotype_in_generated": round(phen_in_gen, 4),
        "n_generated_codes": n_generated,
        "n_phenotype_codes": n_phenotype,
        "n_intersection": n_intersection,
    }

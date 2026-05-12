"""Fill in missing OMOP concept_id values via OMOPHub get_by_code.

The merger sets concept_id from any retriever that supplied one (only
OMOPHub does, natively). Anything still missing after the merge gets
one OMOPHub round-trip per (vocabulary, code) here, in parallel.

OMOPHub has no batch lookup-by-code endpoint: ``concepts.batch`` takes
OMOP integer ids (which we don't have yet) and ``search.bulk_basic``
is free-text only, no ``exact_match`` flag at the bulk level — codes
sharing substrings would cross-match. Parallel ``get_by_code`` is
therefore the only safe path.

Lookups are cached process-wide with a 7-day TTL so a vocabulary
release that fills in previously-unmapped codes lands within the week
without a container restart.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from omophub import OMOPHub, RateLimitError

from app.config import OMOPHUB_API_KEY, OMOPHUB_VOCABULARIES

logger = logging.getLogger(__name__)

_WORKERS = 4
_MAX_429_RETRIES = 4


def _retry_429(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run an OMOPHub call, sleeping on 429 and retrying with backoff."""
    for attempt in range(_MAX_429_RETRIES):
        try:
            return fn(*args, **kwargs)
        except RateLimitError as exc:
            if attempt == _MAX_429_RETRIES - 1:
                raise
            wait = getattr(exc, "retry_after", None) or (0.5 * (2 ** attempt))
            time.sleep(wait)
    raise RuntimeError("unreachable")
_CACHE_TTL_SECONDS = 7 * 24 * 3600

_LABEL_TO_OMOP_VOCAB = {label: omop_id for omop_id, label in OMOPHUB_VOCABULARIES.items()}

# (vocab_id, code) -> (timestamp, concept_id_or_none). Misses are cached
# too. TTL is checked on read; expired entries trigger a fresh lookup.
_CACHE: dict[tuple[str, str], tuple[float, int | None]] = {}
_CACHE_LOCK = threading.Lock()

# Singleton client. Reuses the underlying httpx connection pool across
# requests; instantiating per-request burned a fresh TLS handshake on
# every /api/search.
_client: OMOPHub | None = None
_client_lock = threading.Lock()


def _get_client() -> OMOPHub | None:
    global _client
    if not OMOPHUB_API_KEY:
        return None
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = OMOPHub(api_key=OMOPHUB_API_KEY)
    return _client


def _omop_vocab_id_for(label: str) -> str | None:
    return _LABEL_TO_OMOP_VOCAB.get(label)


def _cache_get(key: tuple[str, str]) -> tuple[bool, int | None]:
    """(hit, value). hit=False on miss or expired."""
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
    if entry is None:
        return False, None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        return False, None
    return True, value


def _cache_set(key: tuple[str, str], value: int | None) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)


def _safe_concept_id(concept: dict) -> int | None:
    """Standard concept_id verbatim, or follow ``Maps to`` to the standard
    equivalent; ``None`` if no clean mapping exists."""
    if not isinstance(concept, dict):
        return None
    raw_id = concept.get("concept_id")
    if raw_id is None:
        return None
    if concept.get("standard_concept") == "S":
        return int(raw_id)
    for rel in concept.get("relationships") or []:
        rel_id = rel.get("relationship_id") or rel.get("relationship_type")
        if rel_id != "Maps to":
            continue
        target = rel.get("target_concept_id")
        if target is None:
            target = rel.get("concept_id")
        if target is not None:
            try:
                return int(target)
            except (TypeError, ValueError):
                continue
    return None


def _lookup(client: OMOPHub, vocab_id: str, code: str) -> tuple[int | None, bool]:
    """Returns (safe_concept_id_or_none, cache_hit)."""
    key = (vocab_id, code)
    hit, value = _cache_get(key)
    if hit:
        return value, True

    try:
        concept = _retry_429(
            client.concepts.get_by_code, vocab_id, code, include_relationships=True,
        )
        cid_int = _safe_concept_id(concept)
    except RateLimitError as exc:
        logger.warning("OMOPHub rate-limited on get_by_code(%s, %s) after retries: %s", vocab_id, code, exc)
        return None, False
    except Exception as exc:
        logger.debug("OMOPHub get_by_code(%s, %s) failed: %s", vocab_id, code, exc)
        cid_int = None

    _cache_set(key, cid_int)
    return cid_int, False


def enrich_concept_ids(state: dict) -> dict:
    codes = state.get("enriched_codes", [])
    if not codes:
        return {}

    client = _get_client()
    if client is None:
        logger.warning("OMOPHUB_API_KEY not set, skipping concept_id enrichment")
        return {}

    pending: list[tuple[int, str, str]] = []  # (index, omop_vocab_id, code)
    for i, c in enumerate(codes):
        if c.get("concept_id") is not None:
            continue
        vocab_id = _omop_vocab_id_for(c.get("vocabulary", ""))
        code = c.get("code") or ""
        if not vocab_id or not code:
            continue
        pending.append((i, vocab_id, code))

    if not pending:
        return {}

    filled = 0
    cache_hits = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {pool.submit(_lookup, client, v, c): i for (i, v, c) in pending}
        for fut in as_completed(futures):
            i = futures[fut]
            cid, hit = fut.result()
            if hit:
                cache_hits += 1
            if cid is not None:
                codes[i]["concept_id"] = cid
                filled += 1

    logger.info(
        "concept_id_enricher: filled %d of %d unmapped codes "
        "(%d already mapped, %d cache hits)",
        filled, len(pending), len(codes) - len(pending), cache_hits,
    )
    return {"enriched_codes": codes}

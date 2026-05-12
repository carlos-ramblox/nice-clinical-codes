"""Expand each included scored_code with its OMOP 'Is a' descendants."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from omophub import OMOPHub, RateLimitError

from app.config import (
    HIERARCHY_EXPAND_MAX_LEVELS,
    HIERARCHY_EXPAND_MAX_PER_PARENT,
    OMOPHUB_API_KEY,
    OMOPHUB_VOCABULARIES,
)
from app.graph.nodes.concept_id_enricher import _retry_429

logger = logging.getLogger(__name__)

_WORKERS = 2
_CACHE_TTL_SECONDS = 7 * 24 * 3600

_CACHE: dict[int, tuple[float, list[dict]]] = {}
_CACHE_LOCK = threading.Lock()

_client: OMOPHub | None = None
_client_lock = threading.Lock()

_OMOP_VOCAB_IDS: frozenset[str] = frozenset(OMOPHUB_VOCABULARIES)
_VOCAB_LABEL: dict[str, str] = dict(OMOPHUB_VOCABULARIES)


def _get_client() -> OMOPHub | None:
    global _client
    if not OMOPHUB_API_KEY:
        return None
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = OMOPHub(api_key=OMOPHUB_API_KEY)
    return _client


def _cache_get(cid: int) -> tuple[bool, list[dict]]:
    with _CACHE_LOCK:
        entry = _CACHE.get(cid)
    if entry is None:
        return False, []
    ts, value = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        return False, []
    return True, value


def _cache_set(cid: int, value: list[dict]) -> None:
    with _CACHE_LOCK:
        _CACHE[cid] = (time.time(), value)


def _fetch_descendants(client: OMOPHub, cid: int) -> tuple[list[dict], bool]:
    hit, value = _cache_get(cid)
    if hit:
        return value, True
    try:
        resp = _retry_429(
            client.hierarchy.descendants, cid,
            max_levels=HIERARCHY_EXPAND_MAX_LEVELS,
            relationship_types=["Is a"],
            page_size=HIERARCHY_EXPAND_MAX_PER_PARENT,
            include_distance=False,
        )
        raw = resp.get("descendants", []) if isinstance(resp, dict) else []
    except RateLimitError as exc:
        logger.warning("OMOPHub rate-limited on descendants(%s) after retries: %s", cid, exc)
        return [], False  # don't cache transient throttle
    except Exception as exc:
        logger.debug("OMOPHub descendants(%s) failed: %s", cid, exc)
        raw = []
    if len(raw) >= HIERARCHY_EXPAND_MAX_PER_PARENT:
        logger.info("descendants(%s) capped at %d", cid, HIERARCHY_EXPAND_MAX_PER_PARENT)
    _cache_set(cid, raw)
    return raw, False


def _descendant_to_scored(parent: dict, desc: dict) -> dict | None:
    vocab_id = desc.get("vocabulary_id")
    if vocab_id not in _OMOP_VOCAB_IDS:
        return None
    if desc.get("standard_concept") != "S":
        return None
    code = desc.get("concept_code")
    if not code:
        return None
    return {
        "code": str(code),
        "term": desc.get("concept_name", ""),
        "vocabulary": _VOCAB_LABEL.get(vocab_id, vocab_id),
        "decision": "include",
        "confidence": 0.7,
        "rationale": f"Expanded via OMOP 'Is a' from concept_id {parent.get('concept_id')}",
        "sources": ["OMOPHub hierarchy"],
        "usage_frequency": None,
        "usage_status": None,
        "usage_source": None,
        "usage_setting": None,
        "concept_id": int(desc["concept_id"]) if desc.get("concept_id") is not None else None,
        "dmd_level": None,
    }


def expand_hierarchy(state: dict) -> dict:
    scored = state.get("scored_codes", []) or []
    if not scored:
        return {}
    client = _get_client()
    if client is None:
        logger.warning("OMOPHUB_API_KEY not set, skipping hierarchy expansion")
        return {}

    parents = [
        c for c in scored
        if c.get("decision") == "include" and c.get("concept_id") is not None
    ]
    if not parents:
        return {}

    seen: set[tuple[str, str]] = {
        (str(c.get("code", "")), c.get("vocabulary", "")) for c in scored
    }
    additions: list[dict] = []
    cache_hits = 0

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {pool.submit(_fetch_descendants, client, c["concept_id"]): c for c in parents}
        for fut in as_completed(futures):
            parent = futures[fut]
            descendants, hit = fut.result()
            if hit:
                cache_hits += 1
            for desc in descendants:
                row = _descendant_to_scored(parent, desc)
                if row is None:
                    continue
                key = (row["code"], row["vocabulary"])
                if key in seen:
                    continue
                seen.add(key)
                additions.append(row)

    if not additions:
        return {}

    combined = scored + additions
    ambiguous = [c for c in combined if c.get("decision") == "uncertain"]
    logger.info(
        "hierarchy_expander: %d parents -> %d new descendants (%d cache hits)",
        len(parents), len(additions), cache_hits,
    )
    return {"scored_codes": combined, "ambiguous_codes": ambiguous}

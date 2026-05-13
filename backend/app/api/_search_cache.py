"""
In-memory cache for search results. Shared between /api/search (writer)
and /api/export + /api/codelists (readers) so a single search_id can
rehydrate the query and the scored codes without re-running the pipeline.

FIFO eviction keeps memory bounded. Lives in its own module so callers
don't have to import from routes.py — avoids cross-module private state
and accidental circular imports as more endpoints appear.
"""

from __future__ import annotations

MAX_CACHE = 100

_entries: dict[str, dict] = {}


def put(
    search_id: str,
    query: str,
    codes: list[dict],
    *,
    include_criteria: list[str] | None = None,
    exclude_criteria: list[str] | None = None,
    include_descendants: bool = False,
) -> None:
    if len(_entries) >= MAX_CACHE:
        _entries.pop(next(iter(_entries)))
    _entries[search_id] = {
        "query": query,
        "codes": codes,
        # T29 — carried so create_codelist can persist criteria on the
        # draft and feed them into signature_hash on approval.
        "include_criteria": list(include_criteria or []),
        "exclude_criteria": list(exclude_criteria or []),
        "include_descendants": bool(include_descendants),
    }


def get(search_id: str) -> dict | None:
    """Returns the stored entry or ``None`` when missing/evicted."""
    return _entries.get(search_id)

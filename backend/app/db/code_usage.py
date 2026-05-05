"""Per-code usage-frequency lookup against the ``code_usage`` SQLite
table populated from OpenCodeCounts-style NHS Digital sources (T31).

The lookup returns three correlated values:

- ``usage_frequency``: int when a count is known, ``None`` otherwise.
- ``usage_status``: one of ``"counted"``, ``"withheld_below_5"``,
  ``"not_in_dataset"``. Lets the UI distinguish "we have no row for this
  code" from "NHS Digital withheld the value under their 1-4 privacy
  rule" — both have ``usage_frequency=None`` but mean different things.
- ``usage_source``: human-readable attribution string for the dataset
  the value was drawn from. Surfaces in the column-header tooltip.

Rows are looked up by ``(vocabulary, code)`` against the most-recent
year present per vocabulary; the most-recent-year map is computed once
at module import to avoid an extra ``MAX(year)`` query on every code.
The cache is invalidated by ``refresh_year_cache()`` after re-ingest.
"""

from __future__ import annotations

import logging
import threading
from typing import TypedDict

from app.db.code_store import get_connection

logger = logging.getLogger(__name__)


# Attribution strings rendered in the response payload. Per-setting so
# the UI can distinguish "12,400 GP visits" (SNOMED primary care) from
# "12,400 inpatient FCEs" (ICD-10/OPCS-4 secondary care).
_SOURCE_ATTRIBUTION = {
    "primary_care":
        "NHS Digital primary care SNOMED reporting",
    "secondary_care_hes":
        "NHS Digital HES inpatient FCEs",
}


class UsageLookup(TypedDict):
    usage_frequency: int | None
    usage_status: str  # "counted" | "withheld_below_5" | "not_in_dataset"
    usage_source: str | None
    usage_setting: str | None  # "primary_care" | "secondary_care_hes" | None
    # TODO: the ``code_usage`` table also stores
    # ``active_at_start`` / ``active_at_end`` columns (captured by the
    # SNOMED ingest under T31). They are NOT yet read by lookup() or
    # surfaced through CodeResult — the foundation is laid for a future
    # "deprecated SNOMED" / "newly active" badge but the UI work and
    # an active-state benchmark are out of scope for T31. When picking
    # this up, extend UsageLookup with active_at_start/active_at_end,
    # widen the SELECT below, and decide whether the badge belongs in
    # the Usage column or alongside the System column.


_NOT_IN_DATASET: UsageLookup = {
    "usage_frequency": None,
    "usage_status": "not_in_dataset",
    "usage_source": None,
    "usage_setting": None,
}


# (vocabulary -> most-recent year) cache. Populated lazily on first
# lookup so a fresh ingest is picked up without an explicit refresh
# call from the caller; reset by ``refresh_year_cache()`` after the
# ingest script finishes a re-load.
_year_cache: dict[str, int] | None = None
_year_cache_lock = threading.Lock()


def _load_year_cache() -> dict[str, int]:
    """Build the (vocabulary -> most-recent year) map once.

    Double-checked locking: the steady-state hot path (cache populated)
    returns without acquiring the lock — important because lookup() is
    called once per candidate code, so 100 candidates × every-call lock
    would put unnecessary contention on the LangGraph thread pool.
    """
    global _year_cache
    cached = _year_cache
    if cached is not None:
        return cached
    with _year_cache_lock:
        if _year_cache is not None:
            return _year_cache
        conn = get_connection()
        rows = conn.execute(
            "SELECT vocabulary, MAX(year) AS year FROM code_usage GROUP BY vocabulary"
        ).fetchall()
        _year_cache = {r["vocabulary"]: r["year"] for r in rows}
        logger.info("code_usage: most-recent-year map = %s", _year_cache)
        return _year_cache


def refresh_year_cache() -> None:
    """Invalidate the most-recent-year cache. Call from the ingest
    script after re-loading ``code_usage`` so subsequent lookups see
    the new latest year without a process restart."""
    global _year_cache
    with _year_cache_lock:
        _year_cache = None


def lookup(vocabulary: str, code: str) -> UsageLookup:
    """Return the usage record for a single (vocabulary, code).

    Returns the most-recent year's row when present; falls back to
    ``not_in_dataset`` when the code has no entry. Withheld counts
    return ``usage_frequency=None`` with ``usage_status="withheld_below_5"``
    so callers can render them distinctly from missing-from-dataset.
    """
    if not vocabulary or not code:
        return _NOT_IN_DATASET

    years = _load_year_cache()
    year = years.get(vocabulary)
    if year is None:
        # Vocabulary has no rows at all (e.g. UMLS suggestions, or the
        # ingest hasn't been run). Skip the SELECT — saves an index hit
        # per code on every search.
        return _NOT_IN_DATASET

    conn = get_connection()
    # ORDER BY setting is deterministic insurance: the schema's PK is
    # (vocabulary, code, year, setting), so two rows with different
    # settings for the same code and year are allowed. Today every
    # vocabulary maps to one setting in the loader's _DATASET_TO_SETTING
    # so the LIMIT 1 is unambiguous; a future SNOMED-secondary-care
    # ingest would otherwise return one of the rows non-deterministically.
    row = conn.execute(
        """SELECT count, is_withheld, setting
           FROM code_usage
           WHERE vocabulary = ? AND code = ? AND year = ?
           ORDER BY setting
           LIMIT 1""",
        (vocabulary, code, year),
    ).fetchone()

    if row is None:
        return _NOT_IN_DATASET

    setting = row["setting"]
    attribution = _SOURCE_ATTRIBUTION.get(setting, setting)

    if row["is_withheld"]:
        return {
            "usage_frequency": None,
            "usage_status": "withheld_below_5",
            "usage_source": attribution,
            "usage_setting": setting,
        }

    return {
        "usage_frequency": row["count"],
        "usage_status": "counted",
        "usage_source": attribution,
        "usage_setting": setting,
    }

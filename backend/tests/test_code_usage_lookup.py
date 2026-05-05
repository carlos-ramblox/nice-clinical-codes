"""Tests for the ``code_usage.lookup()`` helper (T31).

The lookup is the runtime contract that backs the search-page Usage
column. Three things to pin:

  - "counted" rows return a usage_frequency, a usage_status of
    "counted", and the per-setting attribution string.
  - "withheld" rows return None / "withheld_below_5". Distinct from
    not-in-dataset because the UI renders them differently ("<5" vs
    "—") and because "withheld" carries the disclosure that the count
    exists upstream — just not at a precision NHS Digital permits.
  - Codes absent from the table return None / "not_in_dataset" and
    NEVER fall back to a different vocabulary's row by mistake.

Plus: the most-recent-year cache must pick the latest year per
vocabulary. Without this, a re-ingest that loads e.g. 2024 alongside
2025 would surface the wrong year's count.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app import config
from app.db import code_store, code_usage


def _isolate_db(tmp_path: Path) -> None:
    db = tmp_path / "code_usage_test.db"
    url = f"sqlite:///{db.as_posix()}"
    # Both config.DATABASE_URL and code_store.DATABASE_URL must be set:
    # code_store does ``from app.config import DATABASE_URL`` at module
    # load, capturing into a separate module-local name.
    config.DATABASE_URL = url
    code_store.DATABASE_URL = url
    code_store._local = threading.local()
    code_usage.refresh_year_cache()


def _insert(rows: list[dict]) -> None:
    conn = code_store.get_connection()
    conn.executemany(
        """INSERT INTO code_usage
           (vocabulary, code, year, count, setting, period_start, period_end,
            is_withheld, active_at_start, active_at_end)
           VALUES (:vocabulary, :code, :year, :count, :setting, :period_start,
                   :period_end, :is_withheld, :active_at_start, :active_at_end)""",
        rows,
    )
    conn.commit()
    code_usage.refresh_year_cache()


def _row(**overrides) -> dict:
    base = {
        "vocabulary": "SNOMED CT",
        "code": "1234567",
        "year": 2025,
        "count": 12_450,
        "setting": "primary_care",
        "period_start": "2024-08-01",
        "period_end": "2025-07-31",
        "is_withheld": 0,
        "active_at_start": 1,
        "active_at_end": 1,
    }
    base.update(overrides)
    return base


def test_lookup_returns_counted_for_known_snomed_code(tmp_path: Path):
    _isolate_db(tmp_path)
    _insert([_row(code="1234567", count=12_450)])
    result = code_usage.lookup("SNOMED CT", "1234567")
    assert result["usage_frequency"] == 12_450
    assert result["usage_status"] == "counted"
    assert result["usage_source"] == "NHS Digital primary care SNOMED reporting"
    assert result["usage_setting"] == "primary_care"


def test_lookup_returns_withheld_status_for_below_5(tmp_path: Path):
    _isolate_db(tmp_path)
    _insert([_row(code="9999999", count=None, is_withheld=1)])
    result = code_usage.lookup("SNOMED CT", "9999999")
    assert result["usage_frequency"] is None
    assert result["usage_status"] == "withheld_below_5"
    # Attribution still flows even when the count itself is suppressed —
    # "<5" without context is not a useful signal.
    assert result["usage_source"] == "NHS Digital primary care SNOMED reporting"


def test_lookup_returns_not_in_dataset_for_missing_code(tmp_path: Path):
    _isolate_db(tmp_path)
    _insert([_row(code="1234567")])  # only one row, lookup a different code
    result = code_usage.lookup("SNOMED CT", "0000000")
    assert result["usage_frequency"] is None
    assert result["usage_status"] == "not_in_dataset"
    assert result["usage_source"] is None


def test_lookup_returns_not_in_dataset_for_unknown_vocabulary(tmp_path: Path):
    """A vocabulary with no rows at all (e.g. the ``UMLS`` vocabulary
    used for UMLS suggestions) must short-circuit cleanly to
    not_in_dataset rather than returning a SNOMED row by mistake."""
    _isolate_db(tmp_path)
    _insert([_row(vocabulary="SNOMED CT", code="C0011860")])
    result = code_usage.lookup("UMLS", "C0011860")
    assert result["usage_status"] == "not_in_dataset"
    assert result["usage_frequency"] is None


def test_lookup_picks_most_recent_year(tmp_path: Path):
    """Two years for the same code; lookup must return 2025, not 2024."""
    _isolate_db(tmp_path)
    _insert([
        _row(code="1234567", year=2024, count=10_000,
             period_start="2023-08-01", period_end="2024-07-31"),
        _row(code="1234567", year=2025, count=12_450,
             period_start="2024-08-01", period_end="2025-07-31"),
    ])
    result = code_usage.lookup("SNOMED CT", "1234567")
    assert result["usage_frequency"] == 12_450


def test_lookup_returns_hes_attribution_for_secondary_care(tmp_path: Path):
    """ICD-10 HES rows must surface the HES attribution string so the
    UI's setting-aware badge renders ``HES``, not ``GP``."""
    _isolate_db(tmp_path)
    _insert([_row(
        vocabulary="ICD-10 (WHO)", code="E11", count=12_447,
        setting="secondary_care_hes",
        period_start="2024-04-01", period_end="2025-03-31",
        active_at_start=None, active_at_end=None,
    )])
    result = code_usage.lookup("ICD-10 (WHO)", "E11")
    assert result["usage_frequency"] == 12_447
    assert result["usage_source"] == "NHS Digital HES inpatient FCEs"
    assert result["usage_setting"] == "secondary_care_hes"


# --- Runner -----------------------------------------------------------------


def _close_thread_db() -> None:
    import sqlite3
    conn = getattr(code_store._local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        code_store._local = threading.local()


def _run_all() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            with tempfile.TemporaryDirectory() as td:
                try:
                    t(Path(td))
                finally:
                    _close_thread_db()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"PASS {t.__name__}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(_run_all())

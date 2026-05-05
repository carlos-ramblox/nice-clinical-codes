"""Tests for the OpenCodeCounts CSV → SQLite ingest (T31).

Three things to pin:

  - The SNOMED rounding rule (round to nearest 10, half-up). Asserted
    on the value 12,447 → 12,450 per the ticket brief; this is the
    canonical disclosure-honesty test, so a future change to the
    rounding rule has to update this test too.
  - The 1-4 withholding rule. Asserted both for upstream-suppressed
    rows ("*") and for the defensive case where a 1-4 sneaks past
    NHS Digital's own filter — we apply the rule ourselves.
  - That ICD-10 / OPCS-4 are NOT rounded. Two settings with two rules
    means the loader has to dispatch by dataset; this test catches a
    regression that mistakenly applied the SNOMED rule everywhere.

Run via pytest from backend/, or as a script:
    python -m tests.test_opencodecounts_ingest
"""

from __future__ import annotations

import sqlite3
import sys
import textwrap
import threading
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app import config
from app.db import code_store


def _isolate_db(tmp_path: Path) -> None:
    """Point the (thread-local) SQLite connection at a tmp path and
    drop any prior cached connection so the next get_connection() call
    rebuilds against the new path. Keeps the test self-contained even
    when run inside the same process as the live ingest pipeline.

    Both ``config.DATABASE_URL`` and ``code_store.DATABASE_URL`` need
    to be mutated: ``code_store`` does ``from app.config import
    DATABASE_URL`` at module load, which captures the value into a
    module-local name that's independent of the config module's
    attribute. Patching only one would leave the other reading the
    real on-disk DB and produce IntegrityErrors against pre-existing
    rows from past dev runs.
    """
    db = tmp_path / "code_usage_test.db"
    url = f"sqlite:///{db.as_posix()}"
    config.DATABASE_URL = url
    code_store.DATABASE_URL = url
    code_store._local = threading.local()


def _write(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


# --- Rounding rule unit ------------------------------------------------------


def test_round_to_nearest_10_canonical_case():
    """Ticket brief: 12,447 → 12,450."""
    from app.ingestion.opencodecounts import round_to_nearest_10
    assert round_to_nearest_10(12_447) == 12_450


def test_round_to_nearest_10_boundary_half_up():
    """Half-up at .5 — 5 → 10, 15 → 20, 25 → 30. Banker's rounding
    would give 0 / 20 / 20, which is NOT the NHS Digital rule."""
    from app.ingestion.opencodecounts import round_to_nearest_10
    assert round_to_nearest_10(5) == 10
    assert round_to_nearest_10(15) == 20
    assert round_to_nearest_10(25) == 30


def test_round_to_nearest_10_zero_is_zero():
    from app.ingestion.opencodecounts import round_to_nearest_10
    assert round_to_nearest_10(0) == 0


# --- Withholding ------------------------------------------------------------


def test_is_withheld_below_5():
    from app.ingestion.opencodecounts import is_withheld_below_5
    for n in (1, 2, 3, 4):
        assert is_withheld_below_5(n)
    assert not is_withheld_below_5(0)
    assert not is_withheld_below_5(5)
    assert not is_withheld_below_5(10_000)


# --- CSV parsing ------------------------------------------------------------


def test_parse_csv_snomed_rounds_and_marks_withheld(tmp_path: Path):
    """A SNOMED CSV with the canonical 12,447 row plus an upstream-
    withheld row plus a defensive 1-4 row exercises all three branches
    of the dispatch logic in one go."""
    _isolate_db(tmp_path)

    from app.ingestion.opencodecounts import parse_csv

    csv_path = tmp_path / "snomed_2024_25.csv"
    _write(csv_path, """
        start_date,end_date,snomed_code,description,usage,active_at_start,active_at_end
        2024-08-01,2025-07-31,1234567,Type 2 diabetes mellitus,12447,TRUE,TRUE
        2024-08-01,2025-07-31,7654321,Rare condition,*,TRUE,TRUE
        2024-08-01,2025-07-31,9999999,Another rare,3,FALSE,TRUE
    """)

    dataset, rows = parse_csv(csv_path)
    assert dataset == "snomed_primary_care"
    assert len(rows) == 3

    by_code = {r["code"]: r for r in rows}

    # Canonical case from the ticket: 12,447 → 12,450.
    assert by_code["1234567"]["count"] == 12_450
    assert by_code["1234567"]["is_withheld"] == 0
    assert by_code["1234567"]["active_at_start"] == 1
    assert by_code["1234567"]["active_at_end"] == 1

    # Upstream-withheld row (NHS Digital's own "*").
    assert by_code["7654321"]["count"] is None
    assert by_code["7654321"]["is_withheld"] == 1

    # Defensive case: a 1-4 leaked past NHS Digital — we suppress it
    # ourselves rather than republish.
    assert by_code["9999999"]["count"] is None
    assert by_code["9999999"]["is_withheld"] == 1
    assert by_code["9999999"]["active_at_start"] == 0


def test_parse_csv_icd10_is_not_rounded(tmp_path: Path):
    """HES inpatient ICD-10 has no rounding/withholding rule. A raw
    count of 12,447 must round-trip unchanged — proves the dispatch
    logic doesn't apply the SNOMED rule to every dataset."""
    _isolate_db(tmp_path)

    from app.ingestion.opencodecounts import parse_csv

    csv_path = tmp_path / "icd10_2024_25.csv"
    _write(csv_path, """
        start_date,end_date,icd10_code,description,usage
        2024-04-01,2025-03-31,E11,Type 2 diabetes mellitus,12447
        2024-04-01,2025-03-31,I10,Essential hypertension,3
    """)

    dataset, rows = parse_csv(csv_path)
    assert dataset == "icd10_hes_inpatient"
    by_code = {r["code"]: r for r in rows}

    # NOT rounded.
    assert by_code["E11"]["count"] == 12_447
    assert by_code["E11"]["is_withheld"] == 0

    # NOT withheld at 1-4 either — the SNOMED rule does not apply.
    assert by_code["I10"]["count"] == 3
    assert by_code["I10"]["is_withheld"] == 0


def test_parse_csv_skips_snomed_row_with_unparseable_usage(tmp_path: Path):
    """Regression test for the parse-fallthrough bug: a SNOMED row whose
    usage cell is neither a number nor a known withhold marker
    (``*``/``[c]``) used to insert as ``count=None, is_withheld=0``,
    producing a logically inconsistent ``usage_status="counted"`` /
    ``usage_frequency=None`` row downstream. The loader must skip it."""
    _isolate_db(tmp_path)

    from app.ingestion.opencodecounts import parse_csv

    csv_path = tmp_path / "snomed_2024_25.csv"
    _write(csv_path, """
        start_date,end_date,snomed_code,description,usage,active_at_start,active_at_end
        2024-08-01,2025-07-31,1111111,Has a real count,250,TRUE,TRUE
        2024-08-01,2025-07-31,2222222,Mystery sentinel value,??,TRUE,TRUE
        2024-08-01,2025-07-31,3333333,Empty usage cell,,TRUE,TRUE
    """)

    _, rows = parse_csv(csv_path)
    codes = {r["code"] for r in rows}
    # Only the parseable row survives; the two malformed rows are
    # skipped rather than emitted as count=None/is_withheld=0.
    assert codes == {"1111111"}
    assert all(r["count"] is not None for r in rows)


def test_parse_csv_hes_long_filters_to_primary_fce(tmp_path: Path):
    """Pins the HES long-format dispatch (T31 real-data adaptation).

    NHS Digital's HES files publish one row per (code, category,
    attribute) tuple. The loader must:
      - dispatch to the long-format parser via the joint Code +
        Category column presence
      - keep DIAG_3_01 / DIAG_4_01 (and OPERTN_*_01 for procedures)
      - keep only ``FCE_SUM`` rows (case-insensitive — ICD-10 uses
        FCE_SUM, OPCS-4 uses FCE_Sum)
      - skip sentinel codes ('-', '&') and zero/non-positive values
      - emit one wide-format row per kept (code, category) pair

    Without this test a 'simplify' pass that mistakes the magic-string
    set for over-engineering would silently empty the HES rows for
    every search.
    """
    _isolate_db(tmp_path)

    from app.ingestion.opencodecounts import parse_csv

    csv_path = tmp_path / "icd10_hes_diag_2024-25.csv"
    _write(csv_path, """
        UID,Code,Category,Attribute,Value
        1,E11,DIAG_3_01,FCE_SUM,70273
        2,E11,DIAG_3_01,FAE_SUM,42060
        3,E11,DIAG_3_01,FCE_Male_Sum,45484
        4,E11,DIAG_3_01,Age_25_29_Sum,389
        5,E11.1,DIAG_4_01,FCE_SUM,12927
        6,E11.1,DIAG_4_01,Age_25_29_Sum,12
        7,-,DIAG_4_01,FCE_SUM,9999999
        8,&,DIAG_4_01,FCE_SUM,1452
        9,Z00,DIAG_4_01,FCE_SUM,0
        10,A00,DIAG_3_99,FCE_SUM,500
    """)

    dataset, rows = parse_csv(csv_path)
    assert dataset == "icd10_hes_inpatient"

    by_code = {r["code"]: r for r in rows}
    # Only the rows that are (a) DIAG_*_01 and (b) FCE_SUM and
    # (c) non-zero with a real code should survive.
    assert set(by_code) == {"E11", "E11.1"}
    assert by_code["E11"]["count"] == 70_273
    assert by_code["E11"]["setting"] == "secondary_care_hes"
    assert by_code["E11.1"]["count"] == 12_927
    # HES rows have no active_at_* info from the source.
    assert by_code["E11"]["active_at_start"] is None
    # Period derived from filename token "2024-25" → year 2025.
    assert by_code["E11"]["year"] == 2025
    assert by_code["E11"]["period_start"] == "2024-04-01"
    assert by_code["E11"]["period_end"] == "2025-03-31"


def test_parse_csv_hes_long_handles_opcs4_measure_value(tmp_path: Path):
    """OPCS-4 names the count column ``Measure Value`` and the
    attribute column ``Measure`` (case-mixed ``FCE_Sum``); ICD-10
    uses ``Value`` and ``Attribute`` (uppercase ``FCE_SUM``). This
    pins the case- and label-tolerance of the HES parser so a future
    NHS Digital schema drift on either header is caught here."""
    _isolate_db(tmp_path)

    from app.ingestion.opencodecounts import parse_csv

    csv_path = tmp_path / "opcs4_hes_proc_2024-25.csv"
    _write(csv_path, """
        UID,Code,Category,Measure,Measure Value
        1,A01.1,OPERTN_4_01,FCE_Sum,17
        2,A01.1,OPERTN_4_01,Age_25_29_Sum,3
        3,B12,OPERTN_3_01,FCE_Sum,2940
    """)

    dataset, rows = parse_csv(csv_path)
    assert dataset == "opcs4_hes_inpatient"
    by_code = {r["code"]: r["count"] for r in rows}
    assert by_code == {"A01.1": 17, "B12": 2940}


def test_filename_year_handles_academic_year_range(tmp_path: Path):
    """Pins the year-extraction off-by-one fix.

    NHS Digital filenames use the academic-year tag ``YYYY-YY`` (or
    ``YYYY_YY``). Returning the start year would miss the
    most-recent-year cache when a 2025-26 release lands alongside
    a 2024-25 one — both would resolve to year=2024 / year=2025
    respectively, the cache would prefer 2025 (the older), and a
    lookup against the new release would silently miss.
    """
    from app.ingestion.opencodecounts import _filename_year
    assert _filename_year(Path("foo_2024-25.csv")) == 2025
    assert _filename_year(Path("snomed_2024_25.txt")) == 2025
    assert _filename_year(Path("hosp-epis-stat-admi-diag-2024-25.csv")) == 2025
    # 4-digit suffix form (defensive — Bennett's example dataset uses it):
    assert _filename_year(Path("usage_2024-2025.csv")) == 2025
    # No range token → fall back to the first 4-digit year.
    assert _filename_year(Path("snomed_2025.csv")) == 2025
    # No year token at all → None.
    assert _filename_year(Path("snomed.csv")) is None


def test_ingest_directory_picks_up_txt_files(tmp_path: Path):
    """NHS Digital ships SNOMED primary-care as tab-separated ``.txt``
    rather than ``.csv``. The directory glob must include ``*.txt`` —
    a regression to ``glob('*.csv')`` only would silently drop the
    SNOMED dataset from production deploys."""
    _isolate_db(tmp_path)

    occ_dir = tmp_path / "raw" / "opencodecounts"
    occ_dir.mkdir(parents=True)
    # Tab-separated .txt — the real NHS Digital SNOMED file shape.
    (occ_dir / "snomed_code_usage_2024-25.txt").write_text(
        "SNOMED_Concept_ID\tDescription\tUsage\tActive_at_Start\tActive_at_End\n"
        "44054006\tDiabetes mellitus type 2\t1842360\t1\t1\n",
        encoding="utf-8",
    )

    from app.ingestion.opencodecounts import ingest_directory
    result = ingest_directory(tmp_path)
    assert result["loaded"] == 1
    files = result["files"]
    assert len(files) == 1
    assert files[0]["dataset"] == "snomed_primary_care"
    assert files[0]["rows"] == 1


def test_parse_csv_routes_dataset_by_filename(tmp_path: Path):
    """Filename keyword detection — the loader walks the directory
    blindly so a misrouted CSV would silently corrupt the lookup."""
    from app.ingestion.opencodecounts import _detect_dataset
    assert _detect_dataset("snomed_2024_25.csv") == "snomed_primary_care"
    assert _detect_dataset("icd10_hes_2024_25.csv") == "icd10_hes_inpatient"
    assert _detect_dataset("opcs4_hes_2024_25.csv") == "opcs4_hes_inpatient"
    assert _detect_dataset("unrelated.csv") is None


def test_ingest_directory_writes_to_code_usage_table(tmp_path: Path):
    """End-to-end ingest: parse_csv → insert_usage_rows → SQLite row.
    Verifies the table schema and round-trip."""
    _isolate_db(tmp_path)

    occ_dir = tmp_path / "raw" / "opencodecounts"
    occ_dir.mkdir(parents=True)
    _write(occ_dir / "snomed_2024_25.csv", """
        start_date,end_date,snomed_code,description,usage,active_at_start,active_at_end
        2024-08-01,2025-07-31,1234567,Type 2 diabetes mellitus,12447,TRUE,TRUE
    """)

    from app.ingestion.opencodecounts import ingest_directory
    result = ingest_directory(tmp_path)
    assert result["loaded"] == 1

    # Verify via the same thread-local connection rather than opening a
    # second one — Windows holds the .db lock when two connections
    # touch the file in quick succession, even after both .close().
    conn = code_store.get_connection()
    row = conn.execute(
        "SELECT * FROM code_usage WHERE code = '1234567'"
    ).fetchone()
    assert row is not None
    assert row["vocabulary"] == "SNOMED CT"
    assert row["count"] == 12_450
    assert row["setting"] == "primary_care"
    assert row["year"] == 2025


# --- Runner ------------------------------------------------------------------


def _close_thread_db() -> None:
    """Close any per-thread sqlite3 connection so Windows doesn't hold
    the tempdir's .db file open and refuse to remove it."""
    conn = getattr(code_store._local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        code_store._local = threading.local()


def _run_all() -> int:
    import inspect
    import tempfile

    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            sig = inspect.signature(t)
            if "tmp_path" in sig.parameters:
                with tempfile.TemporaryDirectory() as td:
                    try:
                        t(Path(td))
                    finally:
                        _close_thread_db()
            else:
                t()
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

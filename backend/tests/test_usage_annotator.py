"""End-to-end test for the T31 wiring: the usage_annotator graph node
populates EnrichedCode with usage_frequency / usage_status / usage_source
in the same shape that CodeResult downstream serialises.

This is the round-trip test the ticket asks for. We don't run the full
LangGraph here (that would need real API keys); instead we directly call
the annotator on a deduped candidate set and verify each branch — counted,
withheld, not-in-dataset — round-trips correctly. This catches a
regression in any of:

  - the per-vocabulary dispatch (SNOMED → primary_care → "GP" attribution)
  - the withheld-vs-not-in-dataset distinction
  - the EnrichedCode → CodeResult serialisation (we instantiate the
    Pydantic model from the annotated dict to prove the fields match
    the response schema).
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


def _seed_usage(rows: list[dict]) -> None:
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


def _enriched(code: str, vocabulary: str) -> dict:
    """A minimal post-merger EnrichedCode dict."""
    return {
        "code": code,
        "term": "test term",
        "vocabulary": vocabulary,
        "source": "QOF",
        "sources": ["QOF"],
        "source_count": 1,
        "domain": "Condition",
        "similarity_score": None,
        "usage_frequency": None,
        "usage_status": None,
        "usage_source": None,
        "usage_setting": None,
    }


def test_annotator_stamps_all_three_branches(tmp_path: Path):
    """One enriched-code list, three rows exercising each branch.
    The annotator must mutate each in place with the right shape."""
    _isolate_db(tmp_path)
    _seed_usage([
        {
            "vocabulary": "SNOMED CT", "code": "1234567",
            "year": 2025, "count": 12_450, "setting": "primary_care",
            "period_start": "2024-08-01", "period_end": "2025-07-31",
            "is_withheld": 0, "active_at_start": 1, "active_at_end": 1,
        },
        {
            "vocabulary": "SNOMED CT", "code": "9999999",
            "year": 2025, "count": None, "setting": "primary_care",
            "period_start": "2024-08-01", "period_end": "2025-07-31",
            "is_withheld": 1, "active_at_start": 1, "active_at_end": 1,
        },
    ])

    from app.graph.nodes.usage_annotator import annotate_usage

    state = {
        "enriched_codes": [
            _enriched("1234567", "SNOMED CT"),
            _enriched("9999999", "SNOMED CT"),
            _enriched("0000000", "SNOMED CT"),
        ],
    }
    out = annotate_usage(state)
    by_code = {c["code"]: c for c in out["enriched_codes"]}

    assert by_code["1234567"]["usage_frequency"] == 12_450
    assert by_code["1234567"]["usage_status"] == "counted"
    assert by_code["1234567"]["usage_source"] == "NHS Digital primary care SNOMED reporting"
    assert by_code["1234567"]["usage_setting"] == "primary_care"

    assert by_code["9999999"]["usage_frequency"] is None
    assert by_code["9999999"]["usage_status"] == "withheld_below_5"
    assert by_code["9999999"]["usage_setting"] == "primary_care"

    assert by_code["0000000"]["usage_frequency"] is None
    assert by_code["0000000"]["usage_status"] == "not_in_dataset"
    assert by_code["0000000"]["usage_source"] is None
    assert by_code["0000000"]["usage_setting"] is None


def test_annotator_round_trips_into_code_result(tmp_path: Path):
    """The whole point of the wiring: an annotated EnrichedCode-shaped
    dict must serialise into a CodeResult with the new fields populated.
    Catches a regression where the response schema and the pipeline
    state drift apart."""
    _isolate_db(tmp_path)
    _seed_usage([
        {
            "vocabulary": "ICD-10 (WHO)", "code": "E11",
            "year": 2025, "count": 9_847, "setting": "secondary_care_hes",
            "period_start": "2024-04-01", "period_end": "2025-03-31",
            "is_withheld": 0, "active_at_start": None, "active_at_end": None,
        },
    ])

    from app.graph.nodes.usage_annotator import annotate_usage
    from app.api.routes import CodeResult

    state = {"enriched_codes": [_enriched("E11", "ICD-10 (WHO)")]}
    annotated = annotate_usage(state)["enriched_codes"][0]

    # Add the LLM-decision fields the scorer would have appended; we
    # short-circuit the LLM path here because this test is about the
    # T31 fields only.
    annotated.update({
        "decision": "include",
        "confidence": 0.9,
        "rationale": "stub",
    })

    cr = CodeResult(
        code=annotated["code"],
        term=annotated["term"],
        vocabulary=annotated["vocabulary"],
        decision=annotated["decision"],
        confidence=annotated["confidence"],
        rationale=annotated["rationale"],
        sources=annotated["sources"],
        usage_frequency=annotated["usage_frequency"],
        usage_status=annotated["usage_status"],
        usage_source=annotated["usage_source"],
        usage_setting=annotated["usage_setting"],
    )

    assert cr.usage_frequency == 9_847
    assert cr.usage_status == "counted"
    assert cr.usage_source == "NHS Digital HES inpatient FCEs"
    # usage_setting is the machine-readable equivalent the frontend
    # uses to pick the GP/HES badge. Asserting it round-trips here
    # pins the fix for the substring-match regression risk.
    assert cr.usage_setting == "secondary_care_hes"


def test_annotator_passes_through_when_no_table(tmp_path: Path):
    """Fail-soft: when the code_usage table exists but is empty (e.g.
    the operator hasn't staged any CSVs yet) the annotator must still
    return every code, just with not_in_dataset across the board.
    Without this property a missing CSV would empty the search results."""
    _isolate_db(tmp_path)
    # Ensure the table exists but is empty.
    code_store.get_connection()
    code_usage.refresh_year_cache()

    from app.graph.nodes.usage_annotator import annotate_usage
    state = {"enriched_codes": [_enriched("1234567", "SNOMED CT")]}
    out = annotate_usage(state)
    assert len(out["enriched_codes"]) == 1
    assert out["enriched_codes"][0]["usage_status"] == "not_in_dataset"


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

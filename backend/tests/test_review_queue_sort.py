"""
Tests for the HITL review-queue ordering (T06 / math review R7).

The reviewer should see the codes the LLM is least sure about first —
canonical uncertainty sampling from Settles (2009). LLM-marked
``uncertain`` decisions are primed at the very top regardless of their
recorded confidence, since they're already explicitly flagged.

Run from backend/:
    pytest tests/test_review_queue_sort.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db.hitl_store import sort_review_queue


def _row(
    code: str,
    decision: str = "include",
    confidence: float | None = 0.5,
    *,
    id: int | None = None,
) -> dict:
    row: dict = {
        "code": code,
        "ai_decision": decision,
        "ai_confidence": confidence,
    }
    if id is not None:
        row["id"] = id
    return row


def test_ordering_matches_ticket_acceptance_set():
    """T06 acceptance: confidences {0.5, 0.95, 0.05, 0.7, "uncertain"} sort as
    [uncertain, 0.5, 0.7, then 0.95/0.05 tied]."""
    rows = [
        _row("A_p95", "include", 0.95),
        _row("B_p05", "exclude", 0.05),
        _row("C_p50", "include", 0.50),
        _row("D_p70", "include", 0.70),
        _row("E_unc", "uncertain", 0.99),  # high confidence but flagged uncertain
    ]
    out = sort_review_queue(rows)
    codes = [r["code"] for r in out]

    assert codes[0] == "E_unc", "uncertain decision must be first"
    assert codes[1] == "C_p50", "0.5 confidence is the most uncertain non-flagged row"
    assert codes[2] == "D_p70", "0.7 (margin 0.4) is next"
    # 0.95 and 0.05 both have |2c-1| = 0.9 and tie — broken by code asc
    assert set(codes[3:5]) == {"A_p95", "B_p05"}
    assert codes[3] == "A_p95" and codes[4] == "B_p05"


def test_missing_confidence_treated_as_maximum_uncertainty():
    """A None confidence should sort alongside 0.5 (margin 0), not be
    silently dropped or pushed to the bottom — safer default for a
    clinical review queue."""
    rows = [
        _row("Z_high", "include", 0.95),
        _row("A_none", "include", None),
        _row("M_p50", "include", 0.50),
    ]
    out = sort_review_queue(rows)
    codes = [r["code"] for r in out]

    # A_none and M_p50 both margin 0; A_none comes first by code asc
    assert codes[0] == "A_none"
    assert codes[1] == "M_p50"
    assert codes[2] == "Z_high"


def test_uncertain_priority_beats_low_margin():
    """A row marked uncertain still sorts above a high-uncertainty
    include/exclude row even if its recorded confidence is high."""
    rows = [
        _row("low_margin", "include", 0.51),  # margin 0.02
        _row("flagged", "uncertain", 0.99),   # margin 0.98 but uncertain
    ]
    out = sort_review_queue(rows)
    assert [r["code"] for r in out] == ["flagged", "low_margin"]


def test_stable_tiebreak_by_code():
    """Two rows with identical priority and margin sort by code ascending."""
    rows = [
        _row("ZZZ", "include", 0.5),
        _row("AAA", "include", 0.5),
        _row("MMM", "include", 0.5),
    ]
    out = sort_review_queue(rows)
    assert [r["code"] for r in out] == ["AAA", "MMM", "ZZZ"]


def test_invalid_confidence_string_treated_as_max_uncertainty():
    """A string in ai_confidence (DB corruption / migration mishap) must
    not crash the sort; treat as 0.5 like None."""
    rows = [
        _row("good", "include", 0.95),
        _row("bad", "include", "not-a-number"),  # type: ignore[arg-type]
    ]
    out = sort_review_queue(rows)
    assert [r["code"] for r in out] == ["bad", "good"]


def test_empty_list_returns_empty_list():
    assert sort_review_queue([]) == []


def test_id_breaks_ties_when_codes_collide():
    """Schema permits duplicate codes within a codelist (no UNIQUE
    constraint on (codelist_id, code)). When code, margin, and priority
    all match, id makes the order deterministic instead of relying on
    SQLite's undefined row order."""
    rows = [
        _row("DUP", "include", 0.5, id=42),
        _row("DUP", "include", 0.5, id=7),
        _row("DUP", "include", 0.5, id=99),
    ]
    out = sort_review_queue(rows)
    assert [r["id"] for r in out] == [7, 42, 99]


def test_input_not_mutated():
    """sort_review_queue returns a new list; original order is preserved."""
    original = [
        _row("A", "include", 0.95),
        _row("B", "include", 0.50),
    ]
    snapshot = list(original)
    _ = sort_review_queue(original)
    assert original == snapshot


def test_get_codelist_applies_sort_review_queue(tmp_path, monkeypatch):
    """Wiring test: ``get_codelist`` must apply ``sort_review_queue`` to
    the decisions it returns. The unit tests above only exercise
    ``sort_review_queue`` in isolation — without this test, a refactor
    that drops the call at the call-site (currently ``hitl_store.py``
    ``result["decisions"] = sort_review_queue(decisions)``) would leave
    every unit test green while silently regressing the API order back
    to SQLite's undefined row order."""
    from app.db import hitl_store

    db = tmp_path / "hitl_wiring.db"
    monkeypatch.setattr(hitl_store, "HITL_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setattr(hitl_store, "_conn", None)  # force fresh connection

    creator_id = hitl_store.list_users()[0]["id"]

    # Insert codes in a deliberately non-sorted order so a regression to
    # insertion-order would produce a different result than the sort.
    cid = hitl_store.create_codelist(
        name="wiring",
        query="q",
        created_by=creator_id,
        decisions=[
            {"code": "AAA", "term": "", "vocabulary": "SNOMED",
             "decision": "include",   "confidence": 0.95, "rationale": "", "sources": []},
            {"code": "BBB", "term": "", "vocabulary": "SNOMED",
             "decision": "uncertain", "confidence": 0.50, "rationale": "", "sources": []},
            {"code": "CCC", "term": "", "vocabulary": "SNOMED",
             "decision": "include",   "confidence": 0.50, "rationale": "", "sources": []},
        ],
    )

    codes = [d["code"] for d in hitl_store.get_codelist(cid)["decisions"]]
    # Expected order:
    #   BBB — uncertain decision, priority 0 (top regardless of margin)
    #   CCC — margin |2*0.5 - 1| = 0; code 'C' < ...
    #   AAA — margin |2*0.95 - 1| = 0.9
    assert codes == ["BBB", "CCC", "AAA"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))

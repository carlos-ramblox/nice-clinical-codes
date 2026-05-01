"""
Tests for the ChromaDB retriever's per-sub-query rank assignment.

Background: ``retrieve_from_chromadb`` issues one ChromaDB ``search()``
per coding system in the parsed condition's ``coding_systems`` list
(typically ["SNOMED", "ICD10"]). An earlier version assigned
``rank = len(all_codes) + 1`` over the *accumulated* output across the
inner ``for sys_key in systems`` loop, so a second sub-query's true
rank-1 (clinically the best ICD-10 hit) was reported as rank 51 — which
biased any downstream rank-fusion step toward whichever coding system
was searched first. T25 fixes that by resetting the rank counter per
sub-query.

The current merger does not yet consume the rank field (T01 RRF was
investigated and deferred — see _planning/T01_rrf_diagnostic.md), but
the field is part of ``RetrievedCode`` and is populated correctly here
so a future re-introduction of rank-fusion does not need a retriever-
side change first.

Run with pytest from backend/, or as a script:
    python -m tests.test_chroma_rank
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.graph.nodes.chroma_retriever import retrieve_from_chromadb


def _fake_search_factory(per_system_results: dict[str, list[dict]]):
    """Build a stub for ``app.graph.nodes.chroma_retriever.search`` that
    returns a different list per call based on the ``vocabulary`` arg."""
    def _fake_search(query, top_k, vocabulary):
        # The per-system stubs are keyed by the canonical OMOPHub
        # vocabulary string the retriever passes through.
        return list(per_system_results.get(vocabulary, []))
    return _fake_search


def _result(code, term, vocabulary):
    return {
        "code": code,
        "term": term,
        "vocabulary": vocabulary,
        "source": "ChromaDB",
        "domain": "Condition",
        "similarity_score": 0.9,
        "usage_frequency": None,
    }


def test_chroma_rank_resets_per_sub_query():
    """Two sub-queries (SNOMED + ICD-10), each returning three results,
    should produce ranks 1, 2, 3, 1, 2, 3 — not 1..6."""
    per_system = {
        "SNOMED CT": [
            _result("S1", "snomed-1", "SNOMED CT"),
            _result("S2", "snomed-2", "SNOMED CT"),
            _result("S3", "snomed-3", "SNOMED CT"),
        ],
        "ICD-10 (WHO)": [
            _result("I1", "icd-1", "ICD-10 (WHO)"),
            _result("I2", "icd-2", "ICD-10 (WHO)"),
            _result("I3", "icd-3", "ICD-10 (WHO)"),
        ],
    }
    state = {
        "parsed_conditions": [
            {"name": "Test condition", "coding_systems": ["SNOMED", "ICD10"]}
        ],
    }
    with patch(
        "app.graph.nodes.chroma_retriever.search",
        _fake_search_factory(per_system),
    ):
        out = retrieve_from_chromadb(state)

    codes = out["retrieved_codes"]
    assert len(codes) == 6
    by_code = {c["code"]: c for c in codes}
    # Both sub-queries' top hit should carry rank == 1.
    assert by_code["S1"]["rank"] == 1
    assert by_code["I1"]["rank"] == 1
    # Position-3 hits in each sub-query should carry rank == 3, not 3 / 6.
    assert by_code["S3"]["rank"] == 3
    assert by_code["I3"]["rank"] == 3


def test_chroma_rank_starts_at_one_per_condition():
    """Single coding-system condition: ranks are still 1-based per call."""
    per_system = {
        "SNOMED CT": [
            _result(f"X{i}", f"term-{i}", "SNOMED CT") for i in range(5)
        ],
    }
    state = {
        "parsed_conditions": [
            {"name": "Solo", "coding_systems": ["SNOMED"]}
        ],
    }
    with patch(
        "app.graph.nodes.chroma_retriever.search",
        _fake_search_factory(per_system),
    ):
        out = retrieve_from_chromadb(state)

    codes = out["retrieved_codes"]
    assert [c["rank"] for c in codes] == [1, 2, 3, 4, 5]


def test_chroma_source_tag_preserved():
    """The retriever overwrites ``source`` with the literal "ChromaDB"
    string before returning, regardless of what the underlying
    ``search`` stub set. Sanity check so the merger's source-count
    accounting still treats every code as ChromaDB-sourced."""
    per_system = {
        "SNOMED CT": [_result("A", "a", "SNOMED CT")],
    }
    # Inject a different source on the stub to confirm overwrite.
    per_system["SNOMED CT"][0]["source"] = "WRONG"

    state = {
        "parsed_conditions": [
            {"name": "X", "coding_systems": ["SNOMED"]}
        ],
    }
    with patch(
        "app.graph.nodes.chroma_retriever.search",
        _fake_search_factory(per_system),
    ):
        out = retrieve_from_chromadb(state)

    assert out["retrieved_codes"][0]["source"] == "ChromaDB"


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
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

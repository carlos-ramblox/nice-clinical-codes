"""Tests for backend/app/graph/nodes/bnf_retriever.py (T37)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.graph.nodes import bnf_retriever as bnf  # noqa: E402


_ROWS = [
    {
        "code": "0212000B0",
        "term": "Atorvastatin",
        "vocabulary": "BNF",
        "source": bnf.SOURCE_TAG,
        "domain": "Drug",
    },
    {
        "code": "0212000B0AAABAB",
        "term": "Atorvastatin 20mg tablets",
        "vocabulary": "BNF",
        "source": bnf.SOURCE_TAG,
        "domain": "Drug",
    },
]


def test_bnf_chapter_prefix_first_four_chars():
    assert bnf.bnf_chapter_prefix("0212000B0AAABAB") == "0212"
    assert bnf.bnf_chapter_prefix("0601022B0") == "0601"
    assert bnf.bnf_chapter_prefix("") == ""
    assert bnf.bnf_chapter_prefix(None) == ""  # type: ignore[arg-type]


def test_retrieve_fires_on_drug_domain():
    state = {"parsed_conditions": [{"name": "statins", "domain": "Drug"}]}
    with patch("app.graph.nodes.bnf_retriever.search_by_condition", return_value=_ROWS), \
         patch("app.graph.nodes.bnf_retriever.get_concept_id_for", return_value=None):
        out = bnf.retrieve_from_bnf(state)
    assert out["sources_queried"] == [bnf.SOURCE_TAG]
    assert len(out["retrieved_codes"]) == 2
    assert all(c["dmd_level"] is None for c in out["retrieved_codes"])
    assert all(c["vocabulary"] == "BNF" for c in out["retrieved_codes"])


def test_retrieve_skips_when_no_drug_condition():
    state = {"parsed_conditions": [{"name": "asthma", "domain": "Condition"}]}
    with patch("app.graph.nodes.bnf_retriever.search_by_condition") as stub:
        out = bnf.retrieve_from_bnf(state)
    stub.assert_not_called()
    assert out == {"retrieved_codes": [], "sources_queried": []}


def test_retrieve_filters_out_non_bnf_source_rows():
    rows = _ROWS + [{
        "code": "X9999",
        "term": "Atorvastatin SNOMED concept",
        "vocabulary": "SNOMED CT",
        "source": "OpenCodelists (Bennett Institute)",
        "domain": "Condition",
    }]
    state = {"parsed_conditions": [{"name": "statins", "domain": "Drug"}]}
    with patch("app.graph.nodes.bnf_retriever.search_by_condition", return_value=rows), \
         patch("app.graph.nodes.bnf_retriever.get_concept_id_for", return_value=None):
        out = bnf.retrieve_from_bnf(state)
    assert all(c["source"] == bnf.SOURCE_TAG for c in out["retrieved_codes"])
    assert len(out["retrieved_codes"]) == 2


def test_retrieve_empty_conditions():
    out = bnf.retrieve_from_bnf({"parsed_conditions": []})
    assert out == {"retrieved_codes": [], "sources_queried": []}

"""dm+d retriever: level heuristic, FR-008 gating, merger preservation."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.graph.nodes import dmd_retriever as dmd  # noqa: E402
from app.graph.nodes.result_merger import merge_and_dedup  # noqa: E402
from app.services.dmd_classification import infer_dmd_level  # noqa: E402


# --- infer_dmd_level() ------------------------------------------------------

def test_bare_chemical_is_ingredient():
    assert infer_dmd_level("Metformin") == "Ingredient"
    assert infer_dmd_level("Warfarin sodium") == "Ingredient"


def test_product_suffix_is_vtm():
    assert infer_dmd_level("Metformin product") == "VTM"
    assert infer_dmd_level("Warfarin sodium product") == "VTM"


def test_strength_form_is_vmp():
    assert infer_dmd_level("Metformin 500mg tablets") == "VMP"
    assert infer_dmd_level("Salbutamol 100micrograms/dose inhaler CFC free") == "VMP"
    assert infer_dmd_level("Paracetamol 250mg/5ml oral suspension paediatric sugar free") == "VMP"


def test_branded_holder_is_amp():
    assert infer_dmd_level("Glucophage 500mg tablets (Merck Serono Ltd)") == "AMP"
    assert infer_dmd_level("Lipitor 10mg tablets (Pfizer Ltd)") == "AMP"


def test_empty_term_returns_none():
    assert infer_dmd_level("") is None
    assert infer_dmd_level(None) is None
    assert infer_dmd_level("   ") is None
    assert infer_dmd_level("\t\n") is None


# --- retrieve_from_dmd() with stubbed SQLite --------------------------------

_ROWS = [
    {
        "code": "109081006",
        "term": "Metformin",
        "vocabulary": "dm+d",
        "source": dmd.SOURCE_TAG,
        "domain": "Drug",
    },
    {
        "code": "108774000",
        "term": "Metformin 500mg tablets",
        "vocabulary": "dm+d",
        "source": dmd.SOURCE_TAG,
        "domain": "Drug",
    },
]


def test_retrieve_fires_on_drug_domain():
    state = {"parsed_conditions": [{"name": "metformin", "domain": "Drug"}]}
    with patch("app.graph.nodes.dmd_retriever.search_by_condition", return_value=_ROWS), \
         patch("app.graph.nodes.dmd_retriever.get_concept_id_for", return_value=None):
        out = dmd.retrieve_from_dmd(state)
    assert out["sources_queried"] == [dmd.SOURCE_TAG]
    assert len(out["retrieved_codes"]) == 2
    levels = {c["code"]: c["dmd_level"] for c in out["retrieved_codes"]}
    assert levels["109081006"] == "Ingredient"
    assert levels["108774000"] == "VMP"


def test_retrieve_skips_when_no_drug_condition():
    """FR-008: disease queries see byte-identical empty output."""
    state = {"parsed_conditions": [{"name": "asthma", "domain": "Condition"}]}
    with patch("app.graph.nodes.dmd_retriever.search_by_condition") as stub:
        out = dmd.retrieve_from_dmd(state)
    stub.assert_not_called()
    assert out == {"retrieved_codes": [], "sources_queried": []}


def test_retrieve_skips_empty_conditions():
    out = dmd.retrieve_from_dmd({"parsed_conditions": []})
    assert out == {"retrieved_codes": [], "sources_queried": []}


def test_retrieve_filters_out_non_dmd_source_rows():
    """search_by_condition is a global LIKE; non-dm+d rows must be filtered out."""
    rows = _ROWS + [{
        "code": "12345",
        "term": "Metformin SNOMED concept",
        "vocabulary": "SNOMED CT",
        "source": "OpenCodelists (Bennett Institute)",
        "domain": "Condition",
    }]
    state = {"parsed_conditions": [{"name": "metformin", "domain": "Drug"}]}
    with patch("app.graph.nodes.dmd_retriever.search_by_condition", return_value=rows), \
         patch("app.graph.nodes.dmd_retriever.get_concept_id_for", return_value=None):
        out = dmd.retrieve_from_dmd(state)
    assert all(c["source"] == dmd.SOURCE_TAG for c in out["retrieved_codes"])
    assert len(out["retrieved_codes"]) == 2


# --- merger preserves dmd_level through dedup -------------------------------

def test_merger_preserves_dmd_level_for_single_source_row():
    state = {"retrieved_codes": [{
        "code": "108774000",
        "term": "Metformin 500mg tablets",
        "vocabulary": "dm+d",
        "source": dmd.SOURCE_TAG,
        "domain": "Drug",
        "similarity_score": None,
        "dmd_level": "VMP",
    }]}
    out = merge_and_dedup(state)
    assert out["enriched_codes"][0]["dmd_level"] == "VMP"


def test_merger_promotes_dmd_level_when_only_second_source_carries_it():
    """A dm+d row arriving after a non-dm+d duplicate must upgrade None → VMP."""
    state = {"retrieved_codes": [
        {
            "code": "108774000",
            "term": "Metformin 500mg tablets",
            "vocabulary": "dm+d",
            "source": "OMOPHub",
            "domain": "Drug",
            "similarity_score": None,
            "dmd_level": None,
        },
        {
            "code": "108774000",
            "term": "Metformin 500mg tablets",
            "vocabulary": "dm+d",
            "source": dmd.SOURCE_TAG,
            "domain": "Drug",
            "similarity_score": None,
            "dmd_level": "VMP",
        },
    ]}
    out = merge_and_dedup(state)
    assert len(out["enriched_codes"]) == 1
    assert out["enriched_codes"][0]["dmd_level"] == "VMP"
    assert out["enriched_codes"][0]["source_count"] == 2

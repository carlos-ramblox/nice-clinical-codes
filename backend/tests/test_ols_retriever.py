"""Tests for backend/app/graph/nodes/ols_retriever.py (issue #25)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.graph.nodes import ols_retriever as ols  # noqa: E402


def _resp(docs: list[dict]) -> MagicMock:
    """Build a fake requests.Response wrapping an OLS4 search payload."""
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"response": {"numFound": len(docs), "docs": docs}}
    return r


_MONDO_DOC = {
    "iri": "http://purl.obolibrary.org/obo/MONDO_0007154",
    "label": "arteriovenous malformations of the brain",
    "obo_id": "MONDO:0007154",
    "short_form": "MONDO_0007154",
    "ontology_name": "efo",
}


def test_vocab_from_obo_id_uses_prefix():
    assert ols._vocab_from_obo_id("MONDO:0007154") == "MONDO"
    assert ols._vocab_from_obo_id("Orphanet:46724") == "Orphanet"
    assert ols._vocab_from_obo_id("HP:0006548") == "HP"
    assert ols._vocab_from_obo_id("EFO_0001360") == "EFO_0001360"  # no colon


def test_efo_mapping_derives_vocab_from_prefix():
    state = {"parsed_conditions": [{"name": "arteriovenous malformation",
                                    "condition_type": "primary"}]}
    with patch("app.graph.nodes.ols_retriever.requests.get",
               return_value=_resp([_MONDO_DOC])):
        out = ols.retrieve_from_ols(state)

    assert out["sources_queried"] == ["OLS4 (EFO)"]
    assert len(out["retrieved_codes"]) == 1
    c = out["retrieved_codes"][0]
    assert c["code"] == "MONDO:0007154"
    assert c["vocabulary"] == "MONDO"          # NOT "EFO"
    assert c["source"] == "OLS4 (EFO)"
    assert c["domain"] == "Phenotype"
    assert c["term"] == "arteriovenous malformations of the brain"
    assert c["iri"] == _MONDO_DOC["iri"]
    assert c["similarity_score"] is None


def test_oae_queried_for_comorbidity_only():
    state = {"parsed_conditions": [
        {"name": "diabetes", "condition_type": "primary"},
        {"name": "rash", "condition_type": "comorbidity"},
    ]}
    with patch("app.graph.nodes.ols_retriever.requests.get",
               return_value=_resp([])) as g:
        out = ols.retrieve_from_ols(state)

    ontologies = [kw["params"]["ontology"] for _, kw in g.call_args_list]
    # EFO for both conditions, OAE only for the comorbidity
    assert ontologies.count("efo") == 2
    assert ontologies.count("oae") == 1
    # confirm OAE was paired with the comorbidity term, not the primary
    oae_terms = [kw["params"]["q"] for _, kw in g.call_args_list
                 if kw["params"]["ontology"] == "oae"]
    assert oae_terms == ["rash"]
    assert out["sources_queried"] == ["OLS4 (EFO)", "OLS4 (OAE)"]


def test_no_oae_without_comorbidity():
    state = {"parsed_conditions": [{"name": "asthma", "condition_type": "primary"}]}
    with patch("app.graph.nodes.ols_retriever.requests.get",
               return_value=_resp([])):
        out = ols.retrieve_from_ols(state)
    assert out["sources_queried"] == ["OLS4 (EFO)"]


def test_obsolete_terms_dropped():
    docs = [_MONDO_DOC, {**_MONDO_DOC, "obo_id": "MONDO:9999999",
                         "is_obsolete": True}]
    state = {"parsed_conditions": [{"name": "x", "condition_type": "primary"}]}
    with patch("app.graph.nodes.ols_retriever.requests.get",
               return_value=_resp(docs)):
        out = ols.retrieve_from_ols(state)
    codes = {c["code"] for c in out["retrieved_codes"]}
    assert codes == {"MONDO:0007154"}


def test_short_form_fallback_and_skip_when_no_code_or_label():
    docs = [
        {"label": "no obo_id here", "short_form": "EFO_0001360",
         "iri": "http://x/EFO_0001360"},                       # falls back to short_form
        {"obo_id": "MONDO:1", "iri": "http://x/1"},            # no label -> skipped
        {"label": "no code at all", "iri": "http://x/2"},      # no code -> skipped
    ]
    state = {"parsed_conditions": [{"name": "x", "condition_type": "primary"}]}
    with patch("app.graph.nodes.ols_retriever.requests.get",
               return_value=_resp(docs)):
        out = ols.retrieve_from_ols(state)
    assert len(out["retrieved_codes"]) == 1
    c = out["retrieved_codes"][0]
    assert c["code"] == "EFO_0001360"
    assert c["vocabulary"] == "EFO_0001360"  # no colon -> whole string


def test_graceful_degradation_on_timeout():
    state = {"parsed_conditions": [{"name": "x", "condition_type": "primary"}]}
    with patch("app.graph.nodes.ols_retriever.requests.get",
               side_effect=requests.Timeout("boom")):
        out = ols.retrieve_from_ols(state)
    # well-formed return, no exception; EFO still reported as queried
    assert out["retrieved_codes"] == []
    assert out["sources_queried"] == ["OLS4 (EFO)"]


def test_empty_conditions():
    out = ols.retrieve_from_ols({"parsed_conditions": []})
    assert out == {"retrieved_codes": [], "sources_queried": []}

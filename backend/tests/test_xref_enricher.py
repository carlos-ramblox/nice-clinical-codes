"""Tests for backend/app/graph/nodes/xref_enricher.py (issue #25, Stage B)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.graph.nodes import xref_enricher as xr  # noqa: E402


def _td_resp(xrefs: list[dict]) -> MagicMock:
    """Fake requests.Response wrapping an OLS4 term-detail payload."""
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"_embedded": {"terms": [{"obo_xref": xrefs}]}}
    return r


def _ols_concept(**over) -> dict:
    base = {
        "code": "MONDO:0007154",
        "term": "arteriovenous malformations of the brain",
        "vocabulary": "MONDO",
        "source": "OLS4 (EFO)",
        "domain": "Phenotype",
        "sources": ["OLS4 (EFO)"],
        "source_count": 1,
        "iri": "http://purl.obolibrary.org/obo/MONDO_0007154",
    }
    base.update(over)
    return base


# --- _predicate_ok (per-ontology formats) --------------------------------

def test_predicate_ok_handles_both_ontology_formats():
    assert xr._predicate_ok("MONDO:equivalentTo") is True
    assert xr._predicate_ok("Orphanet:46724/e") is True       # /e == Exact
    assert xr._predicate_ok("Orphanet:46724/ntbt") is False   # narrower
    assert xr._predicate_ok("MONDO:broadMatch") is False
    assert xr._predicate_ok("") is False


# --- mapping -------------------------------------------------------------

def test_snomed_mapping_from_sctid_xref():
    state = {"enriched_codes": [_ols_concept()]}
    xrefs = [{"database": "SCTID", "id": "234142008",
              "description": "MONDO:equivalentTo"}]
    with patch("app.graph.nodes.xref_enricher.requests.get",
               return_value=_td_resp(xrefs)):
        out = xr.enrich_with_xrefs(state)

    minted = [c for c in out["enriched_codes"] if c["vocabulary"] == "SNOMED CT"]
    assert len(minted) == 1
    m = minted[0]
    assert m["code"] == "234142008"
    assert m["term"] == "arteriovenous malformations of the brain"
    assert m["source"] == "OLS4 xref (SCTID)"
    assert m["sources"] == ["OLS4 xref (SCTID)"]
    assert m["domain"] == "Condition"
    assert m["concept_id"] is None
    assert m["usage_status"] == "not_in_dataset"


def test_non_snomed_xrefs_ignored():
    state = {"enriched_codes": [_ols_concept()]}
    xrefs = [
        {"database": "ICD10", "id": "Q28.2", "description": "Orphanet:46724/e"},
        {"database": "UMLS", "id": "C0917804", "description": "MONDO:equivalentTo"},
        {"database": "icd11.foundation", "id": "153256729", "description": "x:equivalentTo"},
    ]
    with patch("app.graph.nodes.xref_enricher.requests.get",
               return_value=_td_resp(xrefs)):
        out = xr.enrich_with_xrefs(state)
    # only the original phenotype remains; nothing minted
    assert all(c["vocabulary"] != "SNOMED CT" for c in out["enriched_codes"])
    assert all(c["vocabulary"] != "ICD-10 (WHO)" for c in out["enriched_codes"])
    assert len(out["enriched_codes"]) == 1


def test_predicate_filter_drops_loose_mappings():
    state = {"enriched_codes": [_ols_concept()]}
    xrefs = [{"database": "SCTID", "id": "111", "description": "MONDO:broadMatch"}]
    with patch("app.graph.nodes.xref_enricher.requests.get",
               return_value=_td_resp(xrefs)):
        out = xr.enrich_with_xrefs(state)
    assert all(c["vocabulary"] != "SNOMED CT" for c in out["enriched_codes"])


def test_malformed_sctid_rejected():
    """A non-numeric SCTID from the external API must not be minted as a code."""
    state = {"enriched_codes": [_ols_concept()]}
    xrefs = [{"database": "SCTID", "id": "234142008; DROP TABLE x;--",
              "description": "MONDO:equivalentTo"}]
    with patch("app.graph.nodes.xref_enricher.requests.get",
               return_value=_td_resp(xrefs)):
        out = xr.enrich_with_xrefs(state)
    assert all(c["vocabulary"] != "SNOMED CT" for c in out["enriched_codes"])
    assert len(out["enriched_codes"]) == 1  # only the original phenotype remains


def test_corroborates_existing_snomed_code_no_duplicate():
    existing_snomed = {
        "code": "234142008", "term": "AVM of brain (SNOMED)",
        "vocabulary": "SNOMED CT", "source": "OMOPHub",
        "sources": ["OMOPHub"], "source_count": 1,
    }
    state = {"enriched_codes": [_ols_concept(), existing_snomed]}
    xrefs = [{"database": "SCTID", "id": "234142008",
              "description": "MONDO:equivalentTo"}]
    with patch("app.graph.nodes.xref_enricher.requests.get",
               return_value=_td_resp(xrefs)):
        out = xr.enrich_with_xrefs(state)

    snomed = [c for c in out["enriched_codes"] if c["vocabulary"] == "SNOMED CT"]
    assert len(snomed) == 1                      # no duplicate row
    assert "OMOPHub" in snomed[0]["sources"]
    assert "OLS4 xref (SCTID)" in snomed[0]["sources"]
    assert snomed[0]["source_count"] == 2


# --- gating + degradation ------------------------------------------------

def test_passthrough_when_disabled():
    state = {"enriched_codes": [_ols_concept()]}
    with patch("app.graph.nodes.xref_enricher.OLS4_XREF_ENRICH", False), \
         patch("app.graph.nodes.xref_enricher.requests.get") as g:
        out = xr.enrich_with_xrefs(state)
    g.assert_not_called()
    assert out == {}


def test_passthrough_when_no_ols_concepts():
    state = {"enriched_codes": [{"code": "X", "vocabulary": "SNOMED CT",
                                 "source": "OMOPHub", "term": "x"}]}
    with patch("app.graph.nodes.xref_enricher.requests.get") as g:
        out = xr.enrich_with_xrefs(state)
    g.assert_not_called()
    assert out == {}


def test_graceful_degradation_on_timeout():
    state = {"enriched_codes": [_ols_concept()]}
    with patch("app.graph.nodes.xref_enricher.requests.get",
               side_effect=requests.Timeout("boom")):
        out = xr.enrich_with_xrefs(state)
    # no crash; phenotype unchanged, nothing minted
    assert all(c["vocabulary"] != "SNOMED CT" for c in out["enriched_codes"])
    assert len(out["enriched_codes"]) == 1


def test_caps_term_detail_calls():
    concepts = [_ols_concept(code=f"MONDO:{i}", iri=f"http://x/MONDO_{i}")
                for i in range(5)]
    state = {"enriched_codes": concepts}
    with patch("app.graph.nodes.xref_enricher.OLS4_XREF_MAX_CONCEPTS", 2), \
         patch("app.graph.nodes.xref_enricher.requests.get",
               return_value=_td_resp([])) as g:
        xr.enrich_with_xrefs(state)
    assert g.call_count == 2


def test_post_mint_cap_ranks_by_stable_key_not_alphabetical():
    """When minting pushes the list over MAX_CANDIDATES, the cap must rank by
    _stable_sort_key (source_count/score), not alphabetical code order — so a
    corroborated candidate isn't evicted just because its code sorts late."""
    # _ols_concept mints one SNOMED code, so `added` > 0 triggers the cap.
    high_value = {"code": "zzz999999", "term": "corroborated",
                  "vocabulary": "SNOMED CT", "source": "OMOPHub",
                  "sources": ["OMOPHub", "QOF"], "source_count": 2,
                  "similarity_score": 0.9}
    state = {"enriched_codes": [_ols_concept(), high_value]}
    xrefs = [{"database": "SCTID", "id": "234142008",
              "description": "MONDO:equivalentTo"}]
    with patch("app.graph.nodes.xref_enricher.MAX_CANDIDATES", 1), \
         patch("app.graph.nodes.xref_enricher.requests.get",
               return_value=_td_resp(xrefs)):
        out = xr.enrich_with_xrefs(state)
    # cap=1 keeps only the top-ranked candidate: high source_count wins despite
    # its lexicographically-late code (alphabetical sort would have dropped it).
    assert [c["code"] for c in out["enriched_codes"]] == ["zzz999999"]

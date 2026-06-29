"""Opt-in live smoke test for the OLS4 integration (issue #25).

Hits the real EBI OLS4 endpoints to confirm the live response shapes still
match what `ols_retriever` and `xref_enricher` parse. Skipped by default —
set RUN_OLS4_LIVE=1 to run:

    RUN_OLS4_LIVE=1 pytest tests/test_ols4_live.py -q

Network-dependent and therefore excluded from the offline suite / CI.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.graph.nodes import ols_retriever as ols  # noqa: E402
from app.graph.nodes import xref_enricher as xr  # noqa: E402

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("RUN_OLS4_LIVE"),
        reason="set RUN_OLS4_LIVE=1 to run the live OLS4 smoke test",
    ),
]


def test_ols4_search_and_xref_live():
    """EFO search returns concepts, and term-detail xref yields SNOMED CT.

    Uses a stable, well-mapped concept (type 2 diabetes mellitus →
    MONDO:0005148 → SNOMED CT 44054006) so the assertions don't drift with
    ontology content changes.
    """
    concepts = ols._search_ols("type 2 diabetes mellitus", "efo")
    assert concepts, "expected >=1 EFO concept from live OLS4 search"

    # the merged EFO graph surfaces the MONDO disease concept for T2DM
    t2dm = next((c for c in concepts if c["code"] == "MONDO:0005148"), None)
    assert t2dm is not None, "expected MONDO:0005148 in live EFO results"
    assert t2dm["vocabulary"] == "MONDO"      # derived from obo_id prefix
    assert t2dm["domain"] == "Phenotype"
    assert t2dm["iri"], "concept must carry an IRI for xref lookup"

    # live term-detail → SNOMED CT mapping via obo_xref
    minted = xr._fetch_xrefs(t2dm)
    snomed = [m for m in minted if m["vocabulary"] == "SNOMED CT"]
    assert snomed, "expected a SNOMED CT xref for MONDO:0005148"
    assert all(m["code"].isdigit() for m in snomed)  # SCTIDs are numeric

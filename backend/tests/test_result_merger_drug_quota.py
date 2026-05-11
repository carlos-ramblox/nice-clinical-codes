"""T37g tests: per-vocab quota in result_merger.

Three properties:
  1. drug-condition + saturated SNOMED pool -> drug rows still surface
  2. disease-only condition + saturated pool -> behaviour byte-identical
     to the pre-T37g path
  3. final sort order is deterministic on equal keys (stable tiebreaker)
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.config import DRUG_VOCAB_QUOTA, MAX_CANDIDATES  # noqa: E402
from app.graph.nodes.result_merger import merge_and_dedup  # noqa: E402


def _snomed_row(i: int, source_count: int = 2) -> dict:
    return {
        "code": f"S{i:05d}",
        "term": f"snomed term {i}",
        "vocabulary": "SNOMED CT",
        "source": "OMOPHub",
        "domain": "Drug",
        "similarity_score": 0.5,
        "usage_frequency": None,
        # The merger increments source_count via repeated entries, so we
        # emit `source_count` separate row-dicts for the same key.
    }


def _drug_row(code: str, vocab: str, level: str | None = None) -> dict:
    return {
        "code": code,
        "term": f"{vocab} term {code}",
        "vocabulary": vocab,
        "source": f"OpenCodelists ({vocab})",
        "domain": "Drug",
        "similarity_score": None,
        "usage_frequency": None,
        "dmd_level": level,
    }


def test_drug_condition_quota_protects_dmd_and_bnf_against_saturated_snomed():
    """When a drug query also surfaces > MAX_CANDIDATES SNOMED rows from
    OMOPHub + ChromaDB, the quota must reserve slots for dm+d and BNF."""
    snomed = []
    for i in range(MAX_CANDIDATES + 50):
        snomed.append(_snomed_row(i))
        snomed.append({**_snomed_row(i), "source": "ChromaDB"})

    dmd = [_drug_row(f"D{i:05d}", "dm+d", "VMP") for i in range(20)]
    bnf = [_drug_row(f"B{i:05d}", "BNF") for i in range(20)]

    state = {
        "retrieved_codes": snomed + dmd + bnf,
        "parsed_conditions": [{"name": "metformin", "domain": "Drug"}],
    }
    out = merge_and_dedup(state)["enriched_codes"]

    dmd_in = [c for c in out if c["vocabulary"] == "dm+d"]
    bnf_in = [c for c in out if c["vocabulary"] == "BNF"]
    assert len(dmd_in) == DRUG_VOCAB_QUOTA, f"expected {DRUG_VOCAB_QUOTA} dm+d rows, got {len(dmd_in)}"
    assert len(bnf_in) == DRUG_VOCAB_QUOTA, f"expected {DRUG_VOCAB_QUOTA} BNF rows, got {len(bnf_in)}"
    assert len(out) == MAX_CANDIDATES


def test_disease_condition_quota_path_does_not_fire():
    """No `domain==Drug` parsed condition -> quota path is bypassed,
    behaviour matches the pre-T37g sort-and-cap exactly."""
    snomed_dups = []
    for i in range(MAX_CANDIDATES + 20):
        snomed_dups.append(_snomed_row(i))
        snomed_dups.append({**_snomed_row(i), "source": "ChromaDB"})

    # Synthetic dm+d row included only as a smoke check; should be dropped
    # because source_count=1 against a saturated SNOMED pool.
    dmd_distractor = [_drug_row("D99999", "dm+d", "VMP")]

    state = {
        "retrieved_codes": snomed_dups + dmd_distractor,
        "parsed_conditions": [{"name": "asthma", "domain": "Condition"}],
    }
    out = merge_and_dedup(state)["enriched_codes"]

    dmd_in = [c for c in out if c["vocabulary"] == "dm+d"]
    assert len(out) == MAX_CANDIDATES
    assert len(dmd_in) == 0, "disease query must take the unchanged code path"


def test_quota_sort_is_deterministic_across_two_merges():
    """Determinism: running the same merger over the same input twice
    produces the same order (no rowid / set / dict iteration leakage)."""
    rows = []
    for i in range(20):
        rows.append(_snomed_row(i))
        rows.append({**_snomed_row(i), "source": "ChromaDB"})
    rows.extend(_drug_row(f"D{i:05d}", "dm+d", "VMP") for i in range(20))
    rows.extend(_drug_row(f"B{i:05d}", "BNF") for i in range(20))

    state = {
        "retrieved_codes": rows,
        "parsed_conditions": [{"name": "metformin", "domain": "Drug"}],
    }
    first = [(c["code"], c["vocabulary"]) for c in merge_and_dedup(state)["enriched_codes"]]
    second = [(c["code"], c["vocabulary"]) for c in merge_and_dedup(state)["enriched_codes"]]
    assert first == second


def test_quota_under_saturated_drug_pool_caps_at_quota_per_vocab():
    """If 50 dm+d rows are fed in, only DRUG_VOCAB_QUOTA survive the
    reservation. The remainder competes in the fill pool against other
    vocabs by source_count / similarity_score."""
    dmd = [_drug_row(f"D{i:05d}", "dm+d", "VMP") for i in range(50)]
    state = {
        "retrieved_codes": dmd,
        "parsed_conditions": [{"name": "metformin", "domain": "Drug"}],
    }
    out = merge_and_dedup(state)["enriched_codes"]
    # No SNOMED competing -> the fill pool is empty SNOMED + leftover dm+d,
    # so all 50 dm+d should be kept (well under MAX_CANDIDATES).
    dmd_in = [c for c in out if c["vocabulary"] == "dm+d"]
    assert len(dmd_in) == 50

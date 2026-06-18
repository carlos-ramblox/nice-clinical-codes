"""Tests for the concept_code + vocabulary guard in ``omophub_to_retrieved_codes``.

Ticket #35: the previous implementation silently fell back to the OMOP
internal integer ``concept_id`` when ``concept_code`` was missing, leaking
non-clinical identifiers into the merger and downstream output. The guard
must drop those rows entirely, log an aggregated WARNING per failure type,
and reject any vocabulary that is not in the OMOP-side clinical allowlist.

The two audit-regression tests at the bottom cover the pandas-NaN cases
the first-pass guard missed (``_vocabulary_label`` short-circuiting the
``or`` chain, ``domain_id`` slipping past ``dict.get`` defaults).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("OMOPHUB_API_KEY", "dummy-test-key")

from app.graph.nodes.omophub_retriever import omophub_to_retrieved_codes  # noqa: E402


def _df(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def _valid_row(code: str = "44054006", vocab: str = "SNOMED CT") -> dict:
    return {
        "concept_code": code,
        "concept_id": 4193704,
        "concept_name": "Type 2 diabetes mellitus",
        "_vocabulary_label": vocab,
        "domain_id": "Condition",
    }


@pytest.mark.parametrize("bad_code_field", [
    pytest.param({}, id="key_absent"),
    pytest.param({"concept_code": None}, id="none"),
    pytest.param({"concept_code": ""}, id="empty_string"),
    pytest.param({"concept_code": "   "}, id="whitespace_only"),
])
def test_falsy_concept_code_drops_row(bad_code_field):
    """Any falsy concept_code — missing key, None, empty, whitespace — drops the row."""
    row = {
        "concept_id": 4193704,
        "concept_name": "Type 2 diabetes mellitus",
        "_vocabulary_label": "SNOMED CT",
        "domain_id": "Condition",
        **bad_code_field,
    }
    assert omophub_to_retrieved_codes(_df(row)) == []


def test_non_clinical_vocabulary_drops_row():
    df = _df({
        "concept_code": "X99",
        "concept_id": 4193708,
        "concept_name": "BadVocab record",
        "_vocabulary_label": "OMOP Genomic",
        "domain_id": "Condition",
    })
    assert omophub_to_retrieved_codes(df) == []


def test_valid_snomed_row_passes_through():
    df = _df(_valid_row())
    out = omophub_to_retrieved_codes(df)
    assert len(out) == 1
    assert out[0]["code"] == "44054006"
    assert out[0]["vocabulary"] == "SNOMED CT"
    assert out[0]["concept_id"] == 4193704


def test_valid_icd10_row_passes_through():
    df = _df(_valid_row(code="E11.9", vocab="ICD-10 (WHO)"))
    out = omophub_to_retrieved_codes(df)
    assert len(out) == 1
    assert out[0]["code"] == "E11.9"
    assert out[0]["vocabulary"] == "ICD-10 (WHO)"


def test_valid_opcs4_row_passes_through():
    df = _df(_valid_row(code="O50.0", vocab="OPCS-4"))
    out = omophub_to_retrieved_codes(df)
    assert len(out) == 1
    assert out[0]["code"] == "O50.0"
    assert out[0]["vocabulary"] == "OPCS-4"


def test_nan_vocabulary_label_falls_back_to_query_vocabulary_key():
    """Audit regression: pandas NaN in _vocabulary_label is truthy and would
    short-circuit the ``or`` chain. The fallback also has to remap the OMOP
    ``_query_vocabulary`` *key* through OMOPHUB_VOCABULARIES to get the *label*
    — without that, a legitimate ICD-10 row would be silently dropped."""
    df = _df(
        {"concept_code": "E11.9", "concept_id": 2, "concept_name": "T2DM",
         "_query_vocabulary": "ICD10", "domain_id": "Condition"},
    )
    out = omophub_to_retrieved_codes(df)
    assert len(out) == 1, f"NaN _vocabulary_label + ICD10 _query_vocabulary should pass through; got {out}"
    assert out[0]["vocabulary"] == "ICD-10 (WHO)"


def test_nan_domain_id_becomes_unknown_not_string_nan():
    """Audit regression: ``dict.get(..., 'Unknown')`` only uses the default for
    a missing key, not a pandas NaN value. A NaN domain_id was leaking through
    as the literal string ``'nan'`` in JSON output."""
    df = _df(
        {"concept_code": "44054006", "concept_id": 1, "concept_name": "T2DM",
         "_vocabulary_label": "SNOMED CT"},
    )
    out = omophub_to_retrieved_codes(df)
    assert len(out) == 1
    assert out[0]["domain"] == "Unknown"


def test_aggregated_warning_on_missing_concept_code(caplog):
    df = _df({
        "concept_id": 9999999,
        "concept_name": "Some condition",
        "_vocabulary_label": "SNOMED CT",
        "domain_id": "Condition",
    })
    with caplog.at_level(logging.WARNING, logger="app.graph.nodes.omophub_retriever"):
        omophub_to_retrieved_codes(df)
    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "missing concept_code" in m and "9999999" in m and "Some condition" in m
        for m in warn_msgs
    )


def test_aggregated_warning_on_bad_vocabulary(caplog):
    df = _df({
        "concept_code": "X99",
        "concept_id": 8888888,
        "concept_name": "Non-clinical record",
        "_vocabulary_label": "OMOP Genomic",
        "domain_id": "Condition",
    })
    with caplog.at_level(logging.WARNING, logger="app.graph.nodes.omophub_retriever"):
        omophub_to_retrieved_codes(df)
    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "non-clinical vocabulary" in m and "8888888" in m and "OMOP Genomic" in m
        for m in warn_msgs
    )


def test_n_dirty_rows_produce_one_aggregated_warning_not_n(caplog):
    """20 missing-code rows must produce ONE aggregated WARNING, not 20.
    This is the cap=500 log-volume guard from the architecture audit."""
    rows = [
        {"concept_id": i, "concept_name": f"row-{i}",
         "_vocabulary_label": "SNOMED CT", "domain_id": "Condition"}
        for i in range(20)
    ]
    df = pd.DataFrame(rows)
    with caplog.at_level(logging.WARNING, logger="app.graph.nodes.omophub_retriever"):
        omophub_to_retrieved_codes(df)
    missing_warns = [r for r in caplog.records
                     if r.levelno == logging.WARNING and "missing concept_code" in r.message]
    assert len(missing_warns) == 1, f"expected 1 aggregated WARNING, got {len(missing_warns)}"
    assert "20" in missing_warns[0].message


def test_no_omop_concept_id_ever_appears_as_code():
    df = _df(
        {"concept_id": 4193704, "concept_name": "missing_concept_code_row",
         "_vocabulary_label": "SNOMED CT", "domain_id": "Condition"},
        {"concept_code": None, "concept_id": 4193705, "concept_name": "none_row",
         "_vocabulary_label": "SNOMED CT", "domain_id": "Condition"},
        _valid_row(),
    )
    out = omophub_to_retrieved_codes(df)
    assert all(r["code"] != "4193704" and r["code"] != "4193705" for r in out)
    assert all(r["code"] not in ("nan", "None", "") for r in out)


def test_mixed_input_keeps_only_valid_clinical_rows():
    df = _df(
        _valid_row(code="44054006", vocab="SNOMED CT"),
        _valid_row(code="E11.9", vocab="ICD-10 (WHO)"),
        {"concept_code": None, "concept_id": 1, "concept_name": "drop",
         "_vocabulary_label": "SNOMED CT", "domain_id": "Condition"},
        {"concept_code": "Z99", "concept_id": 2, "concept_name": "bad vocab",
         "_vocabulary_label": "OMOP Genomic", "domain_id": "Condition"},
    )
    out = omophub_to_retrieved_codes(df)
    assert [(r["code"], r["vocabulary"]) for r in out] == [
        ("44054006", "SNOMED CT"),
        ("E11.9", "ICD-10 (WHO)"),
    ]


def test_concept_name_with_newline_is_escaped_in_warning(caplog):
    """Log-injection guard from the security audit: a malicious concept_name
    containing newlines must NOT produce a fake second log entry; %r-formatting
    via _clean_str + !r escapes control characters."""
    df = _df({
        "concept_id": 7777777,
        "concept_name": "Real condition\nINFO 2099-01-01 FAKE LOG ENTRY",
        "_vocabulary_label": "SNOMED CT",
        "domain_id": "Condition",
    })
    with caplog.at_level(logging.WARNING, logger="app.graph.nodes.omophub_retriever"):
        omophub_to_retrieved_codes(df)
    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("missing concept_code" in m for m in warn_msgs)
    # The injected literal "\n" must appear as the two-char sequence \n (escaped),
    # not as a real newline that breaks the log into two lines.
    assert all("\nINFO 2099" not in m for m in warn_msgs), \
        "newline in concept_name leaked unescaped into the WARNING message"

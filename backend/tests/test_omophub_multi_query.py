"""
Tests for the multi-query expansion in
backend/app/graph/nodes/omophub_retriever.py (Fix E).

Two tiers of test:
  - query_variants(): pure-string logic, runs offline.
  - search_omophub(): exercised against a stubbed OMOPHub client so we
    can assert on dedup + cap behaviour without network or an API key.

Run with pytest from backend/, or as a script:
    python -m tests.test_omophub_multi_query
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

# Allow `import app.*` whether the test is invoked from backend/ or the repo
# root (mirrors test_query_parser_vocab.py).
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# search_omophub() raises if OMOPHUB_API_KEY is unset; the stubbed
# client is patched in below so the value here is just a sentinel.
os.environ.setdefault("OMOPHUB_API_KEY", "dummy-test-key")

from app.graph.nodes import omophub_retriever as omr  # noqa: E402


# --- query_variants() tier --------------------------------------------------

def test_no_clinical_prefix_yields_all_variants():
    v = omr.query_variants("Myocardial infarction")
    assert v == ["Myocardial infarction", "Acute Myocardial infarction", "Chronic Myocardial infarction"]


def test_query_already_starts_with_acute_skips_double_prefix():
    v = omr.query_variants("Acute myocardial infarction")
    # raw + Chronic only — "Acute Acute …" is suppressed
    assert v == ["Acute myocardial infarction", "Chronic Acute myocardial infarction"]
    assert all(not x.lower().startswith("acute acute") for x in v)


def test_query_already_starts_with_chronic_skips_double_prefix():
    v = omr.query_variants("Chronic kidney disease")
    assert v == ["Chronic kidney disease", "Acute Chronic kidney disease"]
    assert all(not x.lower().startswith("chronic chronic") for x in v)


def test_query_starts_with_acute_substring_but_different_word():
    # "Acuteness" starts with "acute" but as a substring, not a separate
    # word. The expansion should still issue "Acute Acuteness" because we
    # check for "acute " (with trailing space).
    v = omr.query_variants("Acuteness of hearing")
    assert "Acute Acuteness of hearing" in v


def test_empty_query_yields_empty_list():
    assert omr.query_variants("") == []
    assert omr.query_variants("   ") == []


def test_case_insensitive_prefix_check():
    v = omr.query_variants("ACUTE pancreatitis")
    # raw + Chronic; the leading "ACUTE" is recognised in lowercase
    # comparison so the "Acute ACUTE pancreatitis" duplicate is skipped.
    assert "Acute ACUTE pancreatitis" not in v
    assert "Chronic ACUTE pancreatitis" in v


# --- search_omophub() tier with stubbed client ------------------------------

class _FakeClient:
    """Minimal stand-in for omophub.OMOPHub used by search_omophub()."""

    def __init__(self, behaviour):
        # behaviour: dict[(variant, vocab_id) -> list[dict]] of fake rows
        self._behaviour = behaviour
        self.search = self  # search.basic resolves on the same object

    def basic(self, search_term, **kwargs):
        # vocabulary_ids is always a single-element list in our caller
        vid = kwargs["vocabulary_ids"][0]
        return list(self._behaviour.get((search_term, vid), []))


def _row(code, vocab_id, name="x"):
    return {"concept_code": code, "concept_name": name, "domain_id": "Condition", "_vocab": vocab_id}


def _patched_search(behaviour):
    """Return a context manager that patches OMOPHub() to a _FakeClient."""
    return patch.object(omr, "OMOPHub", lambda **_: _FakeClient(behaviour))


def test_dedup_across_variants_collapses_duplicate_codes():
    # The same (code, vocab) pair appears in both "X" and "Acute X" results.
    # Expect exactly one row per (code, vocab) after dedup.
    behaviour = {
        ("Foo", "ICD10cm"):              [_row("I21", "ICD10cm"), _row("I22", "ICD10cm")],
        ("Acute Foo", "ICD10cm"):        [_row("I21", "ICD10cm"), _row("I23", "ICD10cm")],
        ("Chronic Foo", "ICD10cm"):      [_row("I21", "ICD10cm")],
    }
    with _patched_search(behaviour):
        df = omr.search_omophub("Foo", vocabularies={"ICD10cm": "ICD-10-CM"}, page_size=20)
    codes = sorted(df["concept_code"].tolist())
    assert codes == ["I21", "I22", "I23"], codes


def test_same_code_in_different_vocabs_kept():
    # The dedup key is (code, vocab) so "I21" in ICD10cm and "I21" in
    # SNOMEDCT must both appear.
    behaviour = {
        ("Foo", "ICD10cm"):  [_row("I21", "ICD10cm")],
        ("Foo", "SNOMEDCT"): [_row("I21", "SNOMEDCT", name="snomed-impostor")],
        ("Acute Foo", "ICD10cm"):  [],
        ("Acute Foo", "SNOMEDCT"): [],
        ("Chronic Foo", "ICD10cm"):  [],
        ("Chronic Foo", "SNOMEDCT"): [],
    }
    with _patched_search(behaviour):
        df = omr.search_omophub("Foo", vocabularies={"ICD10cm": "ICD-10-CM", "SNOMEDCT": "SNOMED CT"}, page_size=20)
    assert len(df) == 2


def test_cap_respected_for_single_vocab_three_variants():
    # Each variant returns 25 unique codes per vocab. With one vocab and
    # three variants the cap is page_size * 3 * 1 = 60.
    def _bulk(prefix, vid):
        return [_row(f"{prefix}-{i}", vid) for i in range(25)]

    behaviour = {
        ("Bar", "ICD10cm"):         _bulk("R",  "ICD10cm"),
        ("Acute Bar", "ICD10cm"):   _bulk("A",  "ICD10cm"),
        ("Chronic Bar", "ICD10cm"): _bulk("C",  "ICD10cm"),
    }
    with _patched_search(behaviour):
        df = omr.search_omophub("Bar", vocabularies={"ICD10cm": "ICD-10-CM"}, page_size=20)
    assert len(df) == 60
    codes = df["concept_code"].tolist()
    assert codes[:25] == [f"R-{i}" for i in range(25)]


def test_cap_scales_with_vocab_count():
    # Two vocabs and three variants — the theoretical no-overlap upper
    # bound is page_size * 3 * 2 = 120. Each (variant, vocab) tuple here
    # returns 25 unique codes (75 per vocab; 150 total). The cap should
    # let through 120, not the old hardcoded 60.
    def _bulk(prefix, vid):
        return [_row(f"{prefix}-{vid}-{i}", vid) for i in range(25)]

    pairs = [(v, vid) for v in ["Foo", "Acute Foo", "Chronic Foo"]
                      for vid in ["ICD10cm", "SNOMEDCT"]]
    behaviour = {(v, vid): _bulk(v.replace(" ", ""), vid) for v, vid in pairs}

    with _patched_search(behaviour):
        df = omr.search_omophub("Foo",
                                vocabularies={"ICD10cm": "ICD-10-CM", "SNOMEDCT": "SNOMED CT"},
                                page_size=20)
    assert len(df) == 120


# --- Runner -----------------------------------------------------------------

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

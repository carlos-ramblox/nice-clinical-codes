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
    """Minimal stand-in for omophub.OMOPHub used by search_omophub().

    Supports both ``search.bulk_basic`` (T37f primary path) and
    ``search.basic`` (fallback). The synthesised ``BulkSearchResponse``
    re-uses the same per-(variant, vocab) behaviour table so a test
    written for either path produces the same row set.
    """

    def __init__(self, behaviour, *, raise_on_bulk: bool = False):
        # behaviour: dict[(variant, vocab_id) -> list[dict]] of fake rows
        self._behaviour = behaviour
        self._raise_on_bulk = raise_on_bulk
        self.basic_calls: list[tuple[str, str]] = []
        self.bulk_calls: int = 0
        self.search = self  # search.{basic,bulk_basic} resolve on the same object

    def basic(self, search_term, **kwargs):
        vid = kwargs["vocabulary_ids"][0]
        self.basic_calls.append((search_term, vid))
        return list(self._behaviour.get((search_term, vid), []))

    def bulk_basic(self, searches, *, defaults=None):
        if self._raise_on_bulk:
            raise RuntimeError("bulk path simulated failure")
        self.bulk_calls += 1
        items = []
        for s in searches:
            vid = s["vocabulary_ids"][0]
            q = s["query"]
            items.append({
                "search_id": s["search_id"],
                "query": q,
                "results": list(self._behaviour.get((q, vid), [])),
                "status": "completed",
            })
        return {
            "results": items,
            "total_searches": len(items),
            "completed_searches": len(items),
            "failed_searches": 0,
        }


def _row(code, vocab_id, name="x"):
    return {"concept_code": code, "concept_name": name, "domain_id": "Condition", "_vocab": vocab_id}


def _patched_search(behaviour, *, raise_on_bulk: bool = False):
    """Return a context manager that patches OMOPHub() to a _FakeClient."""
    return patch.object(
        omr, "OMOPHub",
        lambda **_: _FakeClient(behaviour, raise_on_bulk=raise_on_bulk),
    )


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


# --- T37f bulk-search equivalence + fallback --------------------------------

def test_bulk_path_makes_single_call_not_n_times_m():
    behaviour = {
        ("Foo", "ICD10cm"):  [_row("I21", "ICD10cm")],
        ("Foo", "SNOMEDCT"): [_row("S1", "SNOMEDCT")],
        ("Acute Foo", "ICD10cm"):  [_row("I22", "ICD10cm")],
        ("Acute Foo", "SNOMEDCT"): [_row("S2", "SNOMEDCT")],
        ("Chronic Foo", "ICD10cm"):  [_row("I23", "ICD10cm")],
        ("Chronic Foo", "SNOMEDCT"): [_row("S3", "SNOMEDCT")],
    }
    captured: dict = {}
    def make(**_):
        captured["client"] = _FakeClient(behaviour)
        return captured["client"]
    with patch.object(omr, "OMOPHub", make):
        df = omr.search_omophub(
            "Foo",
            vocabularies={"ICD10cm": "ICD-10-CM", "SNOMEDCT": "SNOMED CT"},
            page_size=20,
        )
    assert captured["client"].bulk_calls == 1, "T37f should issue exactly one bulk call"
    assert captured["client"].basic_calls == [], "basic fallback must not fire on success"
    # Sanity: every (variant, vocab) combo's row appears in the merged set.
    assert len(df) == 6, df["concept_code"].tolist()


def test_bulk_equivalent_to_basic_for_same_inputs():
    behaviour = {
        ("Foo", "ICD10cm"):         [_row("I21", "ICD10cm"), _row("I22", "ICD10cm")],
        ("Acute Foo", "ICD10cm"):   [_row("I21", "ICD10cm"), _row("I23", "ICD10cm")],
        ("Chronic Foo", "ICD10cm"): [_row("I21", "ICD10cm")],
    }
    # Bulk path (default)
    with _patched_search(behaviour):
        bulk_codes = sorted(omr.search_omophub("Foo",
            vocabularies={"ICD10cm": "ICD-10-CM"}, page_size=20)["concept_code"].tolist())
    # Forced fallback to basic by raising on bulk
    with _patched_search(behaviour, raise_on_bulk=True):
        basic_codes = sorted(omr.search_omophub("Foo",
            vocabularies={"ICD10cm": "ICD-10-CM"}, page_size=20)["concept_code"].tolist())
    assert bulk_codes == basic_codes == ["I21", "I22", "I23"]


def test_bulk_failure_falls_back_to_basic():
    behaviour = {
        ("Foo", "ICD10cm"):         [_row("I21", "ICD10cm")],
        ("Acute Foo", "ICD10cm"):   [_row("I22", "ICD10cm")],
        ("Chronic Foo", "ICD10cm"): [_row("I23", "ICD10cm")],
    }
    captured: dict = {}
    def make(**_):
        captured["client"] = _FakeClient(behaviour, raise_on_bulk=True)
        return captured["client"]
    with patch.object(omr, "OMOPHub", make):
        df = omr.search_omophub("Foo", vocabularies={"ICD10cm": "ICD-10-CM"}, page_size=20)
    assert captured["client"].bulk_calls == 0, "bulk was raised; counter stays 0"
    assert len(captured["client"].basic_calls) == 3, "fallback issues one basic per variant"
    assert sorted(df["concept_code"].tolist()) == ["I21", "I22", "I23"]


def test_empty_vocabularies_short_circuits_without_calling_omophub():
    """T37f audit FIX: vocabularies={} must NOT fall back to all OMOP vocabs.
    A drug-domain query whose coding_systems map down to {} should make zero
    OMOPHub calls, not silently burn quota on SNOMED/ICD10/OPCS4."""
    captured: dict = {}
    def make(**_):
        captured["client"] = _FakeClient({})
        return captured["client"]
    with patch.object(omr, "OMOPHub", make):
        df = omr.search_omophub("Foo", vocabularies={}, page_size=20)
    assert df.empty
    assert captured.get("client") is None or captured["client"].bulk_calls == 0
    assert captured.get("client") is None or captured["client"].basic_calls == []


def test_none_concept_code_value_is_skipped_not_stringified():
    """T37f audit FIX: a row with ``concept_code=None`` must be dropped, not
    coerced to the literal string ``"None"``. Falls back to concept_id."""
    behaviour = {
        ("Foo", "ICD10cm"): [
            {"concept_code": None, "concept_id": 999, "concept_name": "fallback"},
            {"concept_code": None, "concept_id": None, "concept_name": "drop"},
            {"concept_code": "I21", "concept_name": "kept"},
        ],
        ("Acute Foo", "ICD10cm"): [],
        ("Chronic Foo", "ICD10cm"): [],
    }
    with _patched_search(behaviour):
        df = omr.search_omophub("Foo", vocabularies={"ICD10cm": "ICD-10-CM"}, page_size=20)
    codes = sorted(str(c) for c in df["concept_code"].tolist() if c is not None)
    # The None-value row should fall back to concept_id (999) for dedup key,
    # but its concept_code column in the DataFrame is None. Verify "None" string never appears.
    assert "None" not in codes
    # Two surviving rows: the fallback-to-concept_id one + the I21 one.
    assert len(df) == 2


def test_bulk_skips_per_item_failures_without_crashing_whole_query():
    """One search_id fails (status != completed); other items still flow through."""
    behaviour = {
        ("Foo", "ICD10cm"):         [_row("I21", "ICD10cm")],
        ("Acute Foo", "ICD10cm"):   [_row("I22", "ICD10cm")],
        ("Chronic Foo", "ICD10cm"): [_row("I23", "ICD10cm")],
    }

    class _PartialFailClient(_FakeClient):
        def bulk_basic(self, searches, *, defaults=None):
            self.bulk_calls += 1
            items = []
            for s in searches:
                vid = s["vocabulary_ids"][0]
                if s["query"] == "Acute Foo":
                    items.append({"search_id": s["search_id"], "query": s["query"],
                                  "results": [], "status": "failed", "error": "fake"})
                else:
                    items.append({"search_id": s["search_id"], "query": s["query"],
                                  "results": list(self._behaviour.get((s["query"], vid), [])),
                                  "status": "completed"})
            return {"results": items, "total_searches": len(items),
                    "completed_searches": len(items) - 1, "failed_searches": 1}

    with patch.object(omr, "OMOPHub", lambda **_: _PartialFailClient(behaviour)):
        df = omr.search_omophub("Foo", vocabularies={"ICD10cm": "ICD-10-CM"}, page_size=20)
    assert sorted(df["concept_code"].tolist()) == ["I21", "I23"], "Acute Foo dropped silently"


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

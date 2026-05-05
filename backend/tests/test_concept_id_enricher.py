"""Pin the concept_id_enricher contract.

  - Codes already carrying concept_id are left alone (no OMOPHub call).
  - Codes missing concept_id get filled when OMOPHub returns one.
  - OMOPHub failures (NotFound, network) leave the code unmapped, never raise.
  - The (vocab, code) cache is consulted before each lookup.

    pytest tests/test_concept_id_enricher.py -v
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("OMOPHUB_API_KEY", "dummy-test-key")

from app.graph.nodes import concept_id_enricher as enricher  # noqa: E402


class _FakeConcepts:
    def __init__(self, mapping, fail_for=None):
        self._mapping = mapping
        self._fail_for = fail_for or set()
        self.calls: list[tuple[str, str]] = []

    def get_by_code(self, vocab_id, code, **kwargs):
        self.calls.append((vocab_id, code))
        if (vocab_id, code) in self._fail_for:
            raise RuntimeError("simulated OMOPHub failure")
        if (vocab_id, code) in self._mapping:
            return {"concept_id": self._mapping[(vocab_id, code)]}
        raise RuntimeError("not found")


class _FakeClient:
    def __init__(self, concepts):
        self.concepts = concepts


def _patched(concepts):
    return patch.object(enricher, "OMOPHub", lambda **_: _FakeClient(concepts))


def _reset_state():
    with enricher._CACHE_LOCK:
        enricher._CACHE.clear()
    with enricher._client_lock:
        enricher._client = None


def test_already_mapped_codes_are_skipped():
    _reset_state()
    fake = _FakeConcepts({})
    state = {"enriched_codes": [
        {"code": "44054006", "vocabulary": "SNOMED CT", "concept_id": 201826},
        {"code": "E11", "vocabulary": "ICD-10 (WHO)", "concept_id": 45571656},
    ]}
    with _patched(fake):
        out = enricher.enrich_concept_ids(state)
    # Empty diff: every code was pre-mapped; OMOPHub never called.
    assert out == {}
    assert fake.calls == []


def test_unmapped_codes_get_filled_from_omophub():
    _reset_state()
    fake = _FakeConcepts({
        ("SNOMED", "44054006"): 201826,
        ("ICD10", "E11"): 45571656,
    })
    codes = [
        {"code": "44054006", "vocabulary": "SNOMED CT", "concept_id": None},
        {"code": "E11", "vocabulary": "ICD-10 (WHO)", "concept_id": None},
    ]
    with _patched(fake):
        out = enricher.enrich_concept_ids({"enriched_codes": codes})
    assert out["enriched_codes"][0]["concept_id"] == 201826
    assert out["enriched_codes"][1]["concept_id"] == 45571656
    assert sorted(fake.calls) == [("ICD10", "E11"), ("SNOMED", "44054006")]


def test_omophub_failures_leave_code_unmapped_without_raising():
    _reset_state()
    fake = _FakeConcepts({}, fail_for={("SNOMED", "44054006")})
    codes = [{"code": "44054006", "vocabulary": "SNOMED CT", "concept_id": None}]
    with _patched(fake):
        enricher.enrich_concept_ids({"enriched_codes": codes})
    assert codes[0]["concept_id"] is None


def test_cache_avoids_a_second_lookup_for_the_same_pair():
    _reset_state()
    fake = _FakeConcepts({("SNOMED", "44054006"): 201826})

    state_a = {"enriched_codes": [{"code": "44054006", "vocabulary": "SNOMED CT", "concept_id": None}]}
    state_b = {"enriched_codes": [{"code": "44054006", "vocabulary": "SNOMED CT", "concept_id": None}]}

    with _patched(fake):
        enricher.enrich_concept_ids(state_a)
        enricher.enrich_concept_ids(state_b)

    assert state_a["enriched_codes"][0]["concept_id"] == 201826
    assert state_b["enriched_codes"][0]["concept_id"] == 201826
    assert fake.calls == [("SNOMED", "44054006")]  # second call served from cache


def test_unknown_vocabulary_is_skipped():
    """UMLS-tagged codes (CUIs) have no OMOP vocabulary_id mapping."""
    _reset_state()
    fake = _FakeConcepts({})
    codes = [{"code": "C0011860", "vocabulary": "UMLS", "concept_id": None}]
    with _patched(fake):
        enricher.enrich_concept_ids({"enriched_codes": codes})
    assert codes[0]["concept_id"] is None
    assert fake.calls == []


def test_cache_entry_expires_after_ttl():
    """A vocabulary release that fills in a previously-unmapped code should
    land within the TTL without a container restart."""
    _reset_state()
    fake = _FakeConcepts({("SNOMED", "44054006"): 201826})

    with _patched(fake):
        # Seed a stale "miss" entry for the same key, timestamped past TTL.
        with enricher._CACHE_LOCK:
            enricher._CACHE[("SNOMED", "44054006")] = (
                time.time() - enricher._CACHE_TTL_SECONDS - 1,
                None,
            )
        codes = [{"code": "44054006", "vocabulary": "SNOMED CT", "concept_id": None}]
        enricher.enrich_concept_ids({"enriched_codes": codes})

    assert codes[0]["concept_id"] == 201826
    assert fake.calls == [("SNOMED", "44054006")]  # stale entry triggered a fresh lookup


def test_missing_api_key_is_a_no_op():
    _reset_state()
    fake = _FakeConcepts({("SNOMED", "44054006"): 201826})
    codes = [{"code": "44054006", "vocabulary": "SNOMED CT", "concept_id": None}]
    with patch.object(enricher, "OMOPHUB_API_KEY", ""), _patched(fake):
        out = enricher.enrich_concept_ids({"enriched_codes": codes})
    assert out == {}
    assert codes[0]["concept_id"] is None
    assert fake.calls == []


def _run_all():
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
            except Exception as e:
                failed += 1
                print(f"ERROR {name}: {type(e).__name__}: {e}")
    return failed


if __name__ == "__main__":
    sys.exit(_run_all())

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
    """Stub for OMOPHub `concepts.*`.

    ``mapping`` accepts either an int (treated as a standard concept_id)
    or a dict to override extra fields like ``standard_concept`` and
    ``relationships`` for T37h coverage. Real OMOPHub always returns
    ``standard_concept`` so the dict form is closer to production shape.
    """

    def __init__(self, mapping, fail_for=None):
        self._mapping = mapping
        self._fail_for = fail_for or set()
        self.calls: list[tuple[str, str]] = []

    def get_by_code(self, vocab_id, code, **kwargs):
        self.calls.append((vocab_id, code))
        if (vocab_id, code) in self._fail_for:
            raise RuntimeError("simulated OMOPHub failure")
        if (vocab_id, code) in self._mapping:
            value = self._mapping[(vocab_id, code)]
            if isinstance(value, dict):
                # Default to standard unless the test overrides — matches what
                # the safety net (T37h) checks before accepting the id verbatim.
                return {"standard_concept": "S", **value}
            return {"concept_id": value, "standard_concept": "S"}
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


# --- T37h hallucination safety net ------------------------------------------

def test_standard_concept_kept_verbatim():
    _reset_state()
    fake = _FakeConcepts({("SNOMED", "44054006"): {"concept_id": 201826, "standard_concept": "S"}})
    codes = [{"code": "44054006", "vocabulary": "SNOMED CT", "concept_id": None}]
    with _patched(fake):
        enricher.enrich_concept_ids({"enriched_codes": codes})
    assert codes[0]["concept_id"] == 201826


def test_non_standard_with_maps_to_uses_target_concept_id():
    """ICD-10-CM concept marked non-standard ('C' classification); should follow
    the 'Maps to' relationship to its standard SNOMED equivalent."""
    _reset_state()
    fake = _FakeConcepts({
        ("ICD10", "E11.9"): {
            "concept_id": 35200012,  # non-standard ICD-10-CM concept_id
            "standard_concept": "C",
            "relationships": [
                {"relationship_id": "Maps to", "target_concept_id": 201826},
            ],
        },
    })
    codes = [{"code": "E11.9", "vocabulary": "ICD-10 (WHO)", "concept_id": None}]
    with _patched(fake):
        enricher.enrich_concept_ids({"enriched_codes": codes})
    assert codes[0]["concept_id"] == 201826, "should have used Maps-to target, not raw id"


def test_non_standard_without_maps_to_returns_none():
    """A non-standard concept with no Maps-to relationship lands in the OHDSI
    export's unmapped array rather than ship a non-standard concept_id ATLAS
    will reject."""
    _reset_state()
    fake = _FakeConcepts({
        ("ICD10", "Z99.0"): {
            "concept_id": 35200013,
            "standard_concept": "C",
            "relationships": [],
        },
    })
    codes = [{"code": "Z99.0", "vocabulary": "ICD-10 (WHO)", "concept_id": None}]
    with _patched(fake):
        enricher.enrich_concept_ids({"enriched_codes": codes})
    assert codes[0]["concept_id"] is None


def test_non_standard_concept_id_is_none_with_no_relationships_field():
    """Real OMOPHub responses may omit `relationships` entirely when no
    include_relationships flag is honoured for that lookup. Safety net
    should still degrade gracefully — treat absent as empty."""
    _reset_state()
    fake = _FakeConcepts({
        ("ICD10", "W19"): {"concept_id": 35201000, "standard_concept": "C"},
    })
    codes = [{"code": "W19", "vocabulary": "ICD-10 (WHO)", "concept_id": None}]
    with _patched(fake):
        enricher.enrich_concept_ids({"enriched_codes": codes})
    assert codes[0]["concept_id"] is None


# --- direct unit test for the helper ----------------------------------------

def test_safe_concept_id_helper_branches():
    s = enricher._safe_concept_id
    assert s({"concept_id": 1, "standard_concept": "S"}) == 1
    assert s({"concept_id": 2, "standard_concept": "C",
              "relationships": [{"relationship_id": "Maps to", "target_concept_id": 42}]}) == 42
    assert s({"concept_id": 3, "standard_concept": "C"}) is None
    assert s({"concept_id": 4, "standard_concept": "C",
              "relationships": [{"relationship_id": "Is a", "target_concept_id": 7}]}) is None
    assert s({}) is None
    assert s({"concept_id": None, "standard_concept": "S"}) is None
    # T37h audit FIX: non-dict should return None, not raise
    assert s(None) is None  # type: ignore[arg-type]
    assert s("not a dict") is None  # type: ignore[arg-type]


def test_safe_concept_id_handles_target_zero_without_falling_through():
    """T37h audit FIX: target_concept_id=0 (OMOP CDM 'no matching concept'
    sentinel) must not fall through to a different key via `or`. The
    explicit None check picks 0 verbatim."""
    s = enricher._safe_concept_id
    rel = {"relationship_id": "Maps to", "target_concept_id": 0, "concept_id": 999}
    assert s({"concept_id": 5, "standard_concept": "C", "relationships": [rel]}) == 0


def test_safe_concept_id_rejects_empty_relationship_id():
    """T37h audit FIX: relationship_id='' (empty string) must not match
    'Maps to'. The explicit truthy check guards against SDK shape edges."""
    s = enricher._safe_concept_id
    rel = {"relationship_id": "", "relationship_type": "Maps to", "target_concept_id": 42}
    # relationship_id is "", so we fall back to relationship_type which IS "Maps to"
    assert s({"concept_id": 6, "standard_concept": "C", "relationships": [rel]}) == 42
    # But if BOTH are empty, no match
    rel_blank = {"relationship_id": "", "relationship_type": "", "target_concept_id": 99}
    assert s({"concept_id": 7, "standard_concept": "C", "relationships": [rel_blank]}) is None


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

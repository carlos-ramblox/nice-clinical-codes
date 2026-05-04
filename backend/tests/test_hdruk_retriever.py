"""
Tests for the HDR UK Phenotype Library retriever
(backend/app/graph/nodes/hdruk_retriever.py).

Three tiers of test:
  - _normalise_vocabulary(): pure-string logic, runs offline.
  - hdruk_rows_to_retrieved_codes(): mapping pure dict → RetrievedCode shape.
  - retrieve_from_hdruk(): end-to-end node behaviour against a stubbed
    requests.Session, so we exercise the search → codelist → flatten path
    without making real network calls.

Run with pytest from backend/, or as a script:
    python -m tests.test_hdruk_retriever
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# Allow `import app.*` whether the test is invoked from backend/ or repo root.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.graph.nodes import hdruk_retriever as hdr  # noqa: E402


# --- _normalise_vocabulary() tier ------------------------------------------

def test_double_space_snomed_label_canonicalised():
    # The live API actually returns "SNOMED  CT codes" with two spaces;
    # we collapse whitespace before lookup so it maps to the canonical
    # "SNOMED CT" label our state.RetrievedCode shape expects.
    assert hdr._normalise_vocabulary("SNOMED  CT codes") == "SNOMED CT"


def test_icd10_canonicalised_to_who_label():
    assert hdr._normalise_vocabulary("ICD10 codes") == "ICD-10 (WHO)"
    assert hdr._normalise_vocabulary("ICD-10 codes") == "ICD-10 (WHO)"


def test_opcs4_canonicalised():
    assert hdr._normalise_vocabulary("OPCS4 codes") == "OPCS-4"
    assert hdr._normalise_vocabulary("OPCS-4 codes") == "OPCS-4"


def test_unknown_vocabulary_passes_through_with_codes_suffix_stripped():
    # Anything not in the known map keeps its original label minus a
    # trailing " codes" suffix — so e.g. "Read codes v2" stays as-is
    # (no trailing " codes" to strip) but "ICD11 codes" becomes "ICD11".
    assert hdr._normalise_vocabulary("Read codes v2") == "Read codes v2"
    assert hdr._normalise_vocabulary("ICD11 codes") == "ICD11"


def test_empty_vocabulary_returns_empty():
    assert hdr._normalise_vocabulary("") == ""
    assert hdr._normalise_vocabulary(None) == ""


# --- hdruk_rows_to_retrieved_codes() tier ----------------------------------

def _row(code, vocab_name, description="x"):
    """Stand-in for one row from /api/v1/phenotypes/{id}/export/codes/."""
    return {
        "code": code,
        "description": description,
        "coding_system": {"id": 5, "name": vocab_name, "description": vocab_name},
    }


def test_mapping_preserves_code_term_and_canonical_vocabulary():
    rows = [_row("13Y4.00", "Read codes v2", description="Asthma society member")]
    out = hdr.hdruk_rows_to_retrieved_codes(rows, phenotype_rank=1)
    assert len(out) == 1
    rc = out[0]
    assert rc["code"] == "13Y4.00"
    assert rc["term"] == "Asthma society member"
    assert rc["vocabulary"] == "Read codes v2"
    assert rc["source"] == hdr.SOURCE_TAG
    assert rc["domain"] == "Condition"
    assert rc["similarity_score"] is None
    assert rc["usage_frequency"] is None


def test_phenotype_rank_propagates_to_all_codes_from_same_phenotype():
    rows = [_row(f"X{i}", "ICD10 codes") for i in range(3)]
    out = hdr.hdruk_rows_to_retrieved_codes(rows, phenotype_rank=2)
    assert all(rc["rank"] == 2 for rc in out)
    assert all(rc["vocabulary"] == "ICD-10 (WHO)" for rc in out)


def test_rows_without_code_are_skipped():
    # Defensive: an upstream row with a missing code shouldn't yield a
    # zero-code RetrievedCode the merger would have to filter later.
    rows = [{"code": "", "description": "blank", "coding_system": {"name": "ICD10 codes"}},
            _row("E10", "ICD10 codes", description="diabetes type 1")]
    out = hdr.hdruk_rows_to_retrieved_codes(rows, phenotype_rank=1)
    assert [rc["code"] for rc in out] == ["E10"]


# --- retrieve_from_hdruk() tier with stubbed Session -----------------------

class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status {self.status_code}")


class _FakeSession:
    """Records URLs hit and returns canned payloads. Mirrors the
    minimal surface of requests.Session that the retriever uses.

    Stub keys are substring markers checked against the request URL.
    The search URL ends with ``/api/v1/phenotypes/`` (params are
    separate); codelist URLs contain ``/{phenotype_id}/export/codes/``.
    """

    def __init__(self, url_to_payload, default_status=200):
        # Sort longest-marker-first so a codelist marker like
        # "/PH12/export/" is preferred over the bare search marker
        # "/api/v1/phenotypes/" which is a substring of every URL.
        self._stubs = sorted(url_to_payload.items(), key=lambda kv: -len(kv[0]))
        self.default_status = default_status
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url, params=None, timeout=None):
        suffix = ""
        if params:
            suffix = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        self.calls.append(url + suffix)
        for marker, payload in self._stubs:
            if marker in url:
                if isinstance(payload, tuple):
                    status, body = payload
                    return _FakeResponse(status_code=status, payload=body)
                return _FakeResponse(status_code=self.default_status, payload=payload)
        return _FakeResponse(status_code=404, payload={"detail": "no stub"})

    def update(self, *_, **__):  # for headers.update
        pass


def _patch_session(fake):
    """Replace ``requests.Session`` inside the retriever module so the
    node uses our fake instead of opening real sockets."""
    return patch.object(hdr.requests, "Session", lambda: fake)


def test_full_node_search_then_codelist_flatten():
    search_payload = {
        "page": 1, "total_pages": 1, "page_size": 20,
        "data": [
            {"phenotype_id": "PH12", "name": "Asthma"},
            {"phenotype_id": "PH21", "name": "Asthma (NEWS2)"},
        ],
    }
    ph12_codes = [
        {"code": "13Y4.00", "description": "Asthma society member",
         "coding_system": {"name": "Read codes v2"}},
        {"code": "J45",      "description": "Asthma",
         "coding_system": {"name": "ICD10 codes"}},
    ]
    ph21_codes = [
        {"code": "195967001", "description": "Asthma",
         "coding_system": {"name": "SNOMED  CT codes"}},  # double space, real
    ]
    fake = _FakeSession({
        "/api/v1/phenotypes/":               search_payload,
        "/api/v1/phenotypes/PH12/export/":   ph12_codes,
        "/api/v1/phenotypes/PH21/export/":   ph21_codes,
    })
    # patch out the inter-request sleep so the test runs instantly
    with _patch_session(fake), patch.object(hdr.time, "sleep", lambda *_: None):
        # cap at 2 so both phenotypes are fetched
        with patch.object(hdr, "HDR_UK_TOP_K_PHENOTYPES", 2):
            out = hdr.retrieve_from_hdruk({
                "parsed_conditions": [{"name": "asthma"}],
            })

    codes = out["retrieved_codes"]
    # 2 from PH12 + 1 from PH21
    assert len(codes) == 3
    by_code = {c["code"]: c for c in codes}
    assert by_code["13Y4.00"]["vocabulary"] == "Read codes v2"
    assert by_code["13Y4.00"]["rank"] == 1
    assert by_code["J45"]["vocabulary"] == "ICD-10 (WHO)"
    assert by_code["J45"]["rank"] == 1
    # PH21 was rank 2 and its SNOMED label gets canonicalised across the
    # double-space artefact.
    assert by_code["195967001"]["vocabulary"] == "SNOMED CT"
    assert by_code["195967001"]["rank"] == 2

    assert out["sources_queried"] == [hdr.SOURCE_TAG]


def test_no_conditions_returns_empty_without_calling_api():
    fake = _FakeSession({})
    with _patch_session(fake):
        out = hdr.retrieve_from_hdruk({"parsed_conditions": []})
    assert out == {"retrieved_codes": [], "sources_queried": []}
    assert fake.calls == []


def test_search_failure_swallowed_per_condition():
    # 500 on search should not raise out of the node — the merger
    # tolerates an empty contribution from any one retriever.
    fake = _FakeSession({"/api/v1/phenotypes/": (500, {"detail": "boom"})})
    with _patch_session(fake), patch.object(hdr.time, "sleep", lambda *_: None):
        out = hdr.retrieve_from_hdruk({"parsed_conditions": [{"name": "asthma"}]})
    assert out["retrieved_codes"] == []
    # sources_queried still tagged so eval logging shows we tried HDR UK
    assert out["sources_queried"] == [hdr.SOURCE_TAG]


def test_codelist_failure_for_one_phenotype_does_not_break_others():
    search_payload = {
        "data": [
            {"phenotype_id": "PHGOOD", "name": "ok"},
            {"phenotype_id": "PHBAD",  "name": "broken"},
        ],
    }
    good_codes = [{"code": "X1", "description": "good",
                   "coding_system": {"name": "ICD10 codes"}}]
    fake = _FakeSession({
        "/api/v1/phenotypes/":                     search_payload,
        "/api/v1/phenotypes/PHGOOD/export/":       good_codes,
        "/api/v1/phenotypes/PHBAD/export/":        (500, {}),
    })
    with _patch_session(fake), patch.object(hdr.time, "sleep", lambda *_: None):
        with patch.object(hdr, "HDR_UK_TOP_K_PHENOTYPES", 2):
            out = hdr.retrieve_from_hdruk({"parsed_conditions": [{"name": "x"}]})
    codes = out["retrieved_codes"]
    assert [c["code"] for c in codes] == ["X1"]


# --- Runner ----------------------------------------------------------------

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

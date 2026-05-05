"""
Tests for the GET /api/phenotypes/discover endpoint (T34).

All tests run against the FastAPI TestClient with HDR UK and the LLM
judge mocked at the same boundaries that
``test_phenotype_discovery.py`` patches: the ``requests.Session`` factory
inside ``app.services.phenotype_discovery`` and the ``ChatAnthropic``
factory inside the same module. No live network calls.

Run with pytest from backend/, or as a script:

    python -m tests.test_phenotype_discovery_endpoint
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# Allow `import app.*` whether the test is invoked from backend/ or repo root.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import phenotype_discovery as pd  # noqa: E402


client = TestClient(app)


# --- Fakes (same shape as test_phenotype_discovery.py) ---------------------

def _phenotype_payload(pid, name, **extra):
    """Phenotype dict shaped like an HDR UK search response row."""
    return {
        "phenotype_id": pid,
        "name": name,
        "type": [{"name": "Disease or syndrome"}],
        "coding_system": [{"name": "SNOMED CT"}, {"name": "ICD10 codes"}],
        "data_sources": [{"name": "CPRD GOLD"}],
        "publications": [{"details": f"Published codelist for {name}"}],
        **extra,
    }


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
    """Records search calls and returns a canned phenotype list."""

    def __init__(self, search_data):
        self._search_data = search_data
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url + ("?" + "&".join(f"{k}={v}" for k, v in (params or {}).items()) if params else ""))
        return _FakeResponse(payload={
            "page": 1, "total_pages": 1, "page_size": 20,
            "data": list(self._search_data),
        })

    def update(self, *_, **__):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeStructuredLLM:
    def __init__(self, decisions_by_id, raise_on_invoke=None):
        self._decisions_by_id = decisions_by_id
        self._raise = raise_on_invoke

    def invoke(self, _messages):
        if self._raise is not None:
            raise self._raise
        return pd._PhenotypeRelevanceBatch(decisions=[
            pd._PhenotypeRelevance(phenotype_id=pid, relevant=v["relevant"], reason=v["reason"])
            for pid, v in self._decisions_by_id.items()
        ])


class _FakeChatAnthropic:
    def __init__(self, structured_llm):
        self._structured_llm = structured_llm

    def with_structured_output(self, _schema):
        return self._structured_llm


def _patch_pipeline(search_data, decisions, raise_judge=None):
    """Patch the search session + LLM judge so the endpoint runs offline.

    Both patches target ``app.services.phenotype_discovery`` because the
    endpoint imports its callables from there; patching at the call-site
    module ensures we hit the actual references the endpoint uses.
    """
    fake_session = _FakeSession(search_data)
    fake_llm = _FakeStructuredLLM(decisions, raise_on_invoke=raise_judge)
    return (
        patch.object(pd.requests, "Session", lambda: fake_session),
        patch.object(pd, "ChatAnthropic", lambda *_a, **_kw: _FakeChatAnthropic(fake_llm)),
        patch.object(pd, "HDR_UK_USE_JUDGE", True),
        patch.object(pd, "ANTHROPIC_API_KEY", "dummy"),
    )


def _clear_cache():
    client.delete("/api/phenotypes/discover/cache")


# --- Endpoint behaviour ----------------------------------------------------

def test_discover_returns_phenotypes_with_rationale_and_link():
    _clear_cache()
    search = [
        _phenotype_payload("PH12", "Asthma"),
        _phenotype_payload("PH99", "Pulmonary embolism"),
    ]
    # phenotype_version_id is optional in the fixtures; this test
    # exercises the unversioned-URL path. The versioned-URL contract
    # is pinned by the dedicated test below.
    decisions = {
        "PH12": {"relevant": True,  "reason": "primary scope is asthma in adults"},
        "PH99": {"relevant": False, "reason": "different condition"},
    }
    p1, p2, p3, p4 = _patch_pipeline(search, decisions)
    with p1, p2, p3, p4:
        r = client.get("/api/phenotypes/discover", params={"query": "asthma", "top_k": 5})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    row = body[0]
    assert row["phenotype_id"] == "PH12"
    assert row["name"] == "Asthma"
    assert row["hdruk_url"].endswith("/phenotypes/PH12")
    assert row["relevance_verdict"] == "relevant"
    assert "asthma" in row["relevance_rationale"].lower()
    # Coding-system normalisation is now the discovery sidebar's job at
    # the UI layer; the API surfaces the raw HDR UK labels so the row
    # matches what a clinician sees on the HDR UK detail page they
    # click through to.
    assert "SNOMED CT" in row["coding_systems"]
    assert "ICD10 codes" in row["coding_systems"]


def test_discover_pins_version_in_url_when_available():
    # When the HDR UK search response carries phenotype_version_id, the
    # endpoint pins the link to /phenotypes/{id}/version/{v}/detail/ so
    # an adopted citation stays pointed at the version the user
    # actually consulted, even if HDR UK publishes a newer one later.
    _clear_cache()
    pheno = _phenotype_payload("PH12", "Asthma", phenotype_version_id=24)
    decisions = {"PH12": {"relevant": True, "reason": "matches"}}
    p1, p2, p3, p4 = _patch_pipeline([pheno], decisions)
    with p1, p2, p3, p4:
        r = client.get("/api/phenotypes/discover", params={"query": "asthma"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["phenotype_version_id"] == 24
    assert body[0]["hdruk_url"].endswith("/phenotypes/PH12/version/24/detail/")


def test_discover_query_too_short_returns_422():
    # FastAPI's Query(min_length=3) returns 422, not 400 — pin that
    # contract so the frontend can render a sensible inline hint.
    r = client.get("/api/phenotypes/discover", params={"query": "ab"})
    assert r.status_code == 422


def test_discover_top_k_out_of_bounds_returns_422():
    r = client.get("/api/phenotypes/discover", params={"query": "asthma", "top_k": 99})
    assert r.status_code == 422
    r2 = client.get("/api/phenotypes/discover", params={"query": "asthma", "top_k": 0})
    assert r2.status_code == 422


def test_discover_judge_fallthrough_admits_phenotypes_as_uncertain():
    _clear_cache()
    search = [_phenotype_payload("PH12", "Asthma")]
    p1, p2, p3, p4 = _patch_pipeline(search, {}, raise_judge=RuntimeError("haiku 500"))
    with p1, p2, p3, p4:
        r = client.get("/api/phenotypes/discover", params={"query": "asthma"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    # Judge failed -> phenotype admitted with verdict=uncertain so the UI
    # can hedge the rationale string instead of mis-attributing one.
    assert body[0]["relevance_verdict"] == "uncertain"
    assert "without explicit scope-fit verdict" in body[0]["relevance_rationale"]


def test_discover_empty_search_returns_empty_list():
    _clear_cache()
    p1, p2, p3, p4 = _patch_pipeline([], {})
    with p1, p2, p3, p4:
        r = client.get("/api/phenotypes/discover", params={"query": "totally novel disease"})
    assert r.status_code == 200
    assert r.json() == []


def test_discover_caches_repeated_query_within_ttl():
    # Second call to the same (query, top_k) hits the in-process cache
    # and does NOT issue another HDR UK or Haiku call. We patch the
    # search session so the second call would otherwise return a
    # different list; if the cache is honoured we still see the first
    # call's result.
    _clear_cache()
    first = [_phenotype_payload("PH12", "Asthma")]
    second = [_phenotype_payload("PHX", "Different result")]
    decisions = {"PH12": {"relevant": True, "reason": "matches"},
                 "PHX":  {"relevant": True, "reason": "should not be seen"}}

    p1, p2, p3, p4 = _patch_pipeline(first, decisions)
    with p1, p2, p3, p4:
        r1 = client.get("/api/phenotypes/discover", params={"query": "asthma"})
    assert r1.status_code == 200
    assert [row["phenotype_id"] for row in r1.json()] == ["PH12"]

    p1b, p2b, p3b, p4b = _patch_pipeline(second, decisions)
    with p1b, p2b, p3b, p4b:
        r2 = client.get("/api/phenotypes/discover", params={"query": "asthma"})
    assert r2.status_code == 200
    # If the cache is being honoured, r2 returns the cached PH12, NOT PHX
    # from the second patched session.
    assert [row["phenotype_id"] for row in r2.json()] == ["PH12"]


def test_discover_cache_is_query_lowercased_and_top_k_keyed():
    # Cache key normalises whitespace + case on the query; top_k is part
    # of the key so different fan-out sizes don't collide.
    _clear_cache()
    search = [_phenotype_payload("PH12", "Asthma")]
    decisions = {"PH12": {"relevant": True, "reason": "matches"}}
    p1, p2, p3, p4 = _patch_pipeline(search, decisions)
    with p1, p2, p3, p4:
        r1 = client.get("/api/phenotypes/discover", params={"query": "  Asthma  "})
        r2 = client.get("/api/phenotypes/discover", params={"query": "asthma"})
        # Different top_k -> different cache key -> the patched session
        # is hit again, but since we re-use the same fake the result is
        # the same. The point is that this call does NOT explode on the
        # cache lookup.
        r3 = client.get("/api/phenotypes/discover", params={"query": "asthma", "top_k": 3})
    for r in (r1, r2, r3):
        assert r.status_code == 200
        assert [row["phenotype_id"] for row in r.json()] == ["PH12"]


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

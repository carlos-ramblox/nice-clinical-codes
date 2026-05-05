"""
Tests for the HDR UK cross-reference panel (T35).

Two layers covered:

1. Service-layer unit tests for ``compute_overlap``: division-by-zero
   guard, all-zero on empty input, the asymmetric-percentage contract.
2. Endpoint tests for ``GET /api/codelists/{id}/cross-reference``:
   positive path, empty codelist, missing codelist (404), file-cache
   round-trip across two requests.

Mocking targets the same boundaries as the existing T34 tests
(``app.services.phenotype_discovery.requests.Session`` +
``ChatAnthropic`` factory) so the cache layers fire identically and no
live network call is made. The per-phenotype file cache is redirected
to a temp directory via ``patch.object(pd, "_PHENOTYPE_CACHE_DIR")``
so test runs don't pollute the real cache directory.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Allow `import app.*` whether the test is invoked from backend/ or repo root.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import phenotype_discovery as pd  # noqa: E402


client = TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup_test_codelists():
    """Drop any codelist rows the test created so the dev SQLite DB does
    not accrete fixture rows across runs.

    Snapshot the codelist ids before the test, run the test, then delete
    the diff. Cascades to ``codelist_decisions`` and ``audit_log`` rows
    via explicit DELETE so we don't depend on schema-level FK CASCADE.
    """
    from app.db.hitl_store import get_connection
    conn = get_connection()
    pre_ids = {r["id"] for r in conn.execute("SELECT id FROM codelists")}
    yield
    post_ids = {r["id"] for r in conn.execute("SELECT id FROM codelists")}
    new_ids = post_ids - pre_ids
    if not new_ids:
        return
    placeholders = ",".join(["?"] * len(new_ids))
    params = list(new_ids)
    conn.execute(f"DELETE FROM audit_log WHERE codelist_id IN ({placeholders})", params)
    conn.execute(f"DELETE FROM codelist_decisions WHERE codelist_id IN ({placeholders})", params)
    conn.execute(f"DELETE FROM codelists WHERE id IN ({placeholders})", params)
    conn.commit()


# --- compute_overlap unit tests --------------------------------------------

def test_compute_overlap_normal_case():
    gen = {"E10", "E11", "E12"}
    phen = {"E10", "E11", "E13", "E14"}
    out = pd.compute_overlap(gen, phen)
    assert out["n_generated_codes"] == 3
    assert out["n_phenotype_codes"] == 4
    assert out["n_intersection"] == 2
    # Jaccard = 2/5 = 0.4
    assert out["overlap_jaccard"] == 0.4
    # 2/3 of generated codes are in the phenotype
    assert out["overlap_generated_in_phenotype"] == round(2 / 3, 4)
    # 2/4 of phenotype codes are in generated
    assert out["overlap_phenotype_in_generated"] == 0.5


def test_compute_overlap_empty_generated_set_no_division_error():
    # Edge case: empty codelist (rare but possible during draft).
    # All-zero output is the explicit contract; the caller decides
    # whether to surface the row or hide it.
    out = pd.compute_overlap(set(), {"E10", "E11"})
    assert out["overlap_jaccard"] == 0.0
    assert out["overlap_generated_in_phenotype"] == 0.0
    # 0/2 of phenotype codes are in (empty) generated -> 0.0
    assert out["overlap_phenotype_in_generated"] == 0.0
    assert out["n_generated_codes"] == 0
    assert out["n_phenotype_codes"] == 2
    assert out["n_intersection"] == 0


def test_compute_overlap_empty_phenotype_set_no_division_error():
    out = pd.compute_overlap({"E10", "E11"}, set())
    assert out["overlap_jaccard"] == 0.0
    assert out["overlap_generated_in_phenotype"] == 0.0
    assert out["overlap_phenotype_in_generated"] == 0.0
    assert out["n_phenotype_codes"] == 0


def test_compute_overlap_both_empty_returns_all_zeros():
    out = pd.compute_overlap(set(), set())
    assert out["overlap_jaccard"] == 0.0
    assert out["overlap_generated_in_phenotype"] == 0.0
    assert out["overlap_phenotype_in_generated"] == 0.0


def test_compute_overlap_disjoint_sets_zero_jaccard():
    out = pd.compute_overlap({"E10"}, {"J45"})
    assert out["n_intersection"] == 0
    assert out["overlap_jaccard"] == 0.0
    assert out["overlap_generated_in_phenotype"] == 0.0
    assert out["overlap_phenotype_in_generated"] == 0.0


def test_compute_overlap_full_match_jaccard_one():
    s = {"E10", "E11"}
    out = pd.compute_overlap(s, s)
    assert out["overlap_jaccard"] == 1.0
    assert out["overlap_generated_in_phenotype"] == 1.0
    assert out["overlap_phenotype_in_generated"] == 1.0


# --- fetch_phenotype_codes file-cache round-trip --------------------------

def test_fetch_phenotype_codes_roundtrips_via_file_cache(tmp_path):
    # First call writes the cache; second call (with the live session
    # patched to raise) hits the cache and returns the same set.
    pd.clear_discovery_cache()
    test_dir = tmp_path / "phen_cache"
    payload_codes = [
        {"code": "E10", "coding_system": {"name": "ICD10"}},
        {"code": "E11.0", "coding_system": {"name": "ICD10"}},
        {"code": "  E12  ", "coding_system": {"name": "ICD10"}},  # whitespace-stripped
    ]

    class _FakeResp:
        def __init__(self, payload):
            self._p = payload
            self.status_code = 200

        def json(self):
            return self._p

        def raise_for_status(self):
            pass

    class _FakeSess:
        def __init__(self, payload):
            self._p = payload
            self.calls = 0
            self.headers: dict = {}

        def get(self, *_a, **_kw):
            self.calls += 1
            return _FakeResp(self._p)

    sess = _FakeSess(payload_codes)
    with patch.object(pd, "_PHENOTYPE_CACHE_DIR", test_dir):
        codes_first = pd.fetch_phenotype_codes(sess, "PH12")
    # Codes are normalised (dots stripped, whitespace stripped)
    assert codes_first == {"E10", "E110", "E12"}
    assert sess.calls == 1

    # Second call: even with a session that would raise on .get(), the
    # cache hit avoids the network round-trip entirely.
    class _ExplodingSess:
        headers: dict = {}

        def get(self, *_a, **_kw):
            raise RuntimeError("should not be called")

    with patch.object(pd, "_PHENOTYPE_CACHE_DIR", test_dir):
        codes_second = pd.fetch_phenotype_codes(_ExplodingSess(), "PH12")
    assert codes_second == codes_first


def test_fetch_phenotype_codes_refresh_bypasses_cache(tmp_path):
    pd.clear_discovery_cache()
    test_dir = tmp_path / "phen_cache"

    class _FakeResp:
        def __init__(self, payload):
            self._p = payload
            self.status_code = 200

        def json(self):
            return self._p

        def raise_for_status(self):
            pass

    class _FakeSess:
        def __init__(self, payload):
            self._p = payload
            self.calls = 0
            self.headers: dict = {}

        def get(self, *_a, **_kw):
            self.calls += 1
            return _FakeResp(self._p)

    sess = _FakeSess([{"code": "X1", "coding_system": {"name": "ICD10"}}])
    with patch.object(pd, "_PHENOTYPE_CACHE_DIR", test_dir):
        first = pd.fetch_phenotype_codes(sess, "PH12")
        assert sess.calls == 1
        # refresh=True forces a re-fetch even though the cache file is fresh
        second = pd.fetch_phenotype_codes(sess, "PH12", refresh=True)
        assert sess.calls == 2
    assert first == second == {"X1"}


# --- Endpoint integration --------------------------------------------------

def _login_demo_user():
    """Sign in as the seeded demo reviewer so auth-gated routes work."""
    users = client.get("/api/auth/users").json()
    if not users:
        return None
    res = client.post("/api/auth/login", json={"user_id": users[0]["id"]})
    return res.json() if res.status_code == 200 else None


def _create_codelist_with_codes(codes: list[dict]) -> str:
    """Seed a codelist via /api/search + /api/codelists. ``codes`` items are
    {code, vocabulary, term} dicts; everything else (decision, sources)
    is filled in with sensible defaults."""
    # We can't call /api/search end-to-end (it kicks off the whole
    # pipeline), so we go straight to the in-memory search cache.
    from app.api import _search_cache
    import uuid
    sid = uuid.uuid4().hex[:12]
    seeded = []
    for c in codes:
        seeded.append({
            "code": c["code"],
            "term": c.get("term", ""),
            "vocabulary": c.get("vocabulary", "ICD-10"),
            "decision": "include",
            "confidence": 0.9,
            "rationale": "test seed",
            "sources": ["test"],
        })
    _search_cache.put(sid, "type 2 diabetes", seeded)
    res = client.post("/api/codelists", json={"search_id": sid, "name": "T35 fixture"})
    assert res.status_code == 201, res.text
    return res.json()["id"]


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
    """One session impersonating both HDR UK search and codelist fetch."""

    def __init__(self, search_data, codes_by_id):
        self._search_data = search_data
        self._codes_by_id = codes_by_id
        self.headers: dict = {}
        self.search_calls = 0
        self.codelist_calls: dict[str, int] = {}

    def get(self, url, params=None, timeout=None):
        if "/export/codes" in url:
            for pid, payload in self._codes_by_id.items():
                if f"/phenotypes/{pid}/" in url or url.endswith(f"/phenotypes/{pid}/export/codes/"):
                    self.codelist_calls[pid] = self.codelist_calls.get(pid, 0) + 1
                    return _FakeResponse(payload=payload)
            return _FakeResponse(status_code=404, payload={})
        # otherwise treat as search
        self.search_calls += 1
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
    def __init__(self, decisions):
        self._d = decisions

    def invoke(self, _messages):
        return pd._PhenotypeRelevanceBatch(decisions=[
            pd._PhenotypeRelevance(phenotype_id=pid, relevant=v["relevant"], reason=v["reason"])
            for pid, v in self._d.items()
        ])


class _FakeChatAnthropic:
    def __init__(self, structured):
        self._structured = structured

    def with_structured_output(self, _schema):
        return self._structured


def _patch_pipeline(search_data, decisions, codes_by_id, cache_dir):
    sess = _FakeSession(search_data, codes_by_id)
    return [
        patch.object(pd.requests, "Session", lambda: sess),
        patch.object(pd, "ChatAnthropic", lambda *_a, **_kw: _FakeChatAnthropic(_FakeStructuredLLM(decisions))),
        patch.object(pd, "HDR_UK_USE_JUDGE", True),
        patch.object(pd, "ANTHROPIC_API_KEY", "dummy"),
        patch.object(pd, "_PHENOTYPE_CACHE_DIR", cache_dir),
    ]


def test_cross_reference_returns_overlap_for_each_relevant_phenotype(tmp_path):
    user = _login_demo_user()
    assert user is not None, "demo user seeding required"
    pd.clear_discovery_cache()

    # Codelist with E10, E11.0, E12 (will normalise to E10, E110, E12)
    cid = _create_codelist_with_codes([
        {"code": "E10", "vocabulary": "ICD-10"},
        {"code": "E11.0", "vocabulary": "ICD-10"},
        {"code": "E12", "vocabulary": "ICD-10"},
    ])

    search = [
        {"phenotype_id": "PH50", "name": "Type 2 diabetes",
         "type": [{"name": "Disease or syndrome"}],
         "coding_system": [{"name": "ICD10"}],
         "data_sources": [{"name": "CPRD GOLD"}],
         "publications": [{"details": "Diabetes UK methods 2024"}]},
        {"phenotype_id": "PH99", "name": "Off-target",
         "type": [{"name": "Disease or syndrome"}],
         "coding_system": [{"name": "ICD10"}], "data_sources": [], "publications": []},
    ]
    decisions = {
        "PH50": {"relevant": True, "reason": "diabetes match"},
        "PH99": {"relevant": False, "reason": "different condition"},
    }
    codes_by_id = {
        "PH50": [
            {"code": "E10",   "coding_system": {"name": "ICD10"}},
            {"code": "E11.0", "coding_system": {"name": "ICD10"}},
            {"code": "E13",   "coding_system": {"name": "ICD10"}},
        ],
    }

    cache_dir = tmp_path / "phen_cache"
    with _patch_pipeline(search, decisions, codes_by_id, cache_dir)[0], \
         _patch_pipeline(search, decisions, codes_by_id, cache_dir)[1], \
         _patch_pipeline(search, decisions, codes_by_id, cache_dir)[2], \
         _patch_pipeline(search, decisions, codes_by_id, cache_dir)[3], \
         _patch_pipeline(search, decisions, codes_by_id, cache_dir)[4]:
        r = client.get(f"/api/codelists/{cid}/cross-reference")

    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1  # PH99 dropped by judge
    row = body[0]
    assert row["phenotype_id"] == "PH50"
    assert row["hdruk_url"].endswith("/phenotypes/PH50")
    # Generated = {E10, E110, E12}, phenotype = {E10, E110, E13}
    # intersection = {E10, E110}, union = {E10, E110, E12, E13}
    # Jaccard = 2/4 = 0.5
    assert row["overlap_jaccard"] == 0.5
    # 2/3 of generated codes in phenotype
    assert row["overlap_generated_in_phenotype"] == round(2 / 3, 4)
    # 2/3 of phenotype codes in generated
    assert row["overlap_phenotype_in_generated"] == round(2 / 3, 4)
    assert row["n_intersection"] == 2
    assert row["data_sources"] == ["CPRD GOLD"]
    assert "Diabetes UK" in row["first_publication"]
    assert row["relevance_rationale"] == "diabetes match"


def test_cross_reference_empty_codelist_returns_empty_list():
    user = _login_demo_user()
    assert user is not None
    pd.clear_discovery_cache()

    # Codelist whose only decision is "exclude" -> no included codes
    from app.api import _search_cache
    import uuid
    sid = uuid.uuid4().hex[:12]
    _search_cache.put(sid, "asthma", [{
        "code": "X1", "term": "irrelevant", "vocabulary": "ICD-10",
        "decision": "exclude", "confidence": 0.9, "rationale": "test", "sources": [],
    }])
    res = client.post("/api/codelists", json={"search_id": sid, "name": "Empty fixture"})
    cid = res.json()["id"]

    r = client.get(f"/api/codelists/{cid}/cross-reference")
    assert r.status_code == 200
    assert r.json() == []


def test_cross_reference_unknown_codelist_returns_404():
    user = _login_demo_user()
    assert user is not None
    r = client.get("/api/codelists/no-such-id/cross-reference")
    assert r.status_code == 404


def test_cross_reference_caches_phenotype_codelist_across_requests(tmp_path):
    # Two consecutive cross-reference requests for the same codelist.
    # First call writes the per-phenotype file cache; second call hits
    # the cache and does NOT issue the codelist-fetch HTTP request.
    user = _login_demo_user()
    assert user is not None
    pd.clear_discovery_cache()

    cid = _create_codelist_with_codes([
        {"code": "J45", "vocabulary": "ICD-10"},
    ])

    search = [{
        "phenotype_id": "PH12", "name": "Asthma",
        "type": [{"name": "Disease or syndrome"}],
        "coding_system": [{"name": "ICD10"}],
        "data_sources": [], "publications": [],
    }]
    decisions = {"PH12": {"relevant": True, "reason": "matches"}}
    codes_by_id = {"PH12": [{"code": "J45", "coding_system": {"name": "ICD10"}}]}

    cache_dir = tmp_path / "phen_cache"
    sess = _FakeSession(search, codes_by_id)

    with patch.object(pd.requests, "Session", lambda: sess), \
         patch.object(pd, "ChatAnthropic", lambda *_a, **_kw: _FakeChatAnthropic(_FakeStructuredLLM(decisions))), \
         patch.object(pd, "HDR_UK_USE_JUDGE", True), \
         patch.object(pd, "ANTHROPIC_API_KEY", "dummy"), \
         patch.object(pd, "_PHENOTYPE_CACHE_DIR", cache_dir):
        r1 = client.get(f"/api/codelists/{cid}/cross-reference")
        r2 = client.get(f"/api/codelists/{cid}/cross-reference")

    assert r1.status_code == 200 and r2.status_code == 200
    # Phenotype codelist fetched exactly once across the two requests
    # (file cache served the second). Search + judge are also cached
    # via the in-process discovery cache.
    assert sess.codelist_calls.get("PH12", 0) == 1

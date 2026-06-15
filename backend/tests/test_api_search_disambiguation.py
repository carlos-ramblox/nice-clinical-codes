"""End-to-end (TestClient) check that /api/search surfaces the T37
disambiguation field. run_pipeline is mocked at the route boundary so the
test stays offline (same pattern as test_baseline_route.py)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("OMOPHUB_API_KEY", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("OPENROUTER_API_KEY", "dummy")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def _state_with(disambiguation: list[dict]) -> dict:
    return {
        "final_code_list": [],
        "parsed_conditions": [{"name": "multiple sclerosis"}],
        "summary": {},
        "provenance_trail": [],
        "disambiguation_suggestions": disambiguation,
    }


def test_search_surfaces_ambiguous_abbreviation():
    state = _state_with([{
        "original_term": "MS",
        "interpreted_as": "multiple sclerosis",
        "alternatives": ["mitral stenosis"],
        "reason": "ambiguous_abbreviation",
        "detected_language": "en",
    }])
    with patch("app.api.routes.run_pipeline", new=AsyncMock(return_value=state)):
        resp = client.post("/api/search", json={"query": "MS"})

    assert resp.status_code == 200, resp.text
    disambiguation = resp.json()["disambiguation"]
    assert disambiguation and disambiguation[0]["reason"] == "ambiguous_abbreviation"
    assert disambiguation[0]["alternatives"] == ["mitral stenosis"]


def test_unambiguous_search_has_null_disambiguation():
    """No suggestions → the field is null, not an empty list, so the frontend
    banner stays absent on the happy path."""
    with patch("app.api.routes.run_pipeline", new=AsyncMock(return_value=_state_with([]))):
        resp = client.post("/api/search", json={"query": "Type 2 diabetes mellitus"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["disambiguation"] is None


def test_disambiguate_endpoint_surfaces_entries():
    """The parse-only endpoint returns flagged suggestions without running the
    pipeline (parse_query is mocked, so no LLM/retriever calls happen)."""
    parsed = {"conditions": [], "coding_systems": [], "disambiguation_suggestions": [
        {"original_term": "MS", "interpreted_as": "multiple sclerosis",
         "alternatives": ["mitral stenosis"], "reason": "ambiguous_abbreviation",
         "detected_language": "en"},
    ]}
    with patch("app.api.routes.parse_query", return_value=parsed):
        resp = client.get("/api/disambiguate", params={"query": "MS"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body[0]["reason"] == "ambiguous_abbreviation"
    assert body[0]["alternatives"] == ["mitral stenosis"]


def test_disambiguate_endpoint_empty_when_unambiguous():
    parsed = {"conditions": [], "coding_systems": [], "disambiguation_suggestions": []}
    with patch("app.api.routes.parse_query", return_value=parsed):
        resp = client.get("/api/disambiguate", params={"query": "Type 2 diabetes mellitus"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_disambiguate_endpoint_rejects_too_short_query():
    resp = client.get("/api/disambiguate", params={"query": "M"})
    assert resp.status_code == 422


def test_malformed_disambiguation_entry_does_not_sink_results():
    """A suggestion with a reason outside the Literal is dropped, not 500'd —
    disambiguation is informational and must never sink a scored response."""
    state = _state_with([
        {"original_term": "x", "interpreted_as": "y", "alternatives": [],
         "reason": "not_a_real_reason", "detected_language": "en"},
        {"original_term": "MS", "interpreted_as": "multiple sclerosis",
         "alternatives": ["mitral stenosis"], "reason": "ambiguous_abbreviation",
         "detected_language": "en"},
    ])
    with patch("app.api.routes.run_pipeline", new=AsyncMock(return_value=state)):
        resp = client.post("/api/search", json={"query": "MS"})

    assert resp.status_code == 200, resp.text
    disambiguation = resp.json()["disambiguation"]
    assert [d["reason"] for d in disambiguation] == ["ambiguous_abbreviation"]

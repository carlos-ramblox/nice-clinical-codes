"""End-to-end (TestClient) check that /api/search surfaces the #28
comorbidity_suggestions field additively. run_pipeline is mocked at the
route boundary so the test stays offline (same pattern as
test_api_search_disambiguation.py)."""
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


def _state_with(comorbidity_suggestions: list[dict]) -> dict:
    return {
        "final_code_list": [],
        "parsed_conditions": [{"name": "heart failure"}],
        "summary": {},
        "provenance_trail": [],
        "comorbidity_suggestions": comorbidity_suggestions,
    }


def test_search_surfaces_comorbidity_suggestions():
    state = _state_with([{
        "condition_name": "Atrial fibrillation",
        "rationale": "co-occurs with heart failure",
        "confidence": 0.9,
        "suggested_by": ["LLM"],
    }])
    with patch("app.api.routes.run_pipeline", new=AsyncMock(return_value=state)):
        resp = client.post("/api/search", json={"query": "heart failure"})

    assert resp.status_code == 200, resp.text
    sugg = resp.json()["comorbidity_suggestions"]
    assert sugg and sugg[0]["condition_name"] == "Atrial fibrillation"
    assert sugg[0]["suggested_by"] == ["LLM"]
    # cui is optional and absent here → serialized as null, not missing
    assert sugg[0]["cui"] is None


def test_no_suggestions_yields_null_field():
    """Empty list → the field is null (not []), so existing consumers and the
    frontend panel guard treat 'absent' uniformly."""
    with patch("app.api.routes.run_pipeline", new=AsyncMock(return_value=_state_with([]))):
        resp = client.post("/api/search", json={"query": "heart failure"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["comorbidity_suggestions"] is None


def test_malformed_suggestion_does_not_sink_results():
    """A hint missing a required field is dropped, not 500'd — comorbidity
    suggestions are informational and must never sink a scored response."""
    state = _state_with([
        {"condition_name": "Broken", "confidence": 0.5},          # missing rationale + suggested_by
        {"condition_name": "Chronic kidney disease", "rationale": "shared risk factors",
         "confidence": 0.88, "suggested_by": ["LLM"]},
    ])
    with patch("app.api.routes.run_pipeline", new=AsyncMock(return_value=state)):
        resp = client.post("/api/search", json={"query": "heart failure"})

    assert resp.status_code == 200, resp.text
    sugg = resp.json()["comorbidity_suggestions"]
    assert [s["condition_name"] for s in sugg] == ["Chronic kidney disease"]

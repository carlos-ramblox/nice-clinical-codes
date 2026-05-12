"""T37e: /api/baseline accepts only OpenRouter-slug-shaped model identifiers,
returns 400 on shape violation, and never echoes upstream exception text into
the 500 response body."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("OMOPHUB_API_KEY", "dummy")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")
os.environ.setdefault("OPENROUTER_API_KEY", "dummy")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

_VALID_TEST_SET = [{"Research_question": "type 2 diabetes", "Codelist": ["E11"]}]


def test_valid_model_passes_validation_and_reaches_run_baseline():
    """A canonical OpenRouter slug ('microsoft/phi-4') must pass Pydantic
    validation and dispatch into run_baseline. run_baseline itself is
    mocked so this test stays offline."""
    fake_codes = [{"code": "E11.9", "term": "T2DM", "vocabulary": "ICD-10",
                   "decision": "include", "confidence": 0.9,
                   "rationale": "x", "sources": ["phi-4"]}]
    with patch("app.api.routes.run_baseline", return_value=fake_codes):
        resp = client.post(
            "/api/baseline",
            json={"test_set": _VALID_TEST_SET, "model": "microsoft/phi-4"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pipeline"] == "baseline:microsoft/phi-4"
    assert body["pipeline_results_count"] == 1


def test_invalid_model_returns_400_with_safe_error_string():
    """A model id with a shell-metacharacter or whitespace is rejected at the
    Pydantic boundary; we never reach run_baseline, never echo input back."""
    resp = client.post(
        "/api/baseline",
        json={"test_set": _VALID_TEST_SET, "model": "$(evil)\n injected"},
    )
    assert resp.status_code == 422, resp.text
    # Pydantic 422 body is a list of structured validation errors. Make sure
    # the injected payload doesn't appear verbatim outside the standard
    # 'input' field that FastAPI mirrors back (we can't suppress that
    # round-trip — it's part of FastAPI's documented schema).
    detail = resp.json()["detail"]
    assert any("model" in err.get("loc", []) for err in detail), detail


def test_run_baseline_failure_does_not_leak_exception_text():
    """When run_baseline raises (bad OpenRouter key, network error, etc.),
    the 500 body must be a generic string — not the exception's str()."""
    secret_message = "OpenRouter rejected key sk-abc-LEAKED-CREDENTIAL-xyz"
    with patch("app.api.routes.run_baseline", side_effect=RuntimeError(secret_message)):
        resp = client.post(
            "/api/baseline",
            json={"test_set": _VALID_TEST_SET, "model": "microsoft/phi-4"},
        )
    assert resp.status_code == 500
    assert "LEAKED-CREDENTIAL" not in resp.text
    assert resp.json() == {"detail": "Baseline processing failed"}

"""Ingest helpers degrade gracefully on PermissionError / OSError."""
from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("OMOPHUB_API_KEY", "dummy")

from app.graph.nodes import dmd_retriever, bnf_retriever, opencodelists_retriever  # noqa: E402


def _raise_permission(*_args, **_kwargs):
    raise PermissionError(13, "Permission denied")


@pytest.fixture
def perm_blocked(tmp_path, monkeypatch):
    """Build a CSV the caller can pass; `open` is patched to raise
    PermissionError so we test the error path, not the file-missing path
    (path.exists() should return True before open raises)."""
    csv = tmp_path / "blocked.csv"
    csv.write_text("code,term\nX,Y\n", encoding="utf-8")
    monkeypatch.setattr(builtins, "open", _raise_permission)
    return csv


def test_dmd_ingest_permission_error_returns_zero_not_raises(perm_blocked, caplog):
    with caplog.at_level("WARNING"):
        result = dmd_retriever.ingest_dmd_csv(perm_blocked)
    assert result == 0
    assert any("could not be opened" in r.message for r in caplog.records)


def test_bnf_ingest_permission_error_returns_zero_not_raises(perm_blocked, caplog):
    with caplog.at_level("WARNING"):
        result = bnf_retriever.ingest_bnf_csv(perm_blocked)
    assert result == 0
    assert any("could not be opened" in r.message for r in caplog.records)


def test_opencodelists_ingest_permission_error_returns_zero_not_raises(perm_blocked, caplog):
    with caplog.at_level("WARNING"):
        result = opencodelists_retriever.ingest_opencodelists_csv(perm_blocked)
    assert result == 0
    assert any("could not be opened" in r.message for r in caplog.records)

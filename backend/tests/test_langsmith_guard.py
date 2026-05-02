"""Tests for the LangSmith tracing-misconfigured guard in app.main."""

import pytest

from app.main import _langsmith_tracing_misconfigured


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"LANGCHAIN_TRACING_V2": "false"},
        {"LANGCHAIN_TRACING_V2": "false", "LANGCHAIN_API_KEY": "key"},
        {"LANGSMITH_TRACING": "false"},
    ],
)
def test_tracing_off_does_not_warn(env):
    assert _langsmith_tracing_misconfigured(env) is False


@pytest.mark.parametrize(
    "env",
    [
        {"LANGCHAIN_TRACING_V2": "true", "LANGCHAIN_API_KEY": "key"},
        {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "key"},
        # mixed prefixes — langsmith client accepts both
        {"LANGCHAIN_TRACING_V2": "true", "LANGSMITH_API_KEY": "key"},
        {"LANGSMITH_TRACING": "true", "LANGCHAIN_API_KEY": "key"},
    ],
)
def test_tracing_on_with_key_does_not_warn(env):
    assert _langsmith_tracing_misconfigured(env) is False


@pytest.mark.parametrize("truthy", ["true", "True", "TRUE", "1", "yes", "on", " true "])
def test_truthy_values_with_no_key_warn(truthy):
    assert _langsmith_tracing_misconfigured({"LANGCHAIN_TRACING_V2": truthy}) is True
    assert _langsmith_tracing_misconfigured({"LANGSMITH_TRACING": truthy}) is True


@pytest.mark.parametrize("not_truthy", ["", "0", "no", "off", "false", "False"])
def test_non_truthy_values_do_not_warn(not_truthy):
    assert (
        _langsmith_tracing_misconfigured({"LANGCHAIN_TRACING_V2": not_truthy}) is False
    )

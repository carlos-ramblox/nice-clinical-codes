"""Shared pytest configuration for the backend test suite."""


def pytest_configure(config):
    # Register the `live` marker so opt-in network tests (e.g. the OLS4
    # smoke test) don't raise "unknown mark" warnings. Live tests are
    # additionally env-gated and skipped by default — see test_ols4_live.py.
    config.addinivalue_line(
        "markers",
        "live: opt-in test that hits real external services over the network.",
    )

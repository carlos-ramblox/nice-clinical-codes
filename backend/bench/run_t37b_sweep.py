"""T37b — K=5 v2 disease benchmark sweep wrapper.

Thin entry-point that injects truststore (for Avast TLS interception
on the dev host) and then runs the standard K=5 variance sweep. The
sweep writes per-codelist .result_runK_<k>.json files exactly as
``run_variance_k5`` does in CI / Docker.

Usage:
    cd backend && python -m bench.run_t37b_sweep
"""
from __future__ import annotations

import truststore
truststore.inject_into_ssl()

import asyncio  # noqa: E402
from app.evaluation.run_variance_k5 import run  # noqa: E402


def main() -> None:
    asyncio.run(run(runs=5, cap_usd=20.0, codelists=None, pause_after_first=False))


if __name__ == "__main__":
    main()

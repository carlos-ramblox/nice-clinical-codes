"""K=5 sweep wrapper; truststore-injects on the dev host (Avast).

Run: cd backend && python -m bench.run_t37b_sweep
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

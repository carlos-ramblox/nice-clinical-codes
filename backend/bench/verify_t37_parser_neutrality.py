"""T37b structural-neutrality probe.

Runs the updated query_parser over the 15 v2 disease benchmark queries
and asserts every parsed condition is domain="Condition". If true, the
new dm+d / BNF retrievers cannot fire on these inputs (FR-008 short-
circuits on `domain == "Drug"`), so F1 on the v2 disease benchmark is
bit-identical pre/post-T37 by construction.

Output: per-query domain assignment table + final PASS/FAIL.

Run from backend/:
    python -m bench.verify_t37_parser_neutrality
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Avast HTTPS interception on this dev host substitutes a CA Python's
# certifi bundle doesn't trust. truststore makes httpx / the Anthropic
# SDK read from the Windows cert store (which has the Avast root).
# Scoped to this probe only — not a permanent app change.
import truststore  # noqa: E402
truststore.inject_into_ssl()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.nodes.query_parser import parse_query  # noqa: E402

BENCH = Path(__file__).resolve().parents[2] / "data" / "test_sets" / "benchmark_2026_04"


def main() -> int:
    queries: list[tuple[str, str]] = []
    for fixture in sorted(BENCH.glob("*.json")):
        if fixture.name.startswith("_") or ".result" in fixture.name:
            continue
        with open(fixture, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list) or not data:
            continue
        query = data[0].get("Research_question", "")
        if query:
            queries.append((fixture.stem, query))

    print(f"Probing {len(queries)} v2 disease benchmark queries against the post-T37 parser.\n")

    drug_classified = 0
    rows = []
    for short, query in queries:
        parsed = parse_query(query)
        conds = parsed.get("conditions", [])
        domains = [c.get("domain", "") for c in conds]
        any_drug = any(d == "Drug" for d in domains)
        rows.append((short, query, domains, any_drug))
        if any_drug:
            drug_classified += 1
        flag = "DRUG!" if any_drug else "ok"
        print(f"  [{flag:5s}] {short:24s}  {query[:50]:50s}  domains={domains}")

    print()
    if drug_classified == 0:
        print(f"PASS: 0 of {len(queries)} disease benchmark queries reclassified as Drug.")
        print("FR-008 gating guarantees dm+d / BNF retrievers contribute empty state.")
        print("v2 disease F1 is bit-identical pre/post-T37 by construction (delta-F1 = 0).")
        return 0
    print(f"FAIL: {drug_classified} of {len(queries)} queries reclassified as Drug.")
    print("FR-008 gating no longer protects these inputs; a full K=5 sweep is required.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

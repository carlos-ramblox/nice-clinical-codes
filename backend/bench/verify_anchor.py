"""Anchor-verification harness for the comorbidity suggester.

Usage (from ``backend/``)::

    python bench/verify_anchor.py
    python bench/verify_anchor.py "patients with heart failure and type 2 diabetes"

Requires ANTHROPIC_API_KEY and retriever backends reachable (see repo-root docker-compose.yml).
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from app.graph.graph import run_pipeline  # noqa: E402
from app.graph.nodes.comorbidity_suggester import _extract_anchor  # noqa: E402

# multi-condition query exercises the primary/comorbidity split
DEFAULT_QUERY = "patients with heart failure and type 2 diabetes"


def _rule(label: str) -> None:
    print(f"\n{'=' * 8} {label} {'=' * 8}")


async def main(query: str) -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print(
            "WARNING: ANTHROPIC_API_KEY is not set — the parser and scorer "
            "LLM calls will fail. Set it in your shell or backend/.env.\n",
            file=sys.stderr,
        )

    print(f"Query: {query!r}")
    result = await run_pipeline(query)

    parsed = result.get("parsed_conditions", [])
    final = result.get("final_code_list", [])
    included = [c for c in final if c.get("decision") == "include"]

    _rule("parsed conditions (primary vs comorbidity split)")
    for c in parsed:
        print(f"  - {c.get('condition_type'):<11} {c.get('name')}")

    _rule("anchor produced by _extract_anchor")
    anchor = _extract_anchor(final, parsed)
    print(f"  primary_names ({len(anchor['primary_names'])}): {anchor['primary_names']}")
    print(f"  included_terms ({len(anchor['included_terms'])}):")
    for t in anchor["included_terms"]:
        print(f"    - {t}")

    _rule("summary")
    print(f"  total final codes:   {len(final)}")
    print(f"  included codes:      {len(included)}")
    print(f"  primary conditions:  {len(anchor['primary_names'])}")
    print(f"  comorbidity_suggestions: {result.get('comorbidity_suggestions')}")

    _rule("judge")
    print("  1. Is the primary/comorbidity split correct above?")
    print("  2. Does the anchor keep ONLY the primary condition name(s)?")
    print("  3. Are the included_terms tight + on-topic, or noisy/too broad?")
    print("     -> tight  : simple anchor holds, proceed to API step.")
    print("     -> broad  : refine code->condition attribution before the LLM step.")


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    asyncio.run(main(q))

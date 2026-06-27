"""Anchor-verification harness for the comorbidity suggester (issue #28).

Runs a real query through ``run_pipeline`` and reports whether the
skeleton's anchor extraction holds up against live pipeline state — the
one genuinely uncertain thing before the LLM source is wired in
(Phase-1 step 6). ``ScoredCode`` carries no back-link to the condition
that produced it, so the skeleton anchors on "primary condition names +
*all* included terms". This script lets you judge whether that compromise
yields a clean, on-topic anchor or whether code->condition attribution is
needed first.

Usage (from the ``backend/`` directory)::

    python -m scripts.verify_anchor
    python -m scripts.verify_anchor "patients with heart failure and type 2 diabetes"

Requires ``ANTHROPIC_API_KEY`` (parser + scorer LLM calls) and the
retriever backends reachable (Chroma, OMOPHub, etc. — see the repo-root
``docker-compose.yml``). A ``backend/.env`` is auto-loaded by ``config``.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Allow `python scripts/verify_anchor.py ...` as well as `-m scripts...`:
# ensure the backend root (which holds the `app` package) is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# INFO so the node's "Comorbidity anchor: ..." line actually prints.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from app.graph.graph import run_pipeline  # noqa: E402
from app.graph.nodes.comorbidity_suggester import _extract_anchor  # noqa: E402

# A multi-condition query so the primary/comorbidity split is actually
# exercised — anchoring must keep the primary and drop the comorbidity.
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
    print(f"  comorbidity_suggestions (skeleton -> expect []): "
          f"{result.get('comorbidity_suggestions')}")

    _rule("judge")
    print("  1. Is the primary/comorbidity split correct above?")
    print("  2. Does the anchor keep ONLY the primary condition name(s)?")
    print("  3. Are the included_terms tight + on-topic, or noisy/too broad?")
    print("     -> tight  : simple anchor holds, proceed to API step (step 4).")
    print("     -> broad  : refine code->condition attribution before the LLM step.")


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    asyncio.run(main(q))

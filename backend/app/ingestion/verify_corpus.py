"""Post-ingest build guardrail: fail the image build if a required
vocabulary is missing from ChromaDB.

``run_all`` only logs a warning and continues when a source file is absent
from the build context, so an image could ship with zero OPCS-4 (or ICD-10)
codes and nobody would notice until a user searched for one. This check
turns that silent gap into a non-zero exit that fails ``docker build``.

The licensed TRUD XML these floors depend on reaches the build via
``aws/fetch_reference_data.sh``; see backend/Dockerfile for where this runs.
"""

import logging
import sys

from app.db.vector_store import count_by_vocabulary

logger = logging.getLogger(__name__)

# Floors, not exact counts: high enough that an empty or partial ingest
# trips the guard, low enough to tolerate routine release-to-release drift.
# dm+d / BNF are intentionally absent — they are optional enrichment vocabs,
# not core retrieval corpora.
REQUIRED_MIN = {
    "SNOMED CT": 1000,
    "ICD-10 (WHO)": 1000,
    "OPCS-4": 1000,
}


def main() -> int:
    counts = count_by_vocabulary()

    print("ChromaDB vocabulary counts:")
    for vocab, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {vocab}: {n}")

    missing = [
        f"{vocab}: {counts.get(vocab, 0)} (need >= {floor})"
        for vocab, floor in REQUIRED_MIN.items()
        if counts.get(vocab, 0) < floor
    ]
    if missing:
        print(
            "\nCORPUS CHECK FAILED — refusing to build an incomplete image:",
            file=sys.stderr,
        )
        for line in missing:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nLicensed TRUD XML is probably missing from the build context. "
            "Run aws/fetch_reference_data.sh before docker build.",
            file=sys.stderr,
        )
        return 1

    print("\nCorpus check passed.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())

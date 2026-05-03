"""
T06 smoke-test seeder. Run from backend/:

    HITL_DATABASE_URL=sqlite:///./data/hitl_t06_smoke.db \
        python -m tests._seed_t06_smoke

Inserts one draft codelist with seven hand-crafted decisions covering
the sort edge cases, then prints the codelist id so the Playwright run
can navigate to it. Idempotent: if the codelist already exists it is
left alone.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db import hitl_store


CODELIST_NAME = "T06 Smoke (uncertainty sort)"

DECISIONS = [
    # (code, term, vocabulary, decision, confidence, rationale)
    ("AAA001", "high-confidence include",  "SNOMED",  "include",   0.95, "AI is sure this is in scope"),
    ("BBB002", "high-confidence exclude",  "SNOMED",  "exclude",   0.05, "AI is sure this is out of scope"),
    ("CCC003", "borderline (0.50)",         "SNOMED",  "include",   0.50, "least sure — surface first"),
    ("DDD004", "moderate (0.70)",           "SNOMED",  "include",   0.70, "moderately sure"),
    ("EEE005", "explicit uncertain",        "SNOMED",  "uncertain", 0.99, "LLM flagged uncertain — pin to top"),
    ("FFF006", "borderline (0.55)",         "ICD-10",  "exclude",   0.55, "near-50/50, exclude side"),
    ("GGG007", "moderate (0.30)",           "ICD-10",  "exclude",   0.30, "moderately sure on exclude"),
]


def seed() -> str:
    # Use the first seeded demo user as the creator
    users = hitl_store.list_users()
    if not users:
        raise RuntimeError("no demo users — DB init must have failed")
    creator = users[0]

    # Skip if a codelist with this name already exists
    for cl in hitl_store.list_codelists():
        if cl["name"] == CODELIST_NAME:
            print(cl["id"])
            return cl["id"]

    decisions = [
        {
            "code": code,
            "term": term,
            "vocabulary": vocab,
            "decision": decision,
            "confidence": conf,
            "rationale": rationale,
            "sources": ["SMOKE_TEST"],
        }
        for code, term, vocab, decision, conf, rationale in DECISIONS
    ]

    cid = hitl_store.create_codelist(
        name=CODELIST_NAME,
        query="t06 smoke test query",
        created_by=creator["id"],
        decisions=decisions,
    )
    print(cid)
    return cid


if __name__ == "__main__":
    db = os.getenv("HITL_DATABASE_URL", "(default)")
    print(f"# HITL_DATABASE_URL={db}", flush=True)
    seed()

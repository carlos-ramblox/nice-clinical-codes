"""dm+d level inference, shared by the dm+d retriever (write path) and
hitl_store.get_codelist (read path). Lives in services/ so the db layer
does not have to import from app.graph.nodes.
"""
from __future__ import annotations

import re
from typing import Literal

VOCABULARY = "dm+d"

DmdLevel = Literal["Ingredient", "VTM", "VMP", "AMP"]

_STRENGTH_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|microgram|micrograms|mcg|g|ml|unit|units|%|dose)\b",
    re.IGNORECASE,
)
# AMPs carry a parenthesised marketing-authorisation holder, e.g. "(Pfizer Ltd)".
_AMP_HOLDER_RE = re.compile(
    r"\([^)]*\b(?:Ltd|plc|GmbH|Inc|Pharma|Healthcare|Limited)\b[^)]*\)"
)


def infer_dmd_level(term: str | None) -> DmdLevel | None:
    """Map a dm+d preferred term to Ingredient | VTM | VMP | AMP.

    Returns ``None`` for an empty / whitespace-only / missing term so
    callers can distinguish "level unknown" from "this IS an ingredient".
    """
    if not term or not term.strip():
        return None
    if _AMP_HOLDER_RE.search(term):
        return "AMP"
    if _STRENGTH_RE.search(term):
        return "VMP"
    if term.strip().lower().endswith((" product", " products")):
        return "VTM"
    return "Ingredient"

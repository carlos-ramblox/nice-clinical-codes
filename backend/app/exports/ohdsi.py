"""OHDSI concept-set JSON export.

Emits the JSON shape consumed by ATLAS, ``circe-be``, and DARWIN-EU
``CodelistGenerator``. Codes without an OMOP ``concept_id`` go into a
parallel ``unmapped`` array; ``uncertain`` decisions are withheld from
both arrays since the review gate has not placed them in or out.

Refs: OHDSI Concept Set Specification (TAB), circe-be ConceptSetItem,
CirceR conceptSetJson.
"""

from __future__ import annotations

from typing import Any, Iterable

# Internal vocabulary labels -> OMOP CDM vocabulary_id (v5.4).
# Unknown entries pass through verbatim rather than be silently renamed.
_VOCAB_TO_OMOP = {
    "SNOMED CT": "SNOMED",
    "SNOMED": "SNOMED",
    "ICD-10 (WHO)": "ICD10",
    "ICD-10": "ICD10",
    "ICD10": "ICD10",
    "ICD-10-CM": "ICD10CM",
    "ICD10CM": "ICD10CM",
    "OPCS-4": "OPCS4",
    "OPCS4": "OPCS4",
}


def _omop_vocabulary_id(vocab: str) -> str:
    return _VOCAB_TO_OMOP.get(vocab, vocab)


def _resolve_decision(c: dict) -> str:
    """human_decision wins (post-review); fall back to decision; default uncertain."""
    return c.get("human_decision") or c.get("decision") or "uncertain"


def to_ohdsi_concept_set(
    name: str,
    codes: Iterable[dict],
    *,
    concept_set_id: int = 0,
) -> dict[str, Any]:
    """Return ``{"concept_set": <OHDSI JSON>, "unmapped": [...]}``.

    Codes are routed by ``concept_id``: present -> ``items``, missing ->
    ``unmapped``. ``uncertain`` decisions are skipped. ``isExcluded``
    mirrors ``decision == "exclude"``. Descendants default to false;
    ATLAS users opt in via the UI.
    """
    items: list[dict] = []
    unmapped: list[dict] = []

    for c in codes:
        decision = _resolve_decision(c)
        if decision not in ("include", "exclude"):
            continue

        cid = c.get("concept_id")
        if cid is None:
            unmapped.append({
                "code": c.get("code", ""),
                "vocabulary": c.get("vocabulary", ""),
                "term": c.get("term", ""),
                "decision": decision,
            })
            continue

        items.append({
            "concept": {
                "CONCEPT_ID": int(cid),
                "VOCABULARY_ID": _omop_vocabulary_id(c.get("vocabulary", "")),
                "CONCEPT_CODE": str(c.get("code", "")),
                "CONCEPT_NAME": c.get("term", ""),
            },
            "isExcluded": decision == "exclude",
            "includeDescendants": False,
            "includeMapped": False,
        })

    return {
        "concept_set": {
            "id": int(concept_set_id),
            "name": name,
            "expression": {"items": items},
        },
        "unmapped": unmapped,
    }

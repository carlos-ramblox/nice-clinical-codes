"""
Vocabulary matching helpers shared by the merger and the output assembler.

Both nodes apply the same "if every parsed condition pins one coding
system, drop codes whose vocabulary doesn't match" rule. The merger
applies it before the source-count cap (so requested-vocab codes don't
get displaced); the output assembler applies it as a belt-and-braces
final pass. Sharing the constant and helper here keeps the two filters
in lockstep — when a new vocabulary alias is added (e.g. an OMOP-style
"ICD10CM"), both filters pick it up at once.
"""
from __future__ import annotations


# Maps the short vocabulary names produced by the query parser to the
# longer vocabulary strings actually emitted by the retrievers and
# stamped on retrieved codes. Aliases are listed for vocabularies the
# retriever stack writes under more than one form (the OMOPHub label
# vs ChromaDB metadata vs OPCS-4 hyphenation).
VOCAB_MATCHES: dict[str, tuple[str, ...]] = {
    "SNOMED": ("SNOMED CT", "SNOMED"),
    "ICD10":  ("ICD-10 (WHO)", "ICD-10", "ICD10", "ICD-10-CM"),
    "OPCS4":  ("OPCS-4", "OPCS4"),
}


def requested_vocab_set(conditions: list[dict]) -> tuple[str, ...] | None:
    """Return the tuple of allowed vocabulary strings to filter on, or
    ``None`` when filtering should not apply.

    Filtering applies only when every parsed condition agrees on the
    same single coding system AND that system is in
    :data:`VOCAB_MATCHES`. Multi-vocab queries, heterogeneous
    queries, and queries with an unknown coding system pass through
    unfiltered (returns ``None``).
    """
    if not conditions:
        return None
    systems_per_condition = [tuple(sorted(c.get("coding_systems") or [])) for c in conditions]
    first = systems_per_condition[0]
    if len(first) != 1:
        return None
    if any(s != first for s in systems_per_condition[1:]):
        return None
    return VOCAB_MATCHES.get(first[0])

"""Comorbidity suggester — terminal node (issue #28).

After the code list is assembled, suggest clinically related comorbidities
from the *included* codes so an analyst can broaden their search without
knowing in advance what to look for. Built as a thin orchestrator over
per-source collectors (LLM clinical reasoning, MedGen artifact, live
PubTator) so additional sources slot in without a refactor (plan D3).

Phase-1 skeleton: the anchor-extraction step is real and logged — so it can
be verified against live pipeline state — but every collector is a stub that
returns no hints yet. The node always returns ``comorbidity_suggestions``
(empty for now) and never raises into the graph (plan D11).
"""

import logging

from app.graph.state import ComorbidityHint, ParsedCondition, ScoredCode

logger = logging.getLogger(__name__)


def _extract_anchor(
    final_code_list: list[ScoredCode],
    parsed_conditions: list[ParsedCondition],
) -> dict:
    """Build the anchor set the collectors reason from.

    Anchors on the PRIMARY conditions only — never on codes that were
    themselves comorbidity inclusions — so suggestions don't drift into
    comorbidities-of-comorbidities (plan D5).

    ``ScoredCode`` carries no link back to the condition that produced it,
    so an included code cannot be attributed to a specific primary
    condition. For P1 the anchor is therefore the primary condition *names*
    plus the *terms* of every included code, kept as separate fields so a
    later phase can refine the code→condition attribution if that proves
    necessary. This function is exactly what the anchor-verification step
    exercises against real pipeline runs.
    """
    primary_names = [
        c["name"]
        for c in parsed_conditions
        if c.get("condition_type") == "primary" and c.get("name")
    ]
    included_terms = [
        c["term"]
        for c in final_code_list
        if c.get("decision") == "include" and c.get("term")
    ]
    return {"primary_names": primary_names, "included_terms": included_terms}


# --- per-source collectors (P1 stubs; filled in later phases) ---

def _llm_suggestions(anchor: dict) -> list[ComorbidityHint]:
    """LLM clinical-reasoning source. Stub until Phase 1 step 6."""
    return []


def _medgen_artifact_suggestions(anchor: dict) -> list[ComorbidityHint]:
    """Tier 1 MedGen artifact lookup. Stub until Phase 2."""
    return []


def _pubtator_live_suggestions(anchor: dict) -> list[ComorbidityHint]:
    """Tier 2 live PubTator3 enrichment. Stub until Phase 3."""
    return []


def _merge_and_rank(per_source: list[list[ComorbidityHint]]) -> list[ComorbidityHint]:
    """Dedup across sources + against parsed conditions, tier-rank, cap.

    Stub until Phase 1 step 7 — returns nothing while the collectors are
    stubs.
    """
    return []


def suggest_comorbidities(state: dict) -> dict:
    """LangGraph terminal node: emit comorbidity suggestions.

    Always returns ``comorbidity_suggestions`` (empty in the skeleton) and
    never raises into the pipeline — any failure is logged and degrades to
    an empty list so a suggestion problem can't sink an otherwise-good
    search (plan D11).
    """
    final_code_list = state.get("final_code_list", [])
    parsed_conditions = state.get("parsed_conditions", [])

    # No inclusions → nothing to suggest from (#28 acceptance criteria).
    included = [c for c in final_code_list if c.get("decision") == "include"]
    if not included:
        return {"comorbidity_suggestions": []}

    try:
        anchor = _extract_anchor(final_code_list, parsed_conditions)
        logger.info(
            "Comorbidity anchor: %d primary condition(s) %s, %d included term(s)",
            len(anchor["primary_names"]),
            anchor["primary_names"],
            len(anchor["included_terms"]),
        )

        per_source = [
            _llm_suggestions(anchor),
            _medgen_artifact_suggestions(anchor),
            _pubtator_live_suggestions(anchor),
        ]
        suggestions = _merge_and_rank(per_source)
    except Exception as exc:
        logger.error("Comorbidity suggester failed: %s", exc)
        return {"comorbidity_suggestions": []}

    logger.info("Comorbidity suggester: %d suggestion(s)", len(suggestions))
    return {"comorbidity_suggestions": suggestions}

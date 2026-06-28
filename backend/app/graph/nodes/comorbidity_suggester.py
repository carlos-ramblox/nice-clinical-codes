"""Comorbidity suggester — terminal node (issue #28).

After the code list is assembled, suggest clinically related comorbidities
from the *included* codes so an analyst can broaden their search without
knowing in advance what to look for. Built as a thin orchestrator over
per-source collectors (LLM clinical reasoning, MedGen artifact, live
PubTator) so additional sources slot in without a refactor (plan D3).

Phase 1: the LLM source is live — Claude suggests comorbidities of the
PRIMARY condition(s), merged/ranked/deduped and capped. The MedGen
(Tier 1) and live-PubTator (Tier 2) collectors are still stubs that
return ``[]`` until Phases 2/3. The node always returns
``comorbidity_suggestions`` and never raises into the graph (plan D11).
"""

import logging

from pydantic import BaseModel, Field
from langchain_anthropic import ChatAnthropic

from app.config import ANTHROPIC_API_KEY, LLM_SCORING_MODEL
from app.graph.state import ComorbidityHint, ParsedCondition, ScoredCode

logger = logging.getLogger(__name__)

# Cap on the number of suggestions surfaced (plan: cap 10, tunable).
MAX_SUGGESTIONS = 10

# Only feed this many included terms to the LLM as secondary context — they
# are vocabulary colour, not the anchor, so a long list adds prompt bloat
# without changing the target condition.
_MAX_CONTEXT_TERMS = 40


SYSTEM_PROMPT = """You are a clinical expert helping an analyst broaden a clinical code list.

You are given one or more PRIMARY conditions (the focus of the analyst's search), \
and optionally a sample of the code-list terms already retrieved for those \
conditions as background context.

Suggest distinct clinical conditions that are commonly COMORBID with the \
PRIMARY condition(s) — conditions a clinician would expect to co-occur in the \
same patients, that the analyst might also want to search for.

Rules:
- Anchor strictly on the PRIMARY condition(s). The code-list terms are only \
background colour; never suggest comorbidities of a condition that merely \
appears in that sample.
- Use canonical clinical condition names (e.g. "Atrial fibrillation", \
"Chronic kidney disease"). NEVER return codes.
- Do NOT repeat any of the primary conditions themselves.
- Give each suggestion a confidence (0.0-1.0) and a one-sentence rationale \
naming the clinical link.
- Quality over quantity: suggest only genuinely associated conditions. If \
there are none, return an empty list."""


class _LlmComorbidity(BaseModel):
    """One LLM-proposed comorbidity. Mirrors the LLM-visible slice of
    ``ComorbidityHint`` — ``suggested_by`` is stamped in code, not asked of
    the model."""
    condition_name: str = Field(description="Canonical clinical condition name, never a code")
    rationale: str = Field(description="One sentence naming the clinical link to the primary condition")
    confidence: float = Field(description="0.0-1.0 confidence this is a relevant comorbidity")


class ComorbiditySuggestions(BaseModel):
    """Structured-output wrapper for the LLM call."""
    suggestions: list[_LlmComorbidity] = Field(description="Suggested comorbidities of the primary condition(s)")


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
    (the clean signal — verified 2026-06-28) plus the *terms* of every
    included code as secondary context. Anchor verification confirmed
    ``included_terms`` can carry a comorbidity-derived code, so the LLM
    prompt targets ``primary_names`` and treats ``included_terms`` as
    background only.
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


# --- per-source collectors ---

async def _llm_suggestions(anchor: dict) -> list[ComorbidityHint]:
    """LLM clinical-reasoning source (live since Phase 1 step 6).

    Targets the primary condition(s); the included terms ride along only as
    background context. Degrades to ``[]`` on any failure (missing key,
    API error) so a suggestion problem never sinks the search.
    """
    primary_names = anchor.get("primary_names", [])
    if not primary_names:
        logger.debug("Comorbidity LLM: no primary conditions to anchor on; skipping")
        return []
    if not ANTHROPIC_API_KEY:
        logger.warning("Comorbidity LLM: ANTHROPIC_API_KEY not set; skipping")
        return []

    context_terms = anchor.get("included_terms", [])[:_MAX_CONTEXT_TERMS]
    user_message = (
        f"<primary_conditions>\n{chr(10).join(primary_names)}\n</primary_conditions>\n\n"
        f"<codelist_terms_context>\n{chr(10).join(context_terms)}\n</codelist_terms_context>\n\n"
        f"Suggest up to {MAX_SUGGESTIONS} conditions commonly comorbid with the "
        f"primary condition(s) above."
    )

    try:
        llm = ChatAnthropic(
            model=LLM_SCORING_MODEL,
            api_key=ANTHROPIC_API_KEY,
            max_tokens=2048,
            temperature=0,
        )
        structured_llm = llm.with_structured_output(ComorbiditySuggestions)
        result = await structured_llm.ainvoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ])
    except Exception as exc:
        logger.error("Comorbidity LLM source failed: %s", exc)
        return []

    return [
        ComorbidityHint(
            condition_name=s.condition_name,
            rationale=s.rationale,
            confidence=s.confidence,
            suggested_by=["LLM"],
        )
        for s in result.suggestions
    ]


def _medgen_artifact_suggestions(anchor: dict) -> list[ComorbidityHint]:
    """Tier 1 MedGen artifact lookup. Stub until Phase 2."""
    return []


def _pubtator_live_suggestions(anchor: dict) -> list[ComorbidityHint]:
    """Tier 2 live PubTator3 enrichment. Stub until Phase 3."""
    return []


# --- merge / rank ---

def _normalize(name: str) -> str:
    """Normalized dedup key for a condition name."""
    return " ".join(name.lower().split())


def _tier(hint: ComorbidityHint) -> int:
    """Ranking tier (lower = higher priority): agreed-by-both → MedGen-only
    → LLM-only. Avoids a cross-source confidence-currency mismatch (plan)."""
    by = set(hint.get("suggested_by", []))
    if "LLM" in by and "MedGen" in by:
        return 0
    if "MedGen" in by:
        return 1
    return 2


def _merge_and_rank(
    per_source: list[list[ComorbidityHint]],
    parsed_conditions: list[ParsedCondition],
) -> list[ComorbidityHint]:
    """Dedup across sources + against parsed conditions, tier-rank, cap.

    - Dedup key: ``cui`` when present, else the normalized condition name.
      A condition surfacing from >1 source collapses to one row whose
      ``suggested_by`` is the union (the D8 ``["LLM","MedGen"]`` case),
      keeping the highest confidence seen.
    - Drops anything already in ``parsed_conditions`` (don't re-suggest what
      the analyst already searched).
    - Tier-rank, then by confidence within tier; stable sort; cap.
    """
    # Names the analyst already searched — never re-suggest these.
    parsed_names = {
        _normalize(c["name"]) for c in parsed_conditions if c.get("name")
    }

    merged: dict[str, ComorbidityHint] = {}
    for source in per_source:
        for hint in source:
            name = hint.get("condition_name", "")
            if not name:
                continue
            norm = _normalize(name)
            if norm in parsed_names:
                continue
            key = hint.get("cui") or norm
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(hint)  # copy; we mutate suggested_by/confidence
                continue
            # Same condition from another source: union provenance, keep best confidence.
            existing_by = list(existing.get("suggested_by", []))
            for src in hint.get("suggested_by", []):
                if src not in existing_by:
                    existing_by.append(src)
            existing["suggested_by"] = existing_by
            existing["confidence"] = max(
                existing.get("confidence", 0.0), hint.get("confidence", 0.0)
            )

    ranked = sorted(
        merged.values(),
        key=lambda h: (_tier(h), -h.get("confidence", 0.0)),
    )
    return ranked[:MAX_SUGGESTIONS]


async def suggest_comorbidities(state: dict) -> dict:
    """LangGraph terminal node: emit comorbidity suggestions.

    Always returns ``comorbidity_suggestions`` and never raises into the
    pipeline — any failure is logged and degrades to an empty list so a
    suggestion problem can't sink an otherwise-good search (plan D11).
    Async because the LLM source is awaited (the graph drives the node via
    ``ainvoke``, like the scoring node).
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
            await _llm_suggestions(anchor),
            _medgen_artifact_suggestions(anchor),
            _pubtator_live_suggestions(anchor),
        ]
        suggestions = _merge_and_rank(per_source, parsed_conditions)
    except Exception as exc:
        logger.error("Comorbidity suggester failed: %s", exc)
        return {"comorbidity_suggestions": []}

    logger.info("Comorbidity suggester: %d suggestion(s)", len(suggestions))
    return {"comorbidity_suggestions": suggestions}

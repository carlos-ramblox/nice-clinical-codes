import logging

from pydantic import BaseModel, Field
from langchain_anthropic import ChatAnthropic

from app.config import ANTHROPIC_API_KEY, LLM_SCORING_MODEL
from app.graph.state import ComorbidityHint, ParsedCondition, ScoredCode

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 10

# cap: long term lists add prompt bloat without changing the anchor condition
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


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _LlmComorbidity(BaseModel):
    # suggested_by is stamped in code after the call, not asked of the model
    condition_name: str = Field(min_length=1, description="Canonical clinical condition name, never a code")
    rationale: str = Field(description="One sentence naming the clinical link to the primary condition")
    confidence: float = Field(ge=0.0, le=1.0, description="0.0-1.0 confidence this is a relevant comorbidity")


class ComorbiditySuggestions(BaseModel):
    suggestions: list[_LlmComorbidity] = Field(description="Suggested comorbidities of the primary condition(s)")


def _extract_anchor(
    final_code_list: list[ScoredCode],
    parsed_conditions: list[ParsedCondition],
) -> dict:
    # primary names only — avoids comorbidities-of-comorbidities drift
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


async def _llm_suggestions(anchor: dict) -> list[ComorbidityHint]:
    primary_names = anchor.get("primary_names", [])
    if not primary_names:
        logger.debug("Comorbidity LLM: no primary conditions to anchor on; skipping")
        return []
    if not ANTHROPIC_API_KEY:
        logger.warning("Comorbidity LLM: ANTHROPIC_API_KEY not set; skipping")
        return []

    context_terms = anchor.get("included_terms", [])[:_MAX_CONTEXT_TERMS]
    primary_block = "\n".join(_xml_escape(n) for n in primary_names)
    context_block = "\n".join(_xml_escape(t) for t in context_terms)
    user_message = (
        f"<primary_conditions>\n{primary_block}\n</primary_conditions>\n\n"
        f"<codelist_terms_context>\n{context_block}\n</codelist_terms_context>\n\n"
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


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


def _tier(hint: ComorbidityHint) -> int:
    # lower = higher priority; avoids direct LLM-vs-MedGen confidence comparison
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
    parsed_names = {
        _normalize(c["name"]) for c in parsed_conditions if c.get("name")
    }

    merged: dict[str, ComorbidityHint] = {}
    for source in per_source:
        for hint in source:
            name = hint.get("condition_name", "")
            if not name.strip():
                continue
            norm = _normalize(name)
            if norm in parsed_names:
                continue
            key = hint.get("cui") or norm
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(hint)
                continue
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
    # never raises: a comorbidity failure must not sink an otherwise-good search
    final_code_list = state.get("final_code_list", [])
    parsed_conditions = state.get("parsed_conditions", [])

    if not any(c.get("decision") == "include" for c in final_code_list):
        return {"comorbidity_suggestions": []}

    try:
        anchor = _extract_anchor(final_code_list, parsed_conditions)
        logger.info(
            "Comorbidity anchor: %d primary condition(s) %s, %d included term(s)",
            len(anchor["primary_names"]),
            anchor["primary_names"],
            len(anchor["included_terms"]),
        )

        per_source = [await _llm_suggestions(anchor)]
        suggestions = _merge_and_rank(per_source, parsed_conditions)
    except Exception as exc:
        logger.error("Comorbidity suggester failed: %s", exc)
        return {"comorbidity_suggestions": []}

    logger.info("Comorbidity suggester: %d suggestion(s)", len(suggestions))
    return {"comorbidity_suggestions": suggestions}

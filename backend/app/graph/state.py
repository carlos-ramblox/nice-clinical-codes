"""Pipeline state that flows through all LangGraph nodes."""

from typing import TypedDict, Annotated
from operator import add


class ParsedCondition(TypedDict):
    name: str
    condition_type: str  # "primary" or "comorbidity"
    coding_systems: list[str]
    domain: str  # "Condition", "Drug", or "Procedure"


class RetrievedCode(TypedDict):
    code: str
    term: str
    vocabulary: str  # "SNOMED CT", "ICD-10"
    source: str  # "OMOPHub", "QOF", "OpenCodelists", "ChromaDB"
    domain: str  # "Condition", "Drug", "Procedure"
    similarity_score: float | None
    usage_frequency: int | None


class ScoredCode(TypedDict):
    code: str
    term: str
    vocabulary: str
    decision: str  # "include", "exclude", "uncertain"
    confidence: float
    rationale: str
    sources: list[str]
    classifier_score: float | None
    llm_score: float | None
    usage_frequency: int | None


class ProvenanceRecord(TypedDict):
    code: str
    source: str
    source_url: str | None
    retrieved_at: str
    enrichment_path: str | None  # e.g. "UMLS:RN (narrower) from CUI:C0011849"


class PipelineState(TypedDict):
    # Input
    raw_query: str

    # Query understanding
    parsed_conditions: list[ParsedCondition]

    # Retrieval — reducer merges parallel results
    retrieved_codes: Annotated[list[RetrievedCode], add]

    # Enrichment
    enriched_codes: list[RetrievedCode]

    # Scoring
    scored_codes: list[ScoredCode]
    ambiguous_codes: list[ScoredCode]  # decision == "uncertain"

    # Output
    final_code_list: list[ScoredCode]
    provenance_trail: list[ProvenanceRecord]
    summary: dict  # {total, included, excluded, uncertain, sources_queried}

    # Metadata
    sources_queried: Annotated[list[str], add]
    errors: Annotated[list[str], add]

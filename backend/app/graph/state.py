"""Pipeline state that flows through all LangGraph nodes."""

from typing import TypedDict, Annotated
from operator import add


class ParsedCondition(TypedDict):
    name: str
    condition_type: str  # "primary" or "comorbidity"
    coding_systems: list[str]
    domain: str  # "Condition", "Drug", or "Procedure"
    # Bennett 2023 mode 3 (study intent). Free-text terms the scoring
    # step uses to scope the codelist: any code whose meaning falls
    # under an exclude term must be marked decision="exclude" (T29).
    # Both default to [] so legacy callers and the empty-criteria path
    # are byte-identical to the pre-T29 shape.
    include_criteria: list[str]
    exclude_criteria: list[str]


class RetrievedCode(TypedDict):
    code: str
    term: str
    vocabulary: str  # canonical names from config.OMOPHUB_VOCABULARIES:
                     # "SNOMED CT", "ICD-10 (WHO)", "OPCS-4" (also "UMLS"
                     # for codes added by the enrichment node)
    source: str  # "OMOPHub", "QOF", "OpenCodelists", "ChromaDB"
    domain: str  # "Condition", "Drug", "Procedure"
    similarity_score: float | None
    # OpenCodeCounts-derived fields (T31). usage_frequency is the
    # most-recent annual count from NHS Digital, or None when the code
    # is absent from the dataset OR when the count was withheld under
    # the 1-4 privacy rule. usage_status disambiguates:
    #   "counted"           - usage_frequency is a real number
    #   "withheld_below_5"  - count exists but suppressed by NHS Digital
    #   "not_in_dataset"    - no row for this code at all
    # usage_source carries the human-readable attribution string for
    # the column-header tooltip. Populated by the usage_annotator node
    # after de-dup; retrievers leave these as None.
    usage_frequency: int | None
    usage_status: str | None
    usage_source: str | None
    usage_setting: str | None  # "primary_care" | "secondary_care_hes"
    # 1-based rank within the source retriever's native ordering.
    # Currently only ChromaDB populates this (per sub-query, after T25);
    # other retrievers do not yet emit a rank because their rank fields
    # would be rowid-order (QOF, OpenCodelists) and therefore not
    # relevance-meaningful — see _planning/T01_rrf_diagnostic.md.
    # 0 means "no rank assigned"; consumers must treat 0 as absent.
    # Reserved here so a future re-introduction of the rank-fusion
    # merger (deferred T01, blocked on T23/T24) does not need to
    # change the typed state shape again.
    rank: int


class EnrichedCode(TypedDict):
    code: str
    term: str
    vocabulary: str
    source: str  # first source that returned this code
    sources: list[str]  # all sources that returned this code
    source_count: int
    domain: str
    similarity_score: float | None
    usage_frequency: int | None
    usage_status: str | None
    usage_source: str | None
    usage_setting: str | None


class ScoredCode(TypedDict):
    code: str
    term: str
    vocabulary: str
    decision: str  # "include", "exclude", "uncertain"
    confidence: float
    rationale: str
    sources: list[str]
    usage_frequency: int | None
    usage_status: str | None
    usage_source: str | None
    usage_setting: str | None


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
    # Vocabulary constraints extracted from the query string. Recognised
    # values are "SNOMED", "ICD10", "OPCS4" (the same set the
    # Condition.coding_systems Literal accepts). Empty when the query
    # doesn't pin a vocabulary; non-empty values flow through to
    # retriever fan-out and to output filtering.
    vocabulary_cues: list[str]

    # Structured study-intent criteria supplied at the request boundary
    # (T29). When non-empty, query_parser_node applies them to every
    # parsed condition and skips natural-language extraction for that
    # condition — the structured input wins. Empty defaults preserve
    # pre-T29 behaviour exactly.
    request_include_criteria: list[str]
    request_exclude_criteria: list[str]

    # Retrieval — reducer merges parallel results
    retrieved_codes: Annotated[list[RetrievedCode], add]

    # Enrichment
    enriched_codes: list[EnrichedCode]

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

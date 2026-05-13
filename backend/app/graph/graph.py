"""LangGraph pipeline: wires all nodes into a StateGraph."""

import logging
from typing import Callable

from langgraph.graph import StateGraph, START, END

from app.config import OMOPHUB_VOCABULARIES
from app.graph.state import PipelineState
from app.graph.nodes.query_parser import parse_query
from app.graph.nodes.omophub_retriever import search_omophub, omophub_to_retrieved_codes
from app.graph.nodes.chroma_retriever import retrieve_from_chromadb
from app.graph.nodes.qof_retriever import retrieve_from_qof
from app.graph.nodes.opencodelists_retriever import retrieve_from_opencodelists
from app.graph.nodes.dmd_retriever import retrieve_from_dmd
from app.graph.nodes.bnf_retriever import retrieve_from_bnf
from app.graph.nodes.result_merger import merge_and_dedup
from app.graph.nodes.concept_id_enricher import enrich_concept_ids
from app.graph.nodes.usage_annotator import annotate_usage
from app.graph.nodes.umls_enrichment_node import enrich_with_umls
from app.graph.nodes.llm_reasoning import score_codes
from app.graph.nodes.hierarchy_expander import expand_hierarchy
from app.graph.nodes.output_assembly import assemble_output

logger = logging.getLogger(__name__)


# --- Node wrappers ---

def query_parser_node(state: dict) -> dict:
    """Parse raw query into structured conditions.

    Reads optional ``request_include_criteria`` / ``request_exclude_criteria``
    from state (set by ``run_pipeline``) and forwards them to
    ``parse_query`` so request-level criteria override the LLM's own
    natural-language extraction (T29). When the lists are empty/absent,
    behaviour is identical to the pre-T29 path.
    """
    inc = state.get("request_include_criteria") or None
    exc = state.get("request_exclude_criteria") or None
    desc = state.get("request_include_descendants")
    result = parse_query(
        state["raw_query"],
        request_include_criteria=inc,
        request_exclude_criteria=exc,
        request_include_descendants=desc,
    )
    return {
        "parsed_conditions": result["conditions"],
        "vocabulary_cues": result.get("vocabulary_cues", []),
    }


def omophub_retriever_node(state: dict) -> dict:
    """Search OMOPHub for each parsed condition."""
    conditions = state.get("parsed_conditions", [])
    all_codes = []

    for condition in conditions:
        name = condition.get("name", "")
        if not name:
            continue

        systems = condition.get("coding_systems", ["SNOMED", "ICD10"])
        # Filter the canonical OMOPHub vocab map to only the systems this
        # condition is constrained to. Single source of truth lives in
        # config.OMOPHUB_VOCABULARIES.
        vocabs = {k: OMOPHUB_VOCABULARIES[k] for k in systems if k in OMOPHUB_VOCABULARIES}

        df = search_omophub(name, vocabularies=vocabs, page_size=20)
        codes = omophub_to_retrieved_codes(df)
        all_codes.extend(codes)

    return {"retrieved_codes": all_codes, "sources_queried": ["OMOPHub"]}


# --- Graph definition ---

# Retriever name → (node id, node callable). Used to wire retrievers in
# build_graph and to express disabled_retrievers as plain strings.
_RETRIEVERS: dict[str, tuple[str, Callable]] = {
    "omophub":       ("omophub_retriever",       omophub_retriever_node),
    "chroma":        ("chroma_retriever",        retrieve_from_chromadb),
    "qof":           ("qof_retriever",           retrieve_from_qof),
    "opencodelists": ("opencodelists_retriever", retrieve_from_opencodelists),
    "dmd":           ("dmd_retriever",           retrieve_from_dmd),
    "bnf":           ("bnf_retriever",           retrieve_from_bnf),
}

# Public alias for callers that only need the set of retriever names
# (e.g. routes.py's disable-all guard). Lets _RETRIEVERS stay private.
RETRIEVER_NAMES: frozenset[str] = frozenset(_RETRIEVERS)


def build_graph(disabled_retrievers: set[str] | None = None) -> StateGraph:
    """Build and return the compiled LangGraph pipeline.

    Parameters
    ----------
    disabled_retrievers
        Optional set of retriever names to skip. Recognised values are the
        keys of ``_RETRIEVERS`` (``"omophub"``, ``"chroma"``, ``"qof"``,
        ``"opencodelists"``). The named retrievers are not added to the
        graph and are not wired to the merger; the rest of the pipeline
        is unchanged.

        The intended use case is the cold-start evaluation benchmark
        where ``{"opencodelists"}`` is passed so the retriever cannot
        surface published lists that overlap with the reference.
    """
    disabled = set(disabled_retrievers or ())
    unknown = disabled - set(_RETRIEVERS)
    if unknown:
        raise ValueError(f"Unknown retriever name(s) in disabled_retrievers: {sorted(unknown)}")

    active_retrievers = [name for name in _RETRIEVERS if name not in disabled]
    if not active_retrievers:
        raise ValueError("Cannot disable all retrievers; the merger has no upstream input.")

    graph = StateGraph(PipelineState)

    # always-present nodes
    graph.add_node("query_parser", query_parser_node)
    graph.add_node("result_merger", merge_and_dedup)
    graph.add_node("concept_id_enricher", enrich_concept_ids)
    graph.add_node("usage_annotator", annotate_usage)
    graph.add_node("umls_enrichment", enrich_with_umls)
    graph.add_node("llm_reasoning", score_codes)
    graph.add_node("hierarchy_expander", expand_hierarchy)
    graph.add_node("output_assembly", assemble_output)

    # active retrievers
    for name in active_retrievers:
        node_id, node_fn = _RETRIEVERS[name]
        graph.add_node(node_id, node_fn)

    # START → query parser
    graph.add_edge(START, "query_parser")

    # query parser → fan-out to active retrievers (parallel)
    # active retrievers → fan-in to result merger
    for name in active_retrievers:
        node_id, _ = _RETRIEVERS[name]
        graph.add_edge("query_parser", node_id)
        graph.add_edge(node_id, "result_merger")

    # sequential: merger → concept_id enricher → usage annotator →
    # UMLS enrichment → reasoning → output → END
    graph.add_edge("result_merger", "concept_id_enricher")
    graph.add_edge("concept_id_enricher", "usage_annotator")
    graph.add_edge("usage_annotator", "umls_enrichment")
    graph.add_edge("umls_enrichment", "llm_reasoning")
    graph.add_edge("llm_reasoning", "hierarchy_expander")
    graph.add_edge("hierarchy_expander", "output_assembly")
    graph.add_edge("output_assembly", END)

    return graph.compile()


# Default compiled graph (all retrievers active) — imported by callers
# that don't need to vary the retriever set.
pipeline = build_graph()


# Memoised compiled graphs keyed by frozenset of disabled retrievers.
# Construction is cheap but not free, so we avoid rebuilding on every
# request. The cache is small (at most one entry per subset of the
# active retrievers) so unbounded growth is not a concern.
_GRAPH_CACHE: dict[frozenset[str], object] = {frozenset(): pipeline}


def _get_pipeline(disabled_retrievers: set[str] | None = None):
    key = frozenset(disabled_retrievers or ())
    if key not in _GRAPH_CACHE:
        _GRAPH_CACHE[key] = build_graph(disabled_retrievers=set(key))
    return _GRAPH_CACHE[key]


async def run_pipeline(
    query: str,
    disabled_retrievers: set[str] | None = None,
    *,
    include_criteria: list[str] | None = None,
    exclude_criteria: list[str] | None = None,
    include_descendants: bool | None = None,
) -> dict:
    """Run the full pipeline with a raw query string.

    ``disabled_retrievers`` is forwarded to ``build_graph`` (via a
    memoised cache). When non-empty, the named retrievers are skipped —
    use this for cold-start evaluation runs where the OpenCodelists
    retriever overlaps with the reference set.

    ``include_criteria`` / ``exclude_criteria`` (T29) are the request-level
    structured study-intent overrides. Non-empty values are passed to the
    parser node via state and override any natural-language criteria the
    parser would have extracted. Empty (the default) preserves pre-T29
    behaviour exactly.

    The graph is invoked via ``ainvoke`` because the LLM-scoring node
    is async (it gathers per-batch ``ainvoke`` calls in parallel).
    LangGraph runs the remaining sync nodes in its own thread pool, so
    the FastAPI handler can simply ``await`` this without an extra
    ``asyncio.to_thread`` shim.
    """
    if disabled_retrievers:
        logger.info("Running pipeline (disabled retrievers: %s) for: %s", sorted(disabled_retrievers), query)
    else:
        logger.info("Running pipeline for: %s", query)
    pipe = _get_pipeline(disabled_retrievers)
    initial: dict = {"raw_query": query}
    if include_criteria:
        initial["request_include_criteria"] = list(include_criteria)
    if exclude_criteria:
        initial["request_exclude_criteria"] = list(exclude_criteria)
    if include_descendants is not None:
        initial["request_include_descendants"] = bool(include_descendants)
    result = await pipe.ainvoke(initial)
    logger.info("Pipeline complete: %d codes in final list", len(result.get("final_code_list", [])))
    return result

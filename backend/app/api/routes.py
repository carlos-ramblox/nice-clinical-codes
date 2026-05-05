import asyncio
import csv
import io
import logging
import time
import uuid
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

_COLD_START_DESCRIPTION = (
    "When true, disables the OpenCodelists retriever for this request. "
    "Use for evaluation runs whose reference list comes from "
    "OpenCodelists itself, so the retriever cannot surface the "
    "reference and bias recall upward."
)

# Per-retriever opt-in disable flags. Production callers leave them at the
# default (False); the per-retriever ablation runner (see
# backend/app/evaluation/run_ablation.py) sets them to isolate one
# retriever at a time. Production behaviour is unchanged.
_DISABLE_OMOPHUB_DESCRIPTION = (
    "Evaluation-only flag. When true, disables the OMOPHub retriever for "
    "this request; used by the per-retriever ablation."
)
_DISABLE_QOF_DESCRIPTION = (
    "Evaluation-only flag. When true, disables the QOF Business Rules "
    "retriever for this request; used by the per-retriever ablation."
)
_DISABLE_CHROMA_DESCRIPTION = (
    "Evaluation-only flag. When true, disables the ChromaDB semantic "
    "retriever for this request; used by the per-retriever ablation."
)

from app.graph.graph import run_pipeline
from app.evaluation.evaluator import run_evaluation
from app.baseline.llm_client import run_baseline
from app.api import _search_cache
from app.exports.ohdsi import to_ohdsi_concept_set


def _disabled_retrievers(
    cold_start: bool,
    disable_omophub: bool,
    disable_qof: bool,
    disable_chroma: bool,
) -> set[str] | None:
    """Translate the four opt-in retriever-disable flags into the
    ``disabled_retrievers`` set that ``run_pipeline`` accepts.

    Returns ``None`` (rather than an empty set) when no flag is set, so
    the cached default-graph branch in ``_get_pipeline`` is hit. Raises
    HTTP 400 when all four flags are set — without this, ``build_graph``
    raises ``ValueError`` deep in the pipeline and the route's catch-all
    surfaces it as an opaque 500.
    """
    disabled: set[str] = set()
    if cold_start:
        disabled.add("opencodelists")
    if disable_omophub:
        disabled.add("omophub")
    if disable_qof:
        disabled.add("qof")
    if disable_chroma:
        disabled.add("chroma")
    if len(disabled) >= 4:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot disable all retrievers; the merger has no upstream input. "
                "Leave at least one of cold_start / disable_omophub / disable_qof / "
                "disable_chroma at the default."
            ),
        )
    return disabled or None

logger = logging.getLogger(__name__)

router = APIRouter()



# Request / Response schemas

class SearchRequest(BaseModel):
    query: str = Field(
        ...,
        description="Clinical condition query, e.g. 'type 2 diabetes with hypertension'",
        min_length=2,
        max_length=500,
    )
    # T29 — structured study-intent criteria. When non-empty, these
    # OVERRIDE any "excluding X" / "but not X" phrases the parser would
    # have extracted from the query string. Empty defaults preserve
    # pre-T29 request and signature behaviour exactly. Per-item cap is
    # defence-in-depth against an over-long single criterion bloating
    # the scoring prompt or the signature payload — 100 chars is well
    # above any realistic clinical-token length.
    inclusions: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(
        default_factory=list,
        max_length=10,
        description='Free-text inclusion phrases scoping the codelist.',
    )
    exclusions: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(
        default_factory=list,
        max_length=10,
        description='Free-text exclusion phrases (Bennett 2023 mode 3 carve-outs).',
    )


class CodeResult(BaseModel):
    code: str
    term: str
    vocabulary: str
    decision: str  # include, exclude, uncertain
    confidence: float
    rationale: str
    sources: list[str]
    # OpenCodeCounts-derived fields (T31). usage_frequency stays None
    # both when the code is missing from NHS Digital's published set
    # and when the count was withheld under the 1-4 privacy rule;
    # usage_status disambiguates so the UI can render distinct hints
    # ("—" vs "<5"). usage_source is the human-readable attribution
    # used in the column-header tooltip. usage_setting is the
    # machine-readable equivalent ("primary_care" / "secondary_care_hes")
    # the UI uses to pick the GP / HES badge — using it directly
    # rather than substring-matching usage_source means a future
    # rename of the attribution string can't silently break the badge.
    usage_frequency: int | None = None
    usage_status: str | None = None
    usage_source: str | None = None
    usage_setting: str | None = None
    concept_id: int | None = None


class SearchResponse(BaseModel):
    search_id: str
    query: str
    conditions_parsed: list[dict]
    results: list[CodeResult]
    summary: dict
    provenance_trail: list[dict]
    elapsed_seconds: float


# Endpoints

@router.post("/search", response_model=SearchResponse)
async def search_codes(
    request: SearchRequest,
    cold_start: bool = Query(False, description=_COLD_START_DESCRIPTION),
    disable_omophub: bool = Query(False, description=_DISABLE_OMOPHUB_DESCRIPTION),
    disable_qof: bool = Query(False, description=_DISABLE_QOF_DESCRIPTION),
    disable_chroma: bool = Query(False, description=_DISABLE_CHROMA_DESCRIPTION),
):
    """Search for clinical codes matching a condition query.

    Query parameters
    ----------------
    cold_start : bool, default False
        When ``True``, the OpenCodelists retriever is disabled for this
        request. The other three retrievers (OMOPHub, ChromaDB, QOF) and
        UMLS enrichment remain active. Intended for evaluation runs that
        compare against an OpenCodelists-derived reference list, where
        leaving the retriever live would bias recall upward by surfacing
        the very list the run is meant to compare against.
    disable_omophub, disable_qof, disable_chroma : bool, default False
        Evaluation-only opt-in flags that disable the named retriever for
        this request. Mirror ``cold_start`` so the per-retriever ablation
        runner can isolate one retriever at a time. Production callers
        leave these at the default; the merger requires at least one
        active retriever, so disabling all four is rejected upstream.

    Example::

        curl -X POST 'https://clinicalcodes.uk/api/search?cold_start=true' \\
             -H 'Content-Type: application/json' \\
             -d '{"query": "type 2 diabetes"}'
    """
    t0 = time.time()
    disabled = _disabled_retrievers(cold_start, disable_omophub, disable_qof, disable_chroma)

    try:
        result = await run_pipeline(
            request.query,
            disabled,
            include_criteria=request.inclusions,
            exclude_criteria=request.exclusions,
        )
    except Exception as exc:
        logger.error("Pipeline failed: %s", exc)
        raise HTTPException(status_code=500, detail="Pipeline processing failed")

    elapsed = round(time.time() - t0, 2)
    final_codes = result.get("final_code_list", [])

    search_id = uuid.uuid4().hex[:12]
    _search_cache.put(
        search_id,
        request.query,
        final_codes,
        include_criteria=request.inclusions,
        exclude_criteria=request.exclusions,
    )

    return SearchResponse(
        search_id=search_id,
        query=request.query,
        conditions_parsed=result.get("parsed_conditions", []),
        results=[
            CodeResult(
                code=c["code"],
                term=c["term"],
                vocabulary=c["vocabulary"],
                decision=c["decision"],
                confidence=c["confidence"],
                rationale=c["rationale"],
                sources=c.get("sources", []),
                usage_frequency=c.get("usage_frequency"),
                usage_status=c.get("usage_status"),
                usage_source=c.get("usage_source"),
                usage_setting=c.get("usage_setting"),
                concept_id=c.get("concept_id"),
            )
            for c in final_codes
        ],
        summary=result.get("summary", {}),
        provenance_trail=result.get("provenance_trail", []),
        elapsed_seconds=elapsed,
    )


@router.get("/export/{search_id}")
async def export_codes(search_id: str, output_format: str = "csv"):
    """Export a code list as CSV, Excel, or OHDSI concept-set JSON.

    ``output_format=ohdsi`` returns ``{"concept_set": ..., "unmapped": ...}``;
    paste the ``concept_set`` into ATLAS' Concept Set Import dialog.
    ``unmapped`` lists candidates whose OMOP concept_id the corpus
    could not resolve.
    """
    if output_format not in ("csv", "xlsx", "ohdsi"):
        raise HTTPException(
            status_code=400,
            detail="output_format must be 'csv', 'xlsx', or 'ohdsi'",
        )

    entry = _search_cache.get(search_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Search result not found")
    codes = entry["codes"]

    if output_format == "ohdsi":
        return to_ohdsi_concept_set(entry.get("query") or "", codes)

    export_fields = [
        "code", "term", "vocabulary", "decision", "confidence", "rationale", "sources",
        # T31: include the OpenCodeCounts fields in exports so a
        # downstream analyst working off the CSV/XLSX gets the same
        # signal as the search-page UI. Empty cells encode "—" / "<5"
        # / a real number per usage_status.
        "usage_frequency", "usage_status", "usage_source",
    ]

    rows = []
    for c in codes:
        row = {f: c.get(f, "") for f in export_fields}
        row["sources"] = ", ".join(row["sources"]) if isinstance(row["sources"], list) else row["sources"]
        rows.append(row)

    if output_format == "xlsx":
        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        df.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=codelist_{search_id}.xlsx"},
        )

    # default: CSV
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=export_fields)
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=codelist_{search_id}.csv"},
    )


class EvaluateRequest(BaseModel):
    test_set: list[dict] = Field(
        ...,
        description="Gold-standard test set in Anna's format: [{Entry_no, Research_question, Codelist, Codelist_terms, Codelist_vocabulary, ...}]",
    )


@router.post("/evaluate")
async def evaluate_codes(
    request: EvaluateRequest,
    cold_start: bool = Query(False, description=_COLD_START_DESCRIPTION),
    disable_omophub: bool = Query(False, description=_DISABLE_OMOPHUB_DESCRIPTION),
    disable_qof: bool = Query(False, description=_DISABLE_QOF_DESCRIPTION),
    disable_chroma: bool = Query(False, description=_DISABLE_CHROMA_DESCRIPTION),
):
    """Run the pipeline on a test set query and evaluate against the gold standard.

    Query parameters
    ----------------
    cold_start : bool, default False
        When ``True``, the OpenCodelists retriever is disabled for this
        evaluation run. Use this when the reference codelist comes from
        OpenCodelists itself, so the retriever cannot surface the
        reference and bias recall upward by construction.
    disable_omophub, disable_qof, disable_chroma : bool, default False
        Evaluation-only opt-in flags that disable the named retriever for
        this request. Mirror ``cold_start`` so the per-retriever ablation
        runner can isolate one retriever at a time.

    Example::

        curl -X POST 'https://clinicalcodes.uk/api/evaluate?cold_start=true' \\
             -H 'Content-Type: application/json' \\
             -d @test_set.json
    """
    test_set = request.test_set
    if not test_set:
        raise HTTPException(status_code=400, detail="test_set cannot be empty")

    query = test_set[0].get("Research_question", "")
    if not query:
        raise HTTPException(status_code=400, detail="No Research_question found in test set")

    t0 = time.time()
    disabled = _disabled_retrievers(cold_start, disable_omophub, disable_qof, disable_chroma)

    try:
        pipeline_result = await run_pipeline(query, disabled)
    except Exception as exc:
        logger.error("Evaluation pipeline failed: %s", exc)
        raise HTTPException(status_code=500, detail="Pipeline processing failed")

    final_codes = pipeline_result.get("final_code_list", [])
    retrieved_codes = pipeline_result.get("retrieved_codes", [])
    enriched_codes = pipeline_result.get("enriched_codes", [])

    eval_result = run_evaluation(test_set, {
        "results": final_codes,
        "retrieved_codes": retrieved_codes,
        "enriched_codes": enriched_codes,
    })
    eval_result["elapsed_seconds"] = round(time.time() - t0, 2)
    eval_result["pipeline_results_count"] = len(final_codes)
    eval_result["scored_codes"] = final_codes
    eval_result["pipeline"] = "rag"
    eval_result["cold_start"] = cold_start

    return eval_result


class BaselineRequest(BaseModel):
    test_set: list[dict] = Field(
        ...,
        description="Same format as /api/evaluate. Runs an LLM-only baseline (no RAG) on Research_question and evaluates against Codelist.",
    )
    model: str = Field(
        default="microsoft/phi-4",
        description="OpenRouter model id, e.g. 'microsoft/phi-4', 'openai/gpt-4o-mini', 'anthropic/claude-3.5-haiku'.",
    )


@router.post("/baseline")
async def baseline_evaluate(request: BaselineRequest):
    """
    Run an LLM-only baseline (no retrieval) against a test set and evaluate
    against the gold-standard codelist. Used to show the uplift the RAG
    pipeline provides over a direct LLM call.
    """
    test_set = request.test_set
    if not test_set:
        raise HTTPException(status_code=400, detail="test_set cannot be empty")

    query = test_set[0].get("Research_question", "")
    if not query:
        raise HTTPException(status_code=400, detail="No Research_question found in test set")

    t0 = time.time()
    try:
        codes = await asyncio.to_thread(run_baseline, query, model=request.model)
    except Exception as exc:
        logger.error("Baseline (%s) pipeline failed: %s", request.model, exc)
        raise HTTPException(status_code=500, detail=f"Baseline failed: {exc}")

    eval_result = run_evaluation(test_set, {"results": codes})
    eval_result["elapsed_seconds"] = round(time.time() - t0, 2)
    eval_result["pipeline"] = f"baseline:{request.model}"
    eval_result["pipeline_results_count"] = len(codes)
    eval_result["scored_codes"] = codes

    return eval_result

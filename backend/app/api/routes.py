from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


# Request / Response schemas

class SearchRequest(BaseModel):
    query: str = Field(
        ...,
        description="Clinical condition query, e.g. 'type 2 diabetes with hypertension'",
        min_length=2,
        max_length=500,
    )
    coding_systems: list[str] = Field(
        default=["SNOMED", "ICD10"],
        description="Coding systems to search: SNOMED, ICD10, or both",
    )


class CodeResult(BaseModel):
    code: str
    term: str
    vocabulary: str
    decision: str  # include, exclude, uncertain
    confidence: float
    rationale: str
    sources: list[str]
    usage_frequency: int | None = None
    classifier_score: float | None = None


class SearchResponse(BaseModel):
    query: str
    conditions_parsed: list[dict]
    results: list[CodeResult]
    summary: dict
    provenance_trail: list[dict]


# Endpoints

@router.post("/search", response_model=SearchResponse)
async def search_codes(request: SearchRequest):
    """Search for clinical codes matching a condition query."""
    # TODO: wire up LangGraph pipeline (NICE-013)
    raise HTTPException(status_code=501, detail="Not implemented yet")


class ReviewRequest(BaseModel):
    search_id: str
    decisions: dict[str, str] = Field(
        ...,
        description="Map of code -> decision (include/exclude) for uncertain codes",
    )


@router.post("/review")
async def review_codes(request: ReviewRequest):
    """Submit human review decisions for uncertain codes."""
    # TODO: human-in-the-loop resume (NICE-033)
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.get("/export/{search_id}")
async def export_codes(search_id: str, output_format: str = "csv"):
    """Export a code list as CSV or Excel."""
    # TODO: implement export (NICE-023)
    raise HTTPException(status_code=501, detail="Not implemented yet")

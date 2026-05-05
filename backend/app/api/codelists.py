"""
HITL codelist endpoints.

A "codelist" is the persistent, versioned, auditable artefact NICE needs:
a draft starts as the AI pipeline's output, a clinician reviews and
optionally overrides each decision, and the approved list carries a
SHA-256 signature and a full audit log so the artefact can be defended.

Auth is required on every endpoint — the reviewer's identity must be
recorded for EU AI Act / GDPR Article 22 compliance on human oversight.
"""

import logging
from typing import Literal, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.api import _search_cache
from app.config import HDR_UK_BASE_URL, HDR_UK_TOP_K_PHENOTYPES
from app.db import code_store, hitl_store
from app.db.code_normalize import normalize_code
from app.exports.ohdsi import to_ohdsi_concept_set
from app.services.phenotype_discovery import (
    compute_overlap,
    discover_phenotypes_ranked,
    fetch_phenotype_codes,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/codelists", tags=["codelists"])


# --- request / response models ---------------------------------------------

class AdoptedPhenotype(BaseModel):
    """One HDR UK phenotype adopted as a citation during discovery (T34b).

    Submitted with the codelist on save; recorded as a
    ``phenotype_adopted`` audit-log event by ``hitl_store.create_codelist``.
    The audit log is the single source of truth -- there is no separate
    adoptions table -- so adoption history shares the existing
    tamper-evidence path used for decision overrides.
    """

    phenotype_id: str = Field(..., min_length=1, max_length=64)
    phenotype_version_id: int | None = Field(
        default=None,
        description=(
            "HDR UK version id captured at adoption time, so the citation "
            "stays pinned to the version the user actually consulted."
        ),
    )
    name: str = Field(..., min_length=1, max_length=300)
    hdruk_url: str = Field(..., min_length=1, max_length=300)
    first_publication: str = Field(default="", max_length=400)


class CreateCodelistRequest(BaseModel):
    search_id: str = Field(..., description="search_id returned by POST /api/search")
    name: str = Field(..., min_length=1, max_length=200)
    adopted_phenotypes: list[AdoptedPhenotype] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "HDR UK phenotypes adopted via the discovery sidebar. Realistic "
            "upper bound is 1-3 per study; cap at 20 for defence in depth."
        ),
    )


class DecisionUpdate(BaseModel):
    id: int
    human_decision: Literal["include", "exclude", "uncertain"]
    override_comment: Optional[str] = None


class ReviewRequest(BaseModel):
    decisions: list[DecisionUpdate]
    action: Literal["approve", "reject"]
    notes: Optional[str] = None


# --- list / read ------------------------------------------------------------

@router.get("")
async def list_codelists(
    status: Optional[str] = None,
    mine: bool = False,
    limit: int = 100,
    user: dict = Depends(get_current_user),
):
    """
    List codelists. status filters by draft|in_review|approved|rejected.
    mine=true restricts to the caller's own drafts. limit caps the result
    set for bounded payloads at demo scale.
    """
    limit = max(1, min(limit, 500))
    user_id = user["id"] if mine else None
    rows = hitl_store.list_codelists(user_id=user_id, status=status)
    return rows[:limit]


@router.get("/{codelist_id}")
async def get_codelist(codelist_id: str, user: dict = Depends(get_current_user)):
    cl = hitl_store.get_codelist(codelist_id)
    if cl is None:
        raise HTTPException(status_code=404, detail="Codelist not found")
    return cl


@router.get("/{codelist_id}/audit")
async def get_audit(codelist_id: str, user: dict = Depends(get_current_user)):
    if hitl_store.get_codelist(codelist_id) is None:
        raise HTTPException(status_code=404, detail="Codelist not found")
    return hitl_store.get_audit(codelist_id)


@router.get("/{codelist_id}/export")
async def export_codelist(
    codelist_id: str,
    format: str = Query("ohdsi", description="Export format. Currently only 'ohdsi'."),
    user: dict = Depends(get_current_user),
):
    """Export a codelist as OHDSI concept-set JSON.

    Decisions carry concept_id pinned at insert time; pre-migration
    rows fall back to the local corpus lookup, never to a guess.
    """
    if format != "ohdsi":
        raise HTTPException(status_code=400, detail="format must be 'ohdsi'")

    cl = hitl_store.get_codelist(codelist_id)
    if cl is None:
        raise HTTPException(status_code=404, detail="Codelist not found")

    enriched: list[dict] = []
    for d in cl.get("decisions") or []:
        cid_val = d.get("concept_id")
        if cid_val is None:
            cid_val = code_store.get_concept_id_for(
                d.get("vocabulary") or "", d.get("code") or "",
            )
        enriched.append({
            "code": d.get("code", ""),
            "term": d.get("term", ""),
            "vocabulary": d.get("vocabulary", ""),
            "human_decision": d.get("human_decision"),
            "decision": d.get("ai_decision"),
            "concept_id": cid_val,
        })

    return to_ohdsi_concept_set(cl.get("name") or cl.get("query") or "", enriched)


# --- HDR UK cross-reference (T35) ------------------------------------------
#
# Post-hoc validation: rank up to 5 HDR UK phenotypes by code-set overlap
# with the saved codelist. The discovery layer (the persona-judge plus
# the HDR UK search) is shared with the discovery-sidebar endpoint via
# the in-process cache in ``app.services.phenotype_discovery`` -- a
# researcher who hits both the sidebar and the cross-reference for the
# same query within five minutes pays for at most one Haiku call across
# the pair. Per-phenotype codelist fetches sit behind a 7-day file
# cache under ``data/cache/hdruk_phenotype_codes/``.

class CrossReferenceRow(BaseModel):
    """One row of the cross-reference panel."""

    phenotype_id: str
    name: str
    hdruk_url: str
    overlap_jaccard: float = Field(description="|A ∩ B| / |A ∪ B|; primary affordance")
    overlap_generated_in_phenotype: float = Field(
        description="Fraction of the user's generated codes that appear in the phenotype",
    )
    overlap_phenotype_in_generated: float = Field(
        description="Fraction of the phenotype's codes that appear in the user's generated list",
    )
    n_generated_codes: int
    n_phenotype_codes: int
    n_intersection: int
    data_sources: list[str] = Field(default_factory=list)
    first_publication: str = ""
    relevance_rationale: str = ""


def _hdruk_detail_url(phenotype_id: str) -> str:
    return f"{HDR_UK_BASE_URL.rstrip('/')}/phenotypes/{phenotype_id}"


def _generated_code_set(codelist: dict) -> set[str]:
    """Return the normalised code set for a saved codelist's *included* codes.

    Excluded / uncertain decisions don't count -- the cross-reference is
    a comparison of the *intended output* set, which is what the user
    will publish.
    """
    out: set[str] = set()
    for d in codelist.get("decisions") or []:
        if (d.get("human_decision") or d.get("ai_decision")) != "include":
            continue
        raw = d.get("code") or ""
        if not raw:
            continue
        out.add(normalize_code(str(raw), d.get("vocabulary", "") or ""))
    out.discard("")
    return out


@router.get("/{codelist_id}/cross-reference", response_model=list[CrossReferenceRow])
async def get_cross_reference(
    codelist_id: str,
    refresh: bool = Query(False, description="If true, bypass the per-phenotype file cache"),
    user: dict = Depends(get_current_user),
):
    """Rank HDR UK phenotypes by overlap with this codelist's included codes.

    Discovery query is the codelist's original ``query`` field, with a
    fall-through to the codelist ``name`` if the query is missing
    (older drafts may not carry one). Empty codelists return an empty
    list -- nothing to overlap against.
    """
    cl = hitl_store.get_codelist(codelist_id)
    if cl is None:
        raise HTTPException(status_code=404, detail="Codelist not found")

    discovery_query = (cl.get("query") or cl.get("name") or "").strip()
    if not discovery_query:
        return []

    generated = _generated_code_set(cl)
    if not generated:
        # Empty codelist: no overlap is meaningful. Return [] rather than
        # rows of all-zeros so the UI hides the panel.
        return []

    ranked = discover_phenotypes_ranked(discovery_query, HDR_UK_TOP_K_PHENOTYPES)
    if not ranked:
        return []

    out: list[CrossReferenceRow] = []
    with requests.Session() as session:
        session.headers.update({"Accept": "application/json"})
        for phenotype, decision in ranked:
            pid = phenotype.get("phenotype_id")
            if not pid:
                continue
            phenotype_codes = fetch_phenotype_codes(session, pid, refresh=refresh)
            if not phenotype_codes:
                continue
            metrics = compute_overlap(generated, phenotype_codes)
            pubs = phenotype.get("publications") or []
            first_pub = ""
            if pubs and isinstance(pubs[0], dict):
                first_pub = (pubs[0].get("details") or "").strip()[:240]
            out.append(CrossReferenceRow(
                phenotype_id=pid,
                name=phenotype.get("name", ""),
                hdruk_url=_hdruk_detail_url(pid),
                overlap_jaccard=float(metrics["overlap_jaccard"]),
                overlap_generated_in_phenotype=float(metrics["overlap_generated_in_phenotype"]),
                overlap_phenotype_in_generated=float(metrics["overlap_phenotype_in_generated"]),
                n_generated_codes=int(metrics["n_generated_codes"]),
                n_phenotype_codes=int(metrics["n_phenotype_codes"]),
                n_intersection=int(metrics["n_intersection"]),
                data_sources=[
                    d.get("name", "") for d in (phenotype.get("data_sources") or [])
                    if d.get("name")
                ],
                first_publication=first_pub,
                relevance_rationale=decision.reason if decision is not None else "",
            ))

    out.sort(key=lambda r: r.overlap_jaccard, reverse=True)
    return out[:5]


# --- create from search result ---------------------------------------------

@router.post("", status_code=201)
async def create_codelist(body: CreateCodelistRequest, user: dict = Depends(get_current_user)):
    """
    Persist a /api/search result as a draft codelist owned by the current user.
    Pulls the codes from the in-memory search cache by search_id.
    """
    entry = _search_cache.get(body.search_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail="Search result not found or expired. Re-run the search.",
        )

    cid = hitl_store.create_codelist(
        name=body.name.strip(),
        query=entry["query"],
        created_by=user["id"],
        decisions=entry["codes"],
        adopted_phenotypes=[a.model_dump() for a in body.adopted_phenotypes],
        # T29 — carry the criteria captured at /api/search time. Empty
        # lists are byte-equivalent to the pre-T29 path; non-empty
        # values participate in signature_hash on approval.
        include_criteria=entry.get("include_criteria") or [],
        exclude_criteria=entry.get("exclude_criteria") or [],
    )
    # log user_id only — names are PII, don't ship them to stdout in prod
    logger.info(
        "codelist %s created by user_id=%d (%d codes, %d adoptions)",
        cid, user["id"], len(entry["codes"]), len(body.adopted_phenotypes),
    )
    return hitl_store.get_codelist(cid)


# --- review ----------------------------------------------------------------

@router.post("/{codelist_id}/review")
async def review_codelist(
    codelist_id: str,
    body: ReviewRequest,
    user: dict = Depends(get_current_user),
):
    """
    Apply reviewer decisions, flip status to approved/rejected, record
    overrides in the audit log and compute a signature hash on approval.
    """
    cl = hitl_store.get_codelist(codelist_id)
    if cl is None:
        raise HTTPException(status_code=404, detail="Codelist not found")
    if cl["status"] not in ("draft", "in_review"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot review a codelist in status '{cl['status']}'",
        )

    # enforce: every override carries a non-empty comment
    decision_by_id = {d["id"]: d for d in cl["decisions"]}
    for update in body.decisions:
        ai = decision_by_id.get(update.id)
        if ai is None:
            raise HTTPException(
                status_code=400,
                detail=f"Decision id {update.id} not part of codelist {codelist_id}",
            )
        if ai["ai_decision"] != update.human_decision:
            if not update.override_comment or len(update.override_comment.strip()) < 5:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Override on code {ai['code']} requires an override_comment "
                        "of at least 5 characters"
                    ),
                )

    result = hitl_store.submit_review(
        cid=codelist_id,
        reviewer_id=user["id"],
        decisions=[d.model_dump() for d in body.decisions],
        action=body.action,
        notes=body.notes,
    )
    return {
        "codelist_id": codelist_id,
        **result,
        "reviewed_by": user["name"],
    }

"""
HITL codelist endpoints.

A "codelist" is the persistent, versioned, auditable artefact NICE needs:
a draft starts as the AI pipeline's output, a clinician reviews and
optionally overrides each decision, and the approved list carries a
SHA-256 signature and a full audit log so the artefact can be defended.

Auth is required on every endpoint — the reviewer's identity must be
recorded for EU AI Act / GDPR Article 22 compliance on human oversight.
"""

import csv
import io
import json
import logging
import zipfile
from typing import Literal, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.api import _search_cache
from app.config import HDR_UK_BASE_URL, HDR_UK_TOP_K_PHENOTYPES
from app.db import code_store, hitl_store
from app.db.code_normalize import normalize_code
from app.exports.ohdsi import to_ohdsi_concept_set
from app.exports.opencodelists import (
    build_provenance,
    group_for_opencodelists,
    slug_for,
)
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


class PrivacyRequest(BaseModel):
    # T32: owner-only opt-out from /gallery. True hides the row from the
    # public surface; False puts it back. Status-agnostic on purpose --
    # an owner can pre-mark a draft private so it never auto-publishes
    # the moment it's approved.
    private: bool


# --- T30 v2 models ---------------------------------------------------------


class AssignReviewersRequest(BaseModel):
    """Payload for ``POST /codelists/{id}/reviewers``. Exactly two
    reviewer ids; the store enforces independence (no self-review)
    and existence."""

    reviewer_ids: list[int] = Field(
        ..., min_length=2, max_length=2,
        description=(
            "Exactly two reviewer user-ids. v1 of T30 caps at n=2 "
            "because Cohen's kappa is the only agreement metric "
            "shipped; n=3 with Fleiss' kappa is a future option."
        ),
    )


class VoteUpdate(BaseModel):
    """One per-reviewer vote on a single decision row."""

    decision_id: int
    vote: Literal["include", "exclude", "uncertain"]
    comment: Optional[str] = None


class ReviewVoteRequest(BaseModel):
    """v2 review payload — discriminates on ``votes`` (vs v1's
    ``decisions``) so the dispatcher in ``review_codelist`` can
    reject mismatched-shape submissions early."""

    votes: list[VoteUpdate] = Field(
        ..., min_length=1,
        description="At least one vote. UPSERTs by (decision_id, reviewer_id).",
    )
    is_final: bool = Field(
        default=False,
        description=(
            "True locks this reviewer's votes and (when both reviewers "
            "have finalised) triggers the auto-disposition: unanimous "
            "→ approved + signature; any disagreement → adjudication."
        ),
    )


class ResolutionUpdate(BaseModel):
    """One consensus resolution. Rationale is the audit anchor for
    *why* this decision was resolved this way."""

    decision_id: int
    final_decision: Literal["include", "exclude", "uncertain"]
    rationale: str = Field(..., min_length=1)


class ConsensusRequest(BaseModel):
    """Payload for ``POST /codelists/{id}/consensus``.

    Two-phase both-ACK design:
    - ``acknowledge=False``: propose resolutions. Logged as a
      ``proposed_consensus`` audit event; status stays adjudication.
    - ``acknowledge=True``: accept the *other* reviewer's most
      recent proposal. Resolutions MUST byte-equal the prior
      proposal — silently changing a resolution while pretending
      to ACK would forge the other reviewer's clinical agreement.
    """

    resolutions: list[ResolutionUpdate] = Field(..., min_length=1)
    acknowledge: bool = False


class RejectRequest(BaseModel):
    """Payload for ``POST /codelists/{id}/reject``. Reason required
    so the audit log records *why* a codelist was rejected, not
    only that it was."""

    reason: str = Field(..., min_length=1)


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


@router.get("/{codelist_id}/export.opencodelists.csv")
async def export_codelist_opencodelists(
    codelist_id: str,
    user: dict = Depends(get_current_user),
):
    """Export an approved two-reviewer codelist in OpenCodelists upload-CSV format.

    Response is ``application/zip`` carrying one ``<slug>.<ocl_slug>.csv``
    per coding system plus a ``<slug>.provenance.json`` with the SHA-256
    signature, reviewer pair, and per-system manifest.

    Gated on ``status='approved'`` AND two-reviewer signature (v2 with
    ≥2 distinct reviewer ids). 422 when no included row maps to an
    OpenCodelists coding system.
    """
    cl = hitl_store.get_codelist(codelist_id)
    if cl is None:
        raise HTTPException(status_code=404, detail="Codelist not found")

    if cl.get("status") != "approved":
        raise HTTPException(
            status_code=409,
            detail=(
                "OpenCodelists export requires an approved codelist; "
                f"this one is in status '{cl.get('status')}'"
            ),
        )
    sig_version = cl.get("signature_version")
    distinct_reviewers = len(set(cl.get("reviewer_ids") or []))
    if sig_version != 2:
        raise HTTPException(
            status_code=409,
            detail=(
                "OpenCodelists export requires two-reviewer Delphi adjudication; "
                "this codelist was approved on the legacy single-reviewer signature. "
                "Re-promote a fresh draft to v2 via the /reviewers route to enable export."
            ),
        )
    if distinct_reviewers < 2:
        # v2 state machine guarantees ≥2 distinct ids; fewer means corruption.
        raise HTTPException(
            status_code=409,
            detail=(
                "OpenCodelists export requires two distinct reviewer ids; "
                f"this codelist has {distinct_reviewers}. "
                "This is a data-integrity error — investigate the codelist's reviewer_ids."
            ),
        )

    groups, dropped = group_for_opencodelists(cl.get("decisions") or [])
    if not groups:
        raise HTTPException(
            status_code=422,
            detail=(
                "No included codes map to an OpenCodelists coding system "
                "(supported: SNOMED CT, ICD-10, OPCS-4, CTV3, Read v2, "
                "BNF, dm+d). Nothing to export."
            ),
        )

    base = slug_for(cl.get("name") or cl.get("query") or "", fallback=codelist_id)
    reviewer_names = hitl_store.get_reviewer_names(cl.get("reviewer_ids") or [])
    provenance = build_provenance(cl, groups, dropped, reviewer_names, base=base)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for ocl_slug, rows in sorted(groups.items()):
            csv_buf = io.StringIO()
            writer = csv.DictWriter(csv_buf, fieldnames=["code", "term"])
            writer.writeheader()
            writer.writerows(rows)
            zf.writestr(f"{base}.{ocl_slug}.csv", csv_buf.getvalue())
        zf.writestr(
            f"{base}.provenance.json",
            json.dumps(provenance, indent=2, sort_keys=True),
        )
    zip_buf.seek(0)

    logger.info(
        "opencodelists export: codelist %s by user_id=%d "
        "(%d codes across %d coding systems, %d dropped)",
        codelist_id, user["id"],
        sum(len(rows) for rows in groups.values()), len(groups), len(dropped),
    )

    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{base}.opencodelists.zip"'
            ),
        },
    )


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
        include_descendants=bool(entry.get("include_descendants")),
    )
    # log user_id only — names are PII, don't ship them to stdout in prod
    logger.info(
        "codelist %s created by user_id=%d (%d codes, %d adoptions)",
        cid, user["id"], len(entry["codes"]), len(body.adopted_phenotypes),
    )
    return hitl_store.get_codelist(cid)


# --- review ----------------------------------------------------------------
#
# /review dispatches on ``signature_version``: v1 codelists (legacy
# single-reviewer, ``reviewer_ids=[]``) take the historical
# ``ReviewRequest`` shape and route to ``submit_review``; v2 codelists
# (``signature_version=2`` set by ``/reviewers``) take the
# ``ReviewVoteRequest`` per-reviewer shape and route to
# ``submit_review_v2``. signature_version is immutable post-creation,
# so the dispatch is one-way: a v1 codelist stays v1, a v2 stays v2.
# Mismatched-shape payloads (v1 shape on v2 codelist or vice-versa)
# return 400 — the discriminator is the request body's top-level
# key (``decisions`` vs ``votes``), validated below before the store
# call.


@router.post("/{codelist_id}/review")
async def review_codelist(
    codelist_id: str,
    body: dict,  # raw dict; we discriminate then re-validate
    user: dict = Depends(get_current_user),
):
    """
    Apply reviewer decisions on a codelist. Dispatches on
    ``signature_version``: v1 (legacy single-reviewer) or v2
    (two-reviewer Delphi).
    """
    cl = hitl_store.get_codelist(codelist_id)
    if cl is None:
        raise HTTPException(status_code=404, detail="Codelist not found")

    sig_version = cl.get("signature_version", 1)
    has_v1_shape = "decisions" in body
    has_v2_shape = "votes" in body

    if sig_version == 1:
        if not has_v1_shape:
            raise HTTPException(
                status_code=400,
                detail=(
                    "this codelist uses single-reviewer review; submit a "
                    "complete review payload with 'decisions' and 'action'"
                ),
            )
        if has_v2_shape:
            raise HTTPException(
                status_code=400,
                detail=(
                    "ambiguous payload: 'votes' (v2) submitted to a v1 "
                    "codelist; use 'decisions' instead"
                ),
            )
        return await _review_v1(codelist_id, body, cl, user)

    # v2 path
    if not has_v2_shape:
        raise HTTPException(
            status_code=400,
            detail=(
                "this codelist uses two-reviewer review; submit per-reviewer "
                "votes via 'votes' and 'is_final'"
            ),
        )
    if has_v1_shape:
        raise HTTPException(
            status_code=400,
            detail=(
                "ambiguous payload: 'decisions' (v1) submitted to a v2 "
                "codelist; use 'votes' instead"
            ),
        )
    return await _review_v2(codelist_id, body, user)


async def _review_v1(
    codelist_id: str,
    body: dict,
    cl: dict,
    user: dict,
) -> dict:
    """Legacy single-reviewer path. Unchanged behaviour from pre-T30."""
    try:
        request = ReviewRequest.model_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if cl["status"] not in ("draft", "in_review"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot review a codelist in status '{cl['status']}'",
        )

    # enforce: every override carries a non-empty comment
    decision_by_id = {d["id"]: d for d in cl["decisions"]}
    for update in request.decisions:
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

    try:
        result = hitl_store.submit_review(
            cid=codelist_id,
            reviewer_id=user["id"],
            decisions=[d.model_dump() for d in request.decisions],
            action=request.action,
            notes=request.notes,
        )
    except hitl_store.ConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot review a codelist in status '{exc.status}'",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Codelist not found")
    except ValueError as exc:
        # submit_review now refuses v2 codelists with ValueError. The
        # dispatcher above should have caught the version mismatch
        # before we got here, but the store-level guard is the
        # authoritative gate; surface its message at 422.
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "codelist_id": codelist_id,
        **result,
        "reviewed_by": user["name"],
    }


async def _review_v2(
    codelist_id: str,
    body: dict,
    user: dict,
) -> dict:
    """v2 per-reviewer voting path. Caller is one of the two
    reviewers assigned via ``POST /reviewers``; UPSERTs each vote
    and (on ``is_final=true`` from both reviewers) auto-transitions
    to approved (unanimous) or adjudication (any disagreement)."""
    try:
        request = ReviewVoteRequest.model_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        result = hitl_store.submit_review_v2(
            cid=codelist_id,
            reviewer_id=user["id"],
            votes=[v.model_dump() for v in request.votes],
            is_final=request.is_final,
        )
    except hitl_store.ConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "voting finalised; submit a counter-proposal via /consensus "
                "to change a vote"
                if exc.reason == "voting_finalised"
                else f"Cannot review a codelist in status '{exc.status}'"
            ),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Codelist not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "codelist_id": codelist_id,
        **result,
        "reviewer": user["name"],
    }


# --- T30 v2 endpoints ------------------------------------------------------


@router.post("/{codelist_id}/reviewers", status_code=200)
async def assign_reviewers(
    codelist_id: str,
    body: AssignReviewersRequest,
    user: dict = Depends(get_current_user),
):
    """Assign exactly two reviewers to a draft codelist; transitions
    it to ``in_review`` and locks ``signature_version=2``.

    Owner-only (creator). Idempotent on draft: re-posting the same
    set is a no-op; re-posting a different set replaces. Any
    non-draft status returns 409.
    """
    try:
        return hitl_store.assign_reviewers(
            cid=codelist_id,
            reviewer_ids=body.reviewer_ids,
            caller_user_id=user["id"],
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Codelist not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except hitl_store.ConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot assign reviewers to a codelist in status '{exc.status}'",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{codelist_id}/consensus")
async def submit_consensus(
    codelist_id: str,
    body: ConsensusRequest,
    user: dict = Depends(get_current_user),
):
    """Both-ACK consensus on an adjudication-state codelist.

    First call (``acknowledge=False``) proposes a set of resolutions
    covering every disputed decision (and optionally re-resolving
    previously-unanimous ones). Second call (``acknowledge=True``)
    by the *other* reviewer accepts the prior proposal — must
    byte-equal — and transitions to approved.
    """
    try:
        return hitl_store.submit_consensus(
            cid=codelist_id,
            reviewer_id=user["id"],
            resolutions=[r.model_dump() for r in body.resolutions],
            acknowledge=body.acknowledge,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Codelist not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except hitl_store.ConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot submit consensus on a codelist in status '{exc.status}'",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{codelist_id}/voting-state")
async def get_voting_state(
    codelist_id: str,
    user: dict = Depends(get_current_user),
):
    """Caller-aware view of a v2 codelist's per-reviewer voting state.

    Returns the data the v2 review UI needs in one payload — kappa,
    finalisation flags, the caller's own votes, and (post-self-
    finalisation) the peer's votes. Reviewers and the codelist
    creator can read it; other authenticated users get 403.

    The peer-votes privacy filter is the anchoring-bias guard from
    Watson 2017 Stage 3 — see ``hitl_store.get_voting_state`` for
    the rule.
    """
    try:
        return hitl_store.get_voting_state(
            cid=codelist_id, caller_user_id=user["id"],
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Codelist not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.post("/{codelist_id}/reject")
async def reject_codelist(
    codelist_id: str,
    body: RejectRequest,
    user: dict = Depends(get_current_user),
):
    """Unilateral reject from a v2 codelist's review or adjudication
    state. Single-reviewer rejection is sufficient — reject is a
    veto by design. v1 codelists reject through ``/review`` with
    ``action=reject`` (legacy path)."""
    try:
        return hitl_store.reject_codelist_v2(
            cid=codelist_id,
            reviewer_id=user["id"],
            reason=body.reason,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Codelist not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except hitl_store.ConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reject a codelist in status '{exc.status}'",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- T32 privacy toggle -----------------------------------------------------

@router.put("/{codelist_id}/privacy")
async def set_privacy(
    codelist_id: str,
    body: PrivacyRequest,
    user: dict = Depends(get_current_user),
):
    """Owner-only flip of the public-gallery opt-out flag. 404 for
    missing, 403 for not-the-owner -- distinguishing the two is fine
    here because the auth route already requires a session."""
    try:
        return hitl_store.set_codelist_privacy(
            cid=codelist_id, private=body.private, user_id=user["id"],
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Codelist not found")
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail="Only the codelist creator can change its privacy.",
        )

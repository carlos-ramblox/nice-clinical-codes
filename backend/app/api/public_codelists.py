"""
T32 -- public, unauthenticated read surface for approved codelists.

Mirrors a subset of /api/codelists behind a separate router so the
auth-on-every-route invariant for /api/codelists stays intact: there is
no `Depends(get_current_user)` anywhere in this file. Every row that
leaves this router goes through ``hitl_store._redact_codelist`` first;
the redaction strips override comments, reviewer names (-> initials),
and UMLS-suggestion rows so a non-logged-in visitor cannot reconstruct
who reviewed what or read free-text rationales the reviewer typed.

Visibility filter: ``status = 'approved' AND private = 0``. The owner
flips ``private`` per row via PUT /api/codelists/{id}/privacy on the
authenticated router (see ``codelists.py``) -- this file is read-only.
"""

import csv
import io
import logging

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from app.db import code_store, hitl_store
from app.exports.ohdsi import to_ohdsi_concept_set

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/codelists", tags=["public"])


@router.get("/count")
async def get_public_count(response: Response):
    """Count of approved & non-private codelists -- powers the hero link
    on the search page (`Browse N approved codelists`). Cheap COUNT(*),
    but the search page calls it on every render so we send a short
    Cache-Control window: a freshly-approved codelist is visible within
    a minute and the bot/scraper traffic on the home page stops hitting
    the DB. ``stale-while-revalidate`` lets the browser show the old
    count while it refreshes in the background."""
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
    return {"count": hitl_store.count_public_codelists()}


@router.get("")
async def list_public(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Gallery list view. Newest-approved first."""
    return hitl_store.list_public_codelists(limit=limit, offset=offset)


@router.get("/{codelist_id}")
async def get_public(codelist_id: str):
    """Redacted detail view. 404 covers both 'not found' and
    'private/not-approved' so a private codelist's id can't be probed."""
    cl = hitl_store.get_public_codelist(codelist_id)
    if cl is None:
        raise HTTPException(status_code=404, detail="Codelist not found")
    return cl


# --- public exports --------------------------------------------------------
#
# CSV ships today; OHDSI ships today (T28 has landed). FHIR will plug in
# under the same path once T21 lands -- format=fhir is reserved.

_CSV_FIELDS = [
    "code", "term", "vocabulary",
    "ai_decision", "human_decision", "ai_confidence", "ai_rationale",
    "sources",
]


def _csv_response(name: str, decisions: list[dict]) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS)
    writer.writeheader()
    for d in decisions:
        row = {f: d.get(f, "") for f in _CSV_FIELDS}
        sources = row["sources"]
        row["sources"] = ", ".join(sources) if isinstance(sources, list) else sources
        writer.writerow(row)
    buf.seek(0)
    # Slug for the filename so a downloaded file is recognisable in the
    # user's downloads folder. Worst-case empty -> "codelist.csv".
    slug = (name or "codelist").lower()
    slug = "".join(c if c.isalnum() else "-" for c in slug).strip("-")[:60] or "codelist"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={slug}.csv"},
    )


@router.get("/{codelist_id}/export")
async def export_public(
    codelist_id: str,
    # Default differs from the auth-side /api/codelists/{id}/export
    # (which defaults to ohdsi). Public visitors are more likely to want
    # a quick CSV than an ATLAS-shaped concept set, so the default tilts
    # accordingly; both formats are still reachable via ?format=...
    format: str = Query("csv", description="csv | ohdsi"),
):
    """Public CSV / OHDSI exports. UMLS-suggestion rows already dropped
    by the redaction step so they don't leak through the export either."""
    if format not in ("csv", "ohdsi"):
        raise HTTPException(status_code=400, detail="format must be 'csv' or 'ohdsi'")

    cl = hitl_store.get_public_codelist(codelist_id)
    if cl is None:
        raise HTTPException(status_code=404, detail="Codelist not found")

    decisions = cl.get("decisions") or []

    if format == "ohdsi":
        # Same enrichment shape the auth route uses: pin concept_id from
        # the decision row when present, fall back to the local code_store
        # lookup. Never invent an id -- unmapped rows go to the parallel
        # `unmapped` array.
        enriched: list[dict] = []
        for d in decisions:
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

    return _csv_response(cl.get("name") or "codelist", decisions)

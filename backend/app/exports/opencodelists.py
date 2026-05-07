"""OpenCodelists upload-CSV export.

Emits the artefact OpenCodelists's "Create a codelist" form accepts:
one CSV per coding system (case-sensitive ``code,term`` header per
``codelists/forms.py::CSVValidationMixin`` in opensafely-core/opencodelists),
packaged in a ZIP alongside a ``provenance.json`` with the SHA-256
signature and reviewer pair.

OpenCodelists scopes each codelist to a single coding system; mixed-
vocabulary CSVs are rejected. We partition included decisions on the
OpenCodelists slug for the row's vocabulary; rows with no slug fall
through to ``dropped_codes``.

Slugs are the directory names under ``coding_systems/`` in
opensafely-core/opencodelists. ``icd10`` is WHO only — there is no
``icd10cm`` slug, so US ICD-10-CM rows deliberately drop rather
than be mislabelled.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


_OPENCODELISTS_VOCAB_MAP: dict[str, str] = {
    "SNOMEDCT": "snomedct",
    "SNOMED":   "snomedct",
    "ICD10":    "icd10",
    "OPCS4":    "opcs4",
    "CTV3":     "ctv3",
    "READV3":   "ctv3",
    "READV2":   "readv2",
    "BNF":      "bnf",
    "DMD":      "dmd",
}


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_PARENTHETICAL = re.compile(r"\([^)]*\)")
_NORMALISE_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def _normalise_vocab_key(vocab: str) -> str:
    """Canonicalise for slug lookup. "ICD-10 (WHO)" -> "ICD10"."""
    no_paren = _PARENTHETICAL.sub("", vocab or "")
    return _NORMALISE_NON_ALNUM.sub("", no_paren.upper())


def slug_for(text: str, fallback: str) -> str:
    """Lowercase ASCII filename slug, capped at 60 chars."""
    s = _SLUG_NON_ALNUM.sub("-", (text or "").lower()).strip("-")
    return s[:60] or fallback


def group_for_opencodelists(
    decisions: list[dict],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Partition included decisions by OpenCodelists slug.

    UMLS-suggestion rows drop silently (CUIs aren't OpenCodelists codes).
    Rows whose vocabulary has no slug surface in the second return value.
    """
    groups: dict[str, list[dict]] = {}
    dropped: list[dict] = []
    for d in decisions:
        if d.get("is_umls_suggestion"):
            continue
        final = d.get("human_decision") or d.get("ai_decision")
        if final != "include":
            continue
        code = (d.get("code") or "").strip()
        if not code:
            continue
        vocab = (d.get("vocabulary") or "").strip()
        slug = _OPENCODELISTS_VOCAB_MAP.get(_normalise_vocab_key(vocab))
        if slug is None:
            dropped.append({"code": code, "vocabulary": vocab})
            continue
        groups.setdefault(slug, []).append(
            {"code": code, "term": (d.get("term") or "").strip()},
        )
    return groups, dropped


def build_provenance(
    cl: dict,
    groups: dict[str, list[dict]],
    dropped: list[dict],
    reviewer_names: dict[str, str],
    *,
    base: str,
) -> dict[str, Any]:
    """Provenance trailer. ``base`` is the codelist filename slug —
    threaded in (rather than re-derived) so the manifest's
    ``csv_filename`` values match the actual ZIP member names. Caller
    supplies ``reviewer_names`` so this stays DB-free."""
    reviewer_ids = list(cl.get("reviewer_ids") or [])
    return {
        "schema_version": 2,
        "source": "clinicalcodes.uk",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "codelist": {
            "id": cl.get("id"),
            "name": cl.get("name"),
            "version": cl.get("version"),
            "query": cl.get("query"),
            "approved_at": cl.get("reviewed_at"),
            "include_criteria": cl.get("include_criteria") or [],
            "exclude_criteria": cl.get("exclude_criteria") or [],
        },
        "signature": {
            "algorithm": "sha256",
            "value": cl.get("signature_hash"),
            "signature_version": cl.get("signature_version"),
        },
        "reviewers": {
            "reviewer_ids": reviewer_ids,
            "reviewer_names": reviewer_names,
            "agreement_kappa": cl.get("agreement_kappa"),
        },
        "coding_systems": [
            {
                "opencodelists_slug": slug,
                "csv_filename": f"{base}.{slug}.csv",
                "code_count": len(rows),
            }
            for slug, rows in sorted(groups.items())
        ],
        "dropped_codes": dropped,
        "upload_instructions": (
            "OpenCodelists codelists are scoped to a single coding system. "
            "Upload each CSV in ``coding_systems`` as its own codelist on "
            "https://www.opencodelists.org/ via the 'Create a codelist' form, "
            "picking the matching ``opencodelists_slug`` value as the coding "
            "system. The CSV header is ``code,term``."
        ),
    }

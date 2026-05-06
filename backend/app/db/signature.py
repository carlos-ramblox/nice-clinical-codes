"""
T30 codelist-signature helpers — pure Python, no I/O.

The dispatcher ``hitl_store._compute_signature`` reads the codelist
row and per-decision rows from SQLite, then delegates to the v1 or
v2 helper here based on ``signature_version``. Keeping these
functions pure means tests can exercise the byte-format contracts
by passing dicts directly (see ``test_signature_v2.py``) rather
than spinning up a SQL fixture.

``signature_version`` is **immutable per codelist**: set when the
codelist commits to either path (v1 at creation with empty
``reviewer_ids``, v2 when the first ≥2 reviewers are assigned via
``POST /reviewers``) and never mutated afterwards. Promoting a v1
codelist to v2 retroactively would invalidate every prior
verification of its v1 signature, so the workflow forks instead —
adding reviewers to a legacy codelist creates a new v2 codelist
with a new id rather than mutating the existing row.
"""
from __future__ import annotations

import hashlib
import json
import math


# Method tag baked into v2's kappa block. Switching to a different
# method (weighted, Fleiss, etc.) is a deliberate signature change
# — bump this to a new tag and ship a new ``signature_version``, do
# not silently change the tag for an existing v2 codelist.
_KAPPA_METHOD_TAG = "cohen-unweighted"


def _parse_criteria(codelist: dict) -> tuple[list, list]:
    """Parse ``include_criteria`` and ``exclude_criteria`` from a
    codelist row, returning sorted lists ready for deterministic
    serialisation. Empty lists for missing, NULL, or malformed
    columns — same byte-compat fallback both v1 and v2 used inline
    before this helper was extracted.

    Sorting here is the single source of order for the criteria
    payload across both signature versions; downstream callers feed
    the lists straight into ``json.dumps`` without re-sorting.
    """
    try:
        inc = sorted(json.loads(codelist.get("include_criteria") or "[]"))
        exc = sorted(json.loads(codelist.get("exclude_criteria") or "[]"))
    except (TypeError, ValueError):
        return [], []
    return inc, exc


def _decision_block(decisions: list[dict]) -> str:
    """Per-decision payload block, shared by v1 and v2.

    Sorted by ``(code, vocabulary)``; one row per line in
    ``code|vocabulary|final_decision`` format. Deterministic — the
    sort is the single source of order for both versions, so
    semantically-equal codelists (same codes, decisions, and
    vocabularies) hash identically regardless of insertion order.

    Format assumption: ``code`` and ``vocabulary`` values do not
    contain ``|`` or newline characters. Well-formed values from
    OMOPHub / OpenCodelists / QOF / Chroma never do (codes are
    alphanumeric/dotted, vocabularies are short tags from
    ``OMOPHUB_VOCABULARIES``), and ``hitl_store._insert_decisions``
    rejects any pathological input at write time. The pipe-delimited
    format is frozen because changing it would break v1 byte-compat
    (every pre-T30 approved hash would re-verify wrong); the
    insertion-time validation is the safety net for v2 too.
    """
    rows = sorted(
        (d["code"], d["vocabulary"], d["final_decision"]) for d in decisions
    )
    return "\n".join(f"{c}|{v}|{f}" for c, v, f in rows)


def _compute_signature_v1(codelist: dict, decisions: list[dict]) -> str:
    """v1 signature: pre-T30 single-reviewer format.

    Payload::

        {decision_block}
        [--criteria--                    <- conditional, only when non-empty
        {"exclude": [...], "include": [...]}]

    Frozen — pre-T30 approved hashes verify byte-identical under
    this function. Any mutation here is a backward-compat break.
    """
    payload = _decision_block(decisions)
    inc, exc = _parse_criteria(codelist)
    if inc or exc:
        criteria_block = json.dumps(
            {"include": inc, "exclude": exc}, sort_keys=True,
        )
        payload += f"\n--criteria--\n{criteria_block}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_signature_v2(codelist: dict, decisions: list[dict]) -> str:
    """v2 signature: post-T30 two-reviewer Delphi canonical format.

    Payload::

        {decision_block}
        --criteria--
        {"exclude": [...], "include": [...]}
        --reviewers--
        [3, 7]
        --kappa--
        cohen-unweighted:0.5234

    Always-emit-everything: the criteria block is unconditional
    (no T29-style append-only-when-non-empty), the reviewers block
    is the sorted JSON list, and the kappa block carries its
    method tag so a future method switch is visible in the hash.
    ``cohen-unweighted:null`` represents an undefined kappa (e.g.
    a v2 codelist that approved unanimously without ever entering
    adjudication, where kappa was never computed).
    """
    payload = _decision_block(decisions)

    inc, exc = _parse_criteria(codelist)
    criteria_block = json.dumps(
        {"include": inc, "exclude": exc}, sort_keys=True,
    )

    try:
        reviewer_ids = sorted(json.loads(codelist.get("reviewer_ids") or "[]"))
    except (TypeError, ValueError):
        reviewer_ids = []
    reviewers_block = json.dumps(reviewer_ids)

    kappa = codelist.get("agreement_kappa")
    if kappa is None:
        kappa_block = f"{_KAPPA_METHOD_TAG}:null"
    else:
        kappa_f = float(kappa)
        # NaN / Inf are not valid agreement scores and would silently
        # embed ``cohen-unweighted:nan`` (or ``inf``) in the audit
        # hash. Cohen's kappa over the {include, exclude, uncertain}
        # label set cannot mathematically yield NaN/Inf (the formula
        # is bounded), so reaching this branch indicates upstream
        # corruption — fail loud so the codelist isn't approved with
        # a meaningless signature.
        if not math.isfinite(kappa_f):
            raise ValueError(
                f"agreement_kappa is non-finite ({kappa_f!r}); "
                "cannot produce a stable v2 signature"
            )
        # Normalise -0.0 to +0.0 so two semantically equal kappas
        # always render as the same byte string. ``-0.0`` is unreachable
        # through the current ``cohen_kappa`` formula but cheap defence
        # in depth — IEEE 754 says ``-0.0 + 0.0 == +0.0``.
        kappa_f = kappa_f + 0.0
        # 4 decimal places — enough resolution for the audit
        # (Landis & Koch bands are stated to 2 dp); avoids the
        # floating-point repr noise that would otherwise put
        # non-deterministic bytes in the hash.
        kappa_block = f"{_KAPPA_METHOD_TAG}:{kappa_f:.4f}"

    payload += (
        f"\n--criteria--\n{criteria_block}"
        f"\n--reviewers--\n{reviewers_block}"
        f"\n--kappa--\n{kappa_block}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

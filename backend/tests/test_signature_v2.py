"""
Pin the T30 signature dispatcher and the v1/v2 helpers.

The load-bearing property is **v1 byte-compat** — every codelist
approved before T30 has a stored ``signature_hash`` computed under
the pre-T30 formula; ``_compute_signature_v1`` must produce
byte-identical bytes for the same inputs. ``test_signature_hash_criteria``
already pins this from an end-to-end angle (POST /review through to
the stored hash); this file pins it at the helper level so a future
refactor that touches v1's payload format fails loud here too.

The v2 helper is also tested for byte-stability across reorderings
(decisions, criteria, reviewer_ids) and for the kappa method-tag
property — a method change should produce a legitimately different
hash, which we verify by reproducing the payload manually and
comparing to the helper's output.

Run from backend/:
    pytest tests/test_signature_v2.py -v
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db.hitl_store import (  # noqa: E402
    _compute_signature_v1,
    _compute_signature_v2,
    _decision_block,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

_DECISIONS_FIXTURE = [
    {"code": "E11", "vocabulary": "ICD-10", "final_decision": "include"},
    {"code": "E10", "vocabulary": "ICD-10", "final_decision": "exclude"},
]


def _v1_codelist(**overrides: object) -> dict:
    """Default v1 codelist row (post-T29 / pre-T30 columns absent are
    None or default)."""
    base = {
        "signature_version": 1,
        "include_criteria": "[]",
        "exclude_criteria": "[]",
        "reviewer_ids": "[]",
        "agreement_kappa": None,
    }
    base.update(overrides)
    return base


def _v2_codelist(**overrides: object) -> dict:
    """Default v2 codelist row."""
    base = {
        "signature_version": 2,
        "include_criteria": "[]",
        "exclude_criteria": "[]",
        "reviewer_ids": json.dumps([3, 7]),
        "agreement_kappa": 0.5234,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# v1 byte-compat
# ---------------------------------------------------------------------------


def test_v1_empty_criteria_matches_pre_t29_byte_format() -> None:
    """v1 with empty criteria must hash byte-identical to the legacy
    ``code|vocabulary|human_decision`` formula. This is the load-bearing
    backward-compat property — every pre-T29 approved hash on
    production verifies under this branch and would re-verify after
    T30 ships."""
    codelist = _v1_codelist()

    # Reproduce the legacy formula manually: sort by (code, vocabulary),
    # join lines, hash.
    rows = sorted(_DECISIONS_FIXTURE, key=lambda d: (d["code"], d["vocabulary"]))
    expected_payload = "\n".join(
        f"{d['code']}|{d['vocabulary']}|{d['final_decision']}" for d in rows
    )
    expected_hash = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()

    assert _compute_signature_v1(codelist, _DECISIONS_FIXTURE) == expected_hash


def test_v1_with_criteria_uses_conditional_append() -> None:
    """v1 with non-empty criteria appends ``--criteria--`` (T29).
    The conditional-append rule means an empty-criteria codelist's
    hash must NOT contain the trailer — pin both shapes by computing
    them separately and asserting they differ."""
    empty_hash = _compute_signature_v1(_v1_codelist(), _DECISIONS_FIXTURE)
    with_criteria_hash = _compute_signature_v1(
        _v1_codelist(include_criteria=json.dumps(["adult"])),
        _DECISIONS_FIXTURE,
    )
    assert empty_hash != with_criteria_hash


def test_v1_criteria_list_order_does_not_leak_into_hash() -> None:
    """Two v1 codelists with the same criteria *set* but different
    list ORDER must hash identically. The sort happens inside v1
    before serialisation."""
    h1 = _compute_signature_v1(
        _v1_codelist(exclude_criteria=json.dumps(["gestational", "type 1"])),
        _DECISIONS_FIXTURE,
    )
    h2 = _compute_signature_v1(
        _v1_codelist(exclude_criteria=json.dumps(["type 1", "gestational"])),
        _DECISIONS_FIXTURE,
    )
    assert h1 == h2


# ---------------------------------------------------------------------------
# v2 canonical payload
# ---------------------------------------------------------------------------


def test_v2_payload_is_byte_stable_across_reorderings() -> None:
    """v2 is canonical: same logical content in any incoming order
    produces the same hash. Pins decisions, criteria lists, and
    reviewer_ids are all sorted before serialisation."""
    base_hash = _compute_signature_v2(_v2_codelist(), _DECISIONS_FIXTURE)

    # Reorder decisions.
    reordered_decisions = list(reversed(_DECISIONS_FIXTURE))
    assert _compute_signature_v2(_v2_codelist(), reordered_decisions) == base_hash

    # Reorder reviewer_ids.
    swapped_reviewers_hash = _compute_signature_v2(
        _v2_codelist(reviewer_ids=json.dumps([7, 3])),
        _DECISIONS_FIXTURE,
    )
    assert swapped_reviewers_hash == base_hash

    # Reorder include_criteria.
    cl_a = _v2_codelist(include_criteria=json.dumps(["a", "b", "c"]))
    cl_b = _v2_codelist(include_criteria=json.dumps(["c", "a", "b"]))
    assert _compute_signature_v2(cl_a, _DECISIONS_FIXTURE) == _compute_signature_v2(cl_b, _DECISIONS_FIXTURE)


def test_v2_payload_format_matches_canonical_construction() -> None:
    """Reproduce the v2 payload manually and verify the helper's hash
    matches. Pins the exact byte format including the kappa method
    tag (``cohen-unweighted:0.5234``) — a future code-level change
    that mutates the format will diverge from this manual
    construction and fail loud here.
    """
    codelist = _v2_codelist(
        include_criteria=json.dumps(["adult"]),
        exclude_criteria=json.dumps(["gestational"]),
        reviewer_ids=json.dumps([7, 3]),
        agreement_kappa=0.5234,
    )
    expected_payload = (
        f"{_decision_block(_DECISIONS_FIXTURE)}"
        '\n--criteria--\n{"exclude": ["gestational"], "include": ["adult"]}'
        "\n--reviewers--\n[3, 7]"
        "\n--kappa--\ncohen-unweighted:0.5234"
    )
    expected_hash = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert _compute_signature_v2(codelist, _DECISIONS_FIXTURE) == expected_hash


def test_v2_kappa_null_renders_as_null_literal() -> None:
    """``agreement_kappa = None`` renders as ``cohen-unweighted:null``,
    not as missing-block or ``cohen-unweighted:None`` (Python repr).
    This is the case for a v2 codelist that approved unanimously and
    never entered adjudication, so kappa was never computed.

    The literal ``cohen-unweighted:null`` is pinned in the
    expected_payload below — if the helper ever drops the ``null``
    sentinel or switches to Python-repr ``None``, the hash
    comparison fails.
    """
    codelist = _v2_codelist(agreement_kappa=None)
    expected_payload = (
        f"{_decision_block(_DECISIONS_FIXTURE)}"
        '\n--criteria--\n{"exclude": [], "include": []}'
        "\n--reviewers--\n[3, 7]"
        "\n--kappa--\ncohen-unweighted:null"
    )
    expected_hash = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert _compute_signature_v2(codelist, _DECISIONS_FIXTURE) == expected_hash


def test_v2_kappa_method_tag_is_part_of_the_hash() -> None:
    """If a future ticket switches the method (e.g. cohen-linear-
    weighted), the resulting hash MUST be different from the
    cohen-unweighted hash for the same kappa value. This is the
    whole reason the tag is in the payload — pin the property by
    reproducing the v2 payload with a different method tag and
    asserting the resulting hash is not what the helper produces.
    """
    codelist = _v2_codelist(agreement_kappa=0.5234)

    helper_hash = _compute_signature_v2(codelist, _DECISIONS_FIXTURE)

    # Reproduce the payload with a hypothetical alternative method.
    payload_alternative = (
        f"{_decision_block(_DECISIONS_FIXTURE)}"
        '\n--criteria--\n{"exclude": [], "include": []}'
        "\n--reviewers--\n[3, 7]"
        "\n--kappa--\ncohen-linear-weighted:0.5234"
    )
    alternative_hash = hashlib.sha256(payload_alternative.encode("utf-8")).hexdigest()

    assert helper_hash != alternative_hash, (
        "kappa method tag should be part of the v2 hash — switching "
        "from cohen-unweighted to a hypothetical alternative MUST "
        "change the signature, otherwise a future method swap is a "
        "silent regression"
    )


def test_v2_kappa_value_is_part_of_the_hash() -> None:
    """Two v2 codelists identical except for kappa value must hash
    differently — the kappa block contributes to the signature."""
    cl_a = _v2_codelist(agreement_kappa=0.50)
    cl_b = _v2_codelist(agreement_kappa=0.80)
    assert (
        _compute_signature_v2(cl_a, _DECISIONS_FIXTURE)
        != _compute_signature_v2(cl_b, _DECISIONS_FIXTURE)
    )


# ---------------------------------------------------------------------------
# v1 vs v2 divergence
# ---------------------------------------------------------------------------


def test_v1_and_v2_diverge_on_same_input() -> None:
    """A codelist with the exact same content but different
    ``signature_version`` must hash differently — the dispatcher
    needs to actually dispatch, not return the same value
    regardless of version. Belt-and-braces against a future bug
    that accidentally falls through to v1 for v2 codelists.
    """
    decisions = _DECISIONS_FIXTURE
    cl_v1 = _v1_codelist()
    cl_v2 = _v2_codelist(reviewer_ids="[]", agreement_kappa=None)
    assert (
        _compute_signature_v1(cl_v1, decisions)
        != _compute_signature_v2(cl_v2, decisions)
    )


# ---------------------------------------------------------------------------
# v2 numeric guards
# ---------------------------------------------------------------------------


def test_v2_rejects_nan_kappa() -> None:
    """``agreement_kappa = NaN`` must raise rather than silently embed
    ``cohen-unweighted:nan`` in the audit hash. Cohen's kappa over
    string labels can't mathematically yield NaN; reaching this
    branch indicates upstream corruption and must fail loud."""
    codelist = _v2_codelist(agreement_kappa=float("nan"))
    with pytest.raises(ValueError, match="non-finite"):
        _compute_signature_v2(codelist, _DECISIONS_FIXTURE)


def test_v2_rejects_infinite_kappa() -> None:
    """``agreement_kappa = +inf`` / ``-inf`` must also raise. Same
    reasoning as the NaN case — non-finite values do not round-trip
    through ``f"{kappa:.4f}"`` to a meaningful audit string."""
    for bad in (float("inf"), float("-inf")):
        codelist = _v2_codelist(agreement_kappa=bad)
        with pytest.raises(ValueError, match="non-finite"):
            _compute_signature_v2(codelist, _DECISIONS_FIXTURE)


def test_v2_normalises_negative_zero_kappa() -> None:
    """``agreement_kappa = -0.0`` must hash identically to
    ``agreement_kappa = 0.0``. Without the ``+ 0.0`` normalisation,
    Python's float formatter produces ``-0.0000`` vs ``0.0000`` —
    two byte sequences for one logical value, breaking the
    "semantically equal codelists hash identically" property."""
    pos_zero = _compute_signature_v2(
        _v2_codelist(agreement_kappa=0.0), _DECISIONS_FIXTURE,
    )
    neg_zero = _compute_signature_v2(
        _v2_codelist(agreement_kappa=-0.0), _DECISIONS_FIXTURE,
    )
    assert pos_zero == neg_zero


# ---------------------------------------------------------------------------
# decision-insertion validation (separator characters)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_field, bad_value", [
    ("code", "E11|E10"),
    ("code", "E11\nbogus"),
    ("vocabulary", "ICD|10"),
    ("vocabulary", "ICD-10\n--criteria--\n[]"),
])
def test_insert_decisions_rejects_separator_chars(
    bad_field: str, bad_value: str,
) -> None:
    """``_insert_decisions`` must reject ``|`` and newline characters
    in ``code`` / ``vocabulary``. Both are signature-payload separators
    in v1 and v2; embedding either in a stored row would create payload
    ambiguity that could collide hashes between semantically-different
    codelists. Well-formed retriever output never contains these
    characters, so the validation is a fail-loud safety net for
    pipeline corruption / future manual-add paths."""
    from app.db.hitl_store import _insert_decisions
    import sqlite3
    conn = sqlite3.connect(":memory:")

    base = {
        "code": "E11",
        "vocabulary": "ICD-10",
        "decision": "include",
        "confidence": 0.9,
        "rationale": "ok",
        "sources": [],
    }
    base[bad_field] = bad_value

    with pytest.raises(ValueError, match="separator character"):
        _insert_decisions(conn, "cl-test", [base])

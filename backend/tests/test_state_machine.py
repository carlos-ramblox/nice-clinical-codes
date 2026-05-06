"""
Pin the T30 state machine and the voting helpers it sits on top of.

The state machine is the load-bearing safety surface for the
two-reviewer Delphi flow: every status change a codelist makes
must come from the small fixed table of legal edges, and every
move must leave an audit-log trace. Tests here exercise the
``_transition`` helper directly with an in-memory SQLite — bypass
the API layer so the matrix can stay focused on the transition
algebra without auth / payload-shape noise.

Voting helpers (``_record_vote``, ``_mark_voting_finalised``,
``_both_reviewers_finalised``, ``_compute_codelist_kappa``) get
their own focused tests in the same file because they participate
in the same workflow — a route at step 5 calls them in concert
when reviewer B finalises and the codelist auto-advances.

Run from backend/:
    pytest tests/test_state_machine.py -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db.hitl_store import ConflictError, _init_schema  # noqa: E402
from app.db.state_machine import (  # noqa: E402
    InvalidTransition,
    _VALID_TRANSITIONS,
    _both_reviewers_finalised,
    _compute_codelist_kappa,
    _mark_voting_finalised,
    _record_vote,
    _transition,
)


# ---------------------------------------------------------------------------
# Transition matrix — derived from the production constant.
#
# Anything not in LEGAL_EDGES must raise InvalidTransition. Importing
# the production set directly (rather than re-typing it) means the
# parametrised tests below automatically pick up any future edge
# addition; a hand-typed mirror would silently diverge.
# ---------------------------------------------------------------------------

LEGAL_EDGES: frozenset[tuple[str, str]] = _VALID_TRANSITIONS

ALL_STATUSES: tuple[str, ...] = (
    "draft", "in_review", "adjudication", "approved", "rejected",
)
# Every (from, to) pair we should test — full Cartesian minus self-loops.
ALL_PAIRS: list[tuple[str, str]] = [
    (a, b) for a in ALL_STATUSES for b in ALL_STATUSES if a != b
]
ILLEGAL_EDGES: list[tuple[str, str]] = [
    pair for pair in ALL_PAIRS if pair not in LEGAL_EDGES
]


# ---------------------------------------------------------------------------
# Test fixtures: in-memory SQLite seeded with the post-T30 schema.
# ---------------------------------------------------------------------------


def _open_post_t30() -> sqlite3.Connection:
    """In-memory SQLite with the full post-T30 schema — created via
    ``_init_schema``. Used directly by the state-machine tests so we
    don't run the migration path: the migration's row-preservation
    contract is pinned in ``test_hitl_t30_migration.py`` (8 tests
    covering the rebuild dance), and re-running the migration here
    would only duplicate that surface. Coverage of the end-to-end
    "pre-T30 DB on disk → T30 schema in memory" path lives in the
    migration test file; this fixture exercises the post-migration
    state machine in isolation."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_schema(conn)
    conn.execute(
        "INSERT INTO users (id, email, name) VALUES "
        "(1, 'u1@x', 'U1'), (2, 'u2@x', 'U2'), (3, 'u3@x', 'U3')"
    )
    conn.commit()
    return conn


def _seed_codelist(
    conn: sqlite3.Connection,
    cid: str = "cl1",
    status: str = "draft",
    reviewer_ids: list[int] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO codelists (id, name, status, query, created_by, reviewer_ids) "
        "VALUES (?, 'fixture', ?, 'q', 1, ?)",
        (cid, status, json.dumps(reviewer_ids or [])),
    )
    conn.commit()


def _seed_decisions(
    conn: sqlite3.Connection,
    cid: str,
    n: int,
) -> list[int]:
    """Insert ``n`` placeholder decisions, return their decision ids."""
    ids: list[int] = []
    for i in range(n):
        cur = conn.execute(
            "INSERT INTO codelist_decisions "
            "(codelist_id, code, vocabulary, ai_decision, ai_confidence, "
            " ai_rationale, human_decision, sources, is_umls_suggestion) "
            "VALUES (?, ?, 'ICD-10', 'include', 0.9, 'ok', 'include', '[]', 0)",
            (cid, f"D{i:03d}"),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


# ---------------------------------------------------------------------------
# transition matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("from_status, to_status", sorted(LEGAL_EDGES))
def test_legal_edges_succeed(from_status: str, to_status: str) -> None:
    """Every edge in the legal table must be acceptable. Rejection
    edges need a non-empty reason — supply one for those."""
    conn = _open_post_t30()
    _seed_codelist(conn, status=from_status)

    reason = "fixture reason" if to_status == "rejected" else None
    _transition(conn, "cl1", from_status, to_status, reviewer_id=1, reason=reason)
    conn.commit()

    new_status = conn.execute(
        "SELECT status FROM codelists WHERE id = 'cl1'"
    ).fetchone()["status"]
    assert new_status == to_status

    # Audit-log entry recorded.
    audit = conn.execute(
        "SELECT event, details FROM audit_log "
        "WHERE codelist_id = 'cl1' AND event = 'status_transition'"
    ).fetchall()
    assert len(audit) == 1
    details = json.loads(audit[0]["details"])
    assert details["from"] == from_status
    assert details["to"] == to_status


@pytest.mark.parametrize("from_status, to_status", ILLEGAL_EDGES)
def test_illegal_edges_raise_invalid_transition(
    from_status: str, to_status: str,
) -> None:
    """Every (from, to) pair NOT in the legal table raises
    ``InvalidTransition``. Belt-and-braces against a future
    refactor that accidentally widens the table — the fixed
    Cartesian sweep makes additions to LEGAL_EDGES need an
    explicit update here too."""
    conn = _open_post_t30()
    _seed_codelist(conn, status=from_status)

    with pytest.raises(InvalidTransition):
        _transition(conn, "cl1", from_status, to_status, reviewer_id=1)

    # Status unchanged.
    assert conn.execute(
        "SELECT status FROM codelists WHERE id = 'cl1'"
    ).fetchone()["status"] == from_status


def test_transition_raises_conflict_error_when_actual_status_differs() -> None:
    """If the codelist's actual status doesn't match the caller's
    declared ``from_status``, raise ``ConflictError`` (stale view).
    Distinct from ``InvalidTransition``: the edge IS legal, but the
    state has moved underneath us — typical race when a second
    reviewer reaches the next state first."""
    conn = _open_post_t30()
    _seed_codelist(conn, status="approved")  # already terminal

    with pytest.raises(ConflictError) as exc_info:
        _transition(conn, "cl1", "in_review", "approved", reviewer_id=1)
    assert exc_info.value.status == "approved"


def test_transition_raises_key_error_when_codelist_missing() -> None:
    """A transition on a missing codelist raises ``KeyError``, same
    contract as ``submit_review``. The route translates this to 404."""
    conn = _open_post_t30()
    with pytest.raises(KeyError):
        _transition(conn, "does-not-exist", "draft", "in_review", reviewer_id=1)


# ---------------------------------------------------------------------------
# rejection-reason requirement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("from_status", ["in_review", "adjudication"])
def test_rejection_from_review_states_requires_reason(from_status: str) -> None:
    """Rejecting from in_review or adjudication without a reason is
    itself a clinical-safety hazard — the audit log must record
    *why* a codelist was rejected, not just that it was. Empty or
    whitespace-only strings count as missing."""
    conn = _open_post_t30()
    _seed_codelist(conn, status=from_status)

    for missing_reason in (None, "", "   "):
        with pytest.raises(ValueError, match="non-empty reason"):
            _transition(
                conn, "cl1", from_status, "rejected",
                reviewer_id=1, reason=missing_reason,
            )


def test_rejection_from_draft_does_not_require_reason() -> None:
    """The legacy single-reviewer rejection path (draft → rejected)
    does NOT require a reason in this helper — the legacy
    ``submit_review`` carries reviewer notes via the route's
    ``notes`` field, separately. Don't break the legacy path."""
    conn = _open_post_t30()
    _seed_codelist(conn, status="draft")
    _transition(conn, "cl1", "draft", "rejected", reviewer_id=1, reason=None)
    conn.commit()
    assert conn.execute(
        "SELECT status FROM codelists WHERE id = 'cl1'"
    ).fetchone()["status"] == "rejected"


def test_rejection_audit_records_the_reason() -> None:
    """The rejection reason must be persisted to the audit log so
    forensic readers can reconstruct *why* a codelist was rejected."""
    conn = _open_post_t30()
    _seed_codelist(conn, status="adjudication")
    _transition(
        conn, "cl1", "adjudication", "rejected",
        reviewer_id=2, reason="codes E10/E11 conflict with study intent",
    )
    conn.commit()

    audit = conn.execute(
        "SELECT details FROM audit_log "
        "WHERE codelist_id = 'cl1' AND event = 'status_transition'"
    ).fetchone()
    assert json.loads(audit["details"])["reason"].startswith("codes E10/E11")


# ---------------------------------------------------------------------------
# voting helpers
# ---------------------------------------------------------------------------


def test_record_vote_inserts_then_upserts() -> None:
    """First call inserts; second call on the same (decision, reviewer)
    pair upserts in place (UNIQUE constraint + ON CONFLICT). No silent
    duplicate rows."""
    conn = _open_post_t30()
    _seed_codelist(conn, reviewer_ids=[1, 2])
    [did] = _seed_decisions(conn, "cl1", 1)

    _record_vote(conn, did, reviewer_id=1, vote="include", comment="first")
    conn.commit()
    _record_vote(conn, did, reviewer_id=1, vote="exclude", comment="changed mind")
    conn.commit()

    rows = conn.execute(
        "SELECT vote, comment FROM decision_votes "
        "WHERE decision_id = ? AND reviewer_id = 1",
        (did,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["vote"] == "exclude"
    assert rows[0]["comment"] == "changed mind"


def test_mark_voting_finalised_writes_audit_event() -> None:
    """``_mark_voting_finalised`` writes a single ``voting_finalised``
    event with the reviewer's id. The audit chain is the source of
    truth for "I'm done voting"; ``_both_reviewers_finalised`` reads
    it back."""
    conn = _open_post_t30()
    _seed_codelist(conn, reviewer_ids=[1, 2])

    _mark_voting_finalised(conn, "cl1", reviewer_id=2)
    conn.commit()

    rows = conn.execute(
        "SELECT user_id FROM audit_log "
        "WHERE codelist_id = 'cl1' AND event = 'voting_finalised'"
    ).fetchall()
    assert [r["user_id"] for r in rows] == [2]


def test_both_reviewers_finalised_requires_every_assignee() -> None:
    """``_both_reviewers_finalised`` returns True only when every
    assigned reviewer has logged a finalisation event. One out of two
    isn't enough; an extra event from someone not on the assignee list
    doesn't substitute for a missing assignee."""
    conn = _open_post_t30()
    _seed_codelist(conn, reviewer_ids=[1, 2])

    assert _both_reviewers_finalised(conn, "cl1", [1, 2]) is False

    _mark_voting_finalised(conn, "cl1", reviewer_id=1)
    conn.commit()
    assert _both_reviewers_finalised(conn, "cl1", [1, 2]) is False

    # Non-assignee finalising doesn't count.
    _mark_voting_finalised(conn, "cl1", reviewer_id=3)
    conn.commit()
    assert _both_reviewers_finalised(conn, "cl1", [1, 2]) is False

    _mark_voting_finalised(conn, "cl1", reviewer_id=2)
    conn.commit()
    assert _both_reviewers_finalised(conn, "cl1", [1, 2]) is True


def test_both_reviewers_finalised_returns_false_for_empty_assignees() -> None:
    """Empty ``reviewer_ids`` (legacy single-reviewer flow) must
    return False — that path doesn't go through this helper, and if
    it does we want the caller to fail closed."""
    conn = _open_post_t30()
    assert _both_reviewers_finalised(conn, "cl1", []) is False


# ---------------------------------------------------------------------------
# kappa from votes
# ---------------------------------------------------------------------------


def test_compute_codelist_kappa_perfect_agreement_returns_one() -> None:
    """Both reviewers vote identically on every decision → κ = 1."""
    conn = _open_post_t30()
    _seed_codelist(conn, reviewer_ids=[1, 2])
    dids = _seed_decisions(conn, "cl1", 4)

    for did in dids:
        _record_vote(conn, did, 1, "include")
        _record_vote(conn, did, 2, "include")
    # Half include, half exclude — single-category collapse handled
    # by cohen_kappa's pe==1 convention; let's mix to avoid that
    # edge case.
    for did in dids[:2]:
        _record_vote(conn, did, 1, "exclude")
        _record_vote(conn, did, 2, "exclude")
    conn.commit()

    assert _compute_codelist_kappa(conn, "cl1") == 1.0


def test_compute_codelist_kappa_returns_none_with_one_reviewer() -> None:
    """Only one reviewer has voted → insufficient data → None.
    Mirrors ``cohen_kappa``'s "insufficient data" return so the
    caller can persist NULL into ``codelists.agreement_kappa``."""
    conn = _open_post_t30()
    _seed_codelist(conn, reviewer_ids=[1, 2])
    dids = _seed_decisions(conn, "cl1", 2)

    for did in dids:
        _record_vote(conn, did, 1, "include")
    conn.commit()

    assert _compute_codelist_kappa(conn, "cl1") is None


def test_compute_codelist_kappa_raises_for_more_than_two_reviewers() -> None:
    """Three reviewers' votes is not "insufficient data" — it's "wrong
    metric". Cohen's kappa is n=2 only; silently returning None for
    n=3 would persist NULL kappa for a codelist with full vote data,
    masking a data-quality issue. Step 5 must wire a Fleiss-kappa
    helper before unlocking n=3 reviewer assignment."""
    conn = _open_post_t30()
    _seed_codelist(conn, reviewer_ids=[1, 2, 3])
    [did] = _seed_decisions(conn, "cl1", 1)

    _record_vote(conn, did, 1, "include")
    _record_vote(conn, did, 2, "include")
    _record_vote(conn, did, 3, "exclude")
    conn.commit()

    with pytest.raises(ValueError, match="n=2 only"):
        _compute_codelist_kappa(conn, "cl1")


def test_compute_codelist_kappa_uses_only_common_decisions() -> None:
    """If reviewer A votes on {1, 2} and reviewer B votes on {2, 3},
    kappa is computed over the intersection {2}. The decisions only
    one reviewer touched do not contribute — there's nothing to
    compare against. With a single common decision both agreed on,
    pe==1 → 1.0 by convention."""
    conn = _open_post_t30()
    _seed_codelist(conn, reviewer_ids=[1, 2])
    d1, d2, d3 = _seed_decisions(conn, "cl1", 3)

    _record_vote(conn, d1, 1, "include")
    _record_vote(conn, d2, 1, "exclude")
    _record_vote(conn, d2, 2, "exclude")  # <-- only common decision
    _record_vote(conn, d3, 2, "include")
    conn.commit()

    # Common = {d2}, both voted exclude → κ = 1.0 (pe==1 convention).
    assert _compute_codelist_kappa(conn, "cl1") == 1.0

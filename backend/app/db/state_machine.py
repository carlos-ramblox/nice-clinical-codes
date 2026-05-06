"""
T30 two-reviewer Delphi state machine and per-(decision, reviewer)
vote helpers.

Lives separately from ``hitl_store`` so the workflow algebra (legal
edges, transitions, vote upserts, kappa-from-votes) stays focused
without the persistence-layer noise. The route layer in step 5 will
call ``_transition`` and the voting helpers directly under its own
``BEGIN IMMEDIATE`` lock.

Two import-direction notes:

1. This module imports ``_append_audit`` and ``ConflictError`` from
   ``hitl_store`` — the conventional layered direction would have
   the lower-level audit-write helper sit in a third module that
   both can depend on. Pragmatic exception: ``_append_audit`` is a
   single-INSERT helper used by every write path in ``hitl_store``,
   and refactoring it out for one consumer is over-engineering.
   Documented inline so future readers don't trip over the inverted
   arrow.

2. ``cohen_kappa`` is imported from ``app.services.agreement`` — the
   same db→services inversion already documented in
   ``hitl_store.py``'s import block. The metric is pure-Python with
   no I/O; it could move to ``app/utils/`` if a wider audit later
   finds other inversions worth a coordinated relocation.
"""
from __future__ import annotations

import sqlite3

from app.db.hitl_store import ConflictError, _append_audit
from app.services.agreement import cohen_kappa


class InvalidTransition(Exception):
    """Raised when ``_transition`` is asked to move a codelist along an
    edge that is not in ``_VALID_TRANSITIONS``.

    Distinct from ``ConflictError`` (which means "current state doesn't
    match expected from_status — race condition / stale view") because
    an invalid transition is a caller-side bug: the route asked for an
    edge the state machine does not contain. The route translates this
    to HTTP 422 (Unprocessable Entity) so the client can distinguish
    "you tried something the workflow does not allow" (422) from
    "something else got there first" (409).

    ``from_status`` and ``to_status`` are exposed as attributes so
    step 5's route handler can build the 422 ``detail`` string from
    structured fields rather than parsing ``str(exc)`` (mirrors
    ``ConflictError.status``, which the route already uses for the
    409 detail in ``api/codelists.py:review_codelist``).
    """

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"illegal status transition: {from_status} -> {to_status}"
        )


# T30 status edges. Anything not in this set raises InvalidTransition.
# The legacy single-reviewer flow keeps draft -> approved/rejected;
# the two-reviewer Delphi flow uses
# draft -> in_review -> {approved (unanimous), adjudication, rejected};
# adjudication -> {approved (consensus), rejected (with reason)}.
# Approved and rejected are terminal — no edges out.

_VALID_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    # Legacy single-reviewer flow (reviewer_ids = []).
    ("draft", "approved"),
    ("draft", "rejected"),
    # Two-reviewer Delphi flow.
    ("draft", "in_review"),
    ("in_review", "approved"),      # both finalised + unanimous => skip adjudication
    ("in_review", "adjudication"),  # both finalised + at least one disagreement
    ("in_review", "rejected"),      # any reviewer rejects before adjudication
    ("adjudication", "approved"),   # both reviewers ACK consensus
    ("adjudication", "rejected"),   # any reviewer rejects from adjudication (reason required)
})


def _transition(
    conn: sqlite3.Connection,
    cid: str,
    from_status: str,
    to_status: str,
    reviewer_id: int | None,
    *,
    is_final: bool = False,
    reason: str | None = None,
) -> None:
    """Move ``cid`` along a state-machine edge, logging the transition.

    The caller **must** hold a ``BEGIN IMMEDIATE`` transaction
    before invoking this helper. Calling it outside an exclusive
    write lock is unsafe: two concurrent callers can both pass
    the ``from_status`` re-check before either commits, then both
    issue the ``UPDATE``, with the second silently overwriting
    the first and leaving two ``status_transition`` audit rows
    for one logical move. SQLite has no API to assert IMMEDIATE
    vs DEFERRED at runtime, so the contract is enforced by
    convention at every call site.

    This helper does not commit or roll back — it issues one
    ``UPDATE`` and one audit-log ``INSERT`` so the caller can
    batch them with the rest of the request's writes.

    Constraints:

    - The ``(from_status, to_status)`` pair must be in
      ``_VALID_TRANSITIONS`` or ``InvalidTransition`` is raised.
    - The codelist's actual current status must equal
      ``from_status``; otherwise ``ConflictError`` is raised
      (the caller's view of state was stale, e.g. a concurrent
      reviewer reached the next state first).
    - Rejection from ``in_review`` or ``adjudication`` requires a
      non-empty ``reason``. Rejection without a stated reason is
      itself a clinical-safety hazard — the audit log must record
      *why* a codelist was rejected, not just that it was.
      ``ValueError`` if the reason is missing.
    - ``signature_version`` is **not** mutated — it is set at the
      codelist's commit-to-flow point and is immutable afterwards.

    The audit-log entry uses ``event="status_transition"`` with
    ``details = {from, to, is_final, reason}`` so a downstream
    auditor can reconstruct the order and motivation of every
    state change. Note: this differs from ``submit_review``'s
    legacy event names (``event="approved"`` / ``"rejected"``) —
    analytics queries that look for terminal states must check
    both ``event in ('approved','rejected')`` AND
    ``event='status_transition' AND details->>'to' IN
    ('approved','rejected')`` to cover both code paths.

    ``is_final`` is currently always ``False`` at every call site
    because step 5 has not yet wired the per-reviewer routes that
    populate it. The parameter is reserved for that step's vote
    submission flow ("I'm done voting"); it lands in the audit-log
    details so the forensics question "did reviewer X knowingly
    finalise their votes?" reads cleanly off the row.
    """
    if (from_status, to_status) not in _VALID_TRANSITIONS:
        raise InvalidTransition(from_status, to_status)
    if to_status == "rejected" and from_status in ("in_review", "adjudication"):
        if not reason or not reason.strip():
            raise ValueError(
                f"rejection from {from_status} requires a non-empty reason"
            )

    row = conn.execute(
        "SELECT status FROM codelists WHERE id = ?", (cid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"codelist not found: {cid}")
    if row["status"] != from_status:
        raise ConflictError(cid, row["status"])

    conn.execute(
        "UPDATE codelists SET status = ? WHERE id = ?",
        (to_status, cid),
    )
    _append_audit(
        conn, cid,
        event="status_transition",
        user_id=reviewer_id,
        details={
            "from": from_status,
            "to": to_status,
            "is_final": is_final,
            "reason": reason,
        },
    )


# --- per-reviewer voting ----------------------------------------------------

def _record_vote(
    conn: sqlite3.Connection,
    decision_id: int,
    reviewer_id: int,
    vote: str,
    *,
    comment: str | None = None,
) -> None:
    """Record (or update) a reviewer's vote on a single decision.

    ``UNIQUE(decision_id, reviewer_id)`` on the ``decision_votes``
    table is the conflict target for the upsert: a re-vote
    overwrites the existing row with a fresh ``voted_at``
    timestamp rather than creating a parallel duplicate. The DB
    CHECK constraint enforces the ``include`` / ``exclude`` /
    ``uncertain`` set; this helper does not validate the vote
    value (the constraint will fire on insert).

    SQLite's UPSERT ``DO UPDATE`` clause **always** uses ``ABORT``
    for any constraint violation hit during the UPDATE phase,
    regardless of the outer INSERT's conflict resolution
    (https://www.sqlite.org/lang_upsert.html — "Limitations").
    A bad ``vote`` value therefore rolls back the caller's
    surrounding ``BEGIN IMMEDIATE`` transaction entirely, not
    just this one statement — desired safety, but worth noting
    so future callers don't expect single-row recovery.

    This helper is intentionally liberal about *when* a vote is
    recorded — it accepts a re-vote even after the reviewer has
    logged ``voting_finalised``. Step 5's route is the right place
    to refuse post-finalise re-votes if the workflow demands it;
    keeping ``_record_vote`` permissive lets the helper stay
    reusable for normal voting, override paths, and future
    admin-correction flows.

    The caller owns the surrounding transaction.
    """
    conn.execute(
        """INSERT INTO decision_votes (decision_id, reviewer_id, vote, comment)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(decision_id, reviewer_id) DO UPDATE SET
               vote = excluded.vote,
               comment = excluded.comment,
               voted_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
        (decision_id, reviewer_id, vote, comment),
    )


def _mark_voting_finalised(
    conn: sqlite3.Connection,
    cid: str,
    reviewer_id: int,
) -> None:
    """Log that ``reviewer_id`` has finalised their voting on ``cid``.

    "I'm done voting" is recorded as an explicit
    ``voting_finalised`` audit-log event, not derived from
    "every decision has a vote from this reviewer". The clinical
    forensics question "did reviewer X knowingly approve?" needs
    a positive declaration, not an inference from row counts —
    a reviewer who submits 99/100 votes might still be working,
    and the audit log should not silently treat that as approval.
    """
    _append_audit(
        conn, cid, event="voting_finalised",
        user_id=reviewer_id, details={},
    )


def _both_reviewers_finalised(
    conn: sqlite3.Connection,
    cid: str,
    reviewer_ids: list[int],
) -> bool:
    """Return ``True`` iff every assigned reviewer has logged a
    ``voting_finalised`` event on this codelist.

    Empty ``reviewer_ids`` (legacy single-reviewer flow) returns
    ``False`` — the legacy path should not reach this helper, and
    if it does we want the caller to fail closed rather than
    accidentally transition a draft.
    """
    if not reviewer_ids:
        return False
    rows = conn.execute(
        """SELECT DISTINCT user_id FROM audit_log
            WHERE codelist_id = ? AND event = 'voting_finalised'""",
        (cid,),
    ).fetchall()
    finalised = {r["user_id"] for r in rows}
    return all(rid in finalised for rid in reviewer_ids)


def _compute_codelist_kappa(
    conn: sqlite3.Connection,
    cid: str,
) -> float | None:
    """Cohen's kappa over the per-(decision, reviewer) votes for a
    two-reviewer codelist.

    Returns ``None`` when there aren't exactly two distinct
    reviewers' votes covering at least one common decision —
    mirrors ``cohen_kappa``'s "insufficient data" return rather
    than raising, so the caller can persist ``NULL`` into
    ``codelists.agreement_kappa`` without a special-case branch.

    The agreement metric runs against the intersection of
    decisions both reviewers have voted on. Decisions that only
    one reviewer has touched do not contribute (nothing to
    compare against).
    """
    rows = conn.execute(
        """SELECT v.decision_id, v.reviewer_id, v.vote
             FROM decision_votes v
             JOIN codelist_decisions d ON d.id = v.decision_id
            WHERE d.codelist_id = ?
            ORDER BY v.decision_id, v.reviewer_id""",
        (cid,),
    ).fetchall()

    by_reviewer: dict[int, dict[int, str]] = {}
    for r in rows:
        by_reviewer.setdefault(r["reviewer_id"], {})[r["decision_id"]] = r["vote"]
    # >2 reviewers: not "insufficient data", it's "wrong tool". Returning
    # None here would silently persist NULL kappa for a codelist that
    # actually has full vote data, hiding the data-quality issue. The
    # n=3+ scope is explicit-optional in the T30 ticket; when it lands,
    # call a Fleiss-kappa helper instead and document the dispatch.
    if len(by_reviewer) > 2:
        raise ValueError(
            f"_compute_codelist_kappa expects ≤2 reviewers (Cohen's "
            f"kappa is n=2 only); codelist {cid} has {len(by_reviewer)}. "
            "Add a Fleiss-kappa path before unlocking n=3 reviewer "
            "assignments in step 5."
        )
    if len(by_reviewer) != 2:
        # 0 or 1 reviewers — insufficient data, not an error.
        return None

    a_id, b_id = sorted(by_reviewer.keys())
    common = sorted(set(by_reviewer[a_id]) & set(by_reviewer[b_id]))
    if not common:
        return None
    votes_a = [by_reviewer[a_id][d] for d in common]
    votes_b = [by_reviewer[b_id][d] for d in common]
    return cohen_kappa(votes_a, votes_b)

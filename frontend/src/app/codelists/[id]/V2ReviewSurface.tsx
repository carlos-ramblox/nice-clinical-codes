"use client";

import { useEffect, useMemo, useState } from "react";
import {
  submitV2Review,
  type CodelistDecision,
  type VotingState,
  type VoteRow,
  type VoteValue,
} from "@/lib/api";

const VOTE_LABELS: Record<VoteValue, string> = {
  include: "Include",
  exclude: "Exclude",
  uncertain: "Review",
};

const VOTE_COLORS: Record<VoteValue, string> = {
  include: "bg-green-100 text-green-800 border-green-300",
  exclude: "bg-red-100 text-red-800 border-red-300",
  uncertain: "bg-amber-100 text-amber-800 border-amber-300",
};

interface V2ReviewSurfaceProps {
  codelistId: string;
  decisions: CodelistDecision[];
  state: VotingState;
  onChanged: () => void;  // parent refetches after a successful submit
}

/**
 * v2 review surface — the per-reviewer voting table that replaces
 * the legacy single-reviewer table for codelists where
 * ``signature_version=2``.
 *
 * The reviewer sees only their own votes during in_review (the
 * Watson 2017 anchoring-bias guard). Once they finalise, the peer's
 * votes appear in a parallel column, and disputed rows pin to the
 * top with a red badge in the code column.
 */
export function V2ReviewSurface({
  codelistId,
  decisions,
  state,
  onChanged,
}: V2ReviewSurfaceProps) {
  const isReviewer = state.is_caller_a_reviewer;
  const inAdjudication = state.status === "adjudication";
  const isTerminal =
    state.status === "approved" || state.status === "rejected";
  const peerVisible = state.peer_votes !== null;

  // Local draft of caller's votes, keyed by decision_id. Initialised
  // from state.caller_votes; mutated as the reviewer clicks
  // include/exclude/uncertain. Submitted in batches.
  const [drafts, setDrafts] = useState<Record<number, VoteRow>>(() => {
    const m: Record<number, VoteRow> = {};
    for (const v of state.caller_votes) {
      m[v.decision_id] = { ...v };
    }
    return m;
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If the parent passes a fresh state (after a refetch), re-seed
  // drafts from it so cached UI doesn't drift from server truth.
  useEffect(() => {
    setDrafts(() => {
      const m: Record<number, VoteRow> = {};
      for (const v of state.caller_votes) {
        m[v.decision_id] = { ...v };
      }
      return m;
    });
  }, [state.caller_votes]);

  const peerVoteByDid = useMemo(() => {
    const m: Record<number, VoteValue> = {};
    if (state.peer_votes) {
      for (const v of state.peer_votes) m[v.decision_id] = v.vote;
    }
    return m;
  }, [state.peer_votes]);

  const disputedSet = useMemo(
    () => new Set(state.disputed_decision_ids),
    [state.disputed_decision_ids],
  );

  // Sort: disputed first (only meaningful in adjudication), then by
  // code. Pinning surfaces disagreements without hiding the rest of
  // the artefact (per spec — no filter toggle in v1).
  const sortedDecisions = useMemo(() => {
    const arr = [...decisions];
    arr.sort((a, b) => {
      if (inAdjudication) {
        const aDisp = disputedSet.has(a.id) ? 0 : 1;
        const bDisp = disputedSet.has(b.id) ? 0 : 1;
        if (aDisp !== bDisp) return aDisp - bDisp;
      }
      return a.code.localeCompare(b.code);
    });
    return arr;
  }, [decisions, disputedSet, inAdjudication]);

  // Caller's votes are locked once finalised. The submit button
  // also flips off, but we additionally disable the radio buttons
  // so the UI matches the API contract visually.
  const votesLocked = state.caller_finalised || !isReviewer || isTerminal;

  const setVote = (decisionId: number, vote: VoteValue) => {
    setDrafts((prev) => ({
      ...prev,
      [decisionId]: {
        decision_id: decisionId,
        vote,
        comment: prev[decisionId]?.comment ?? null,
      },
    }));
  };

  const draftCount = Object.keys(drafts).length;
  const allVoted = draftCount === decisions.length;
  const dirty = useMemo(() => {
    // Compare drafts to state.caller_votes — if any vote changed or
    // if we have new votes not in the server state, dirty=true.
    if (Object.keys(drafts).length !== state.caller_votes.length) return true;
    const serverByDid: Record<number, VoteValue> = {};
    for (const v of state.caller_votes) serverByDid[v.decision_id] = v.vote;
    for (const did of Object.keys(drafts)) {
      const k = Number(did);
      if (drafts[k].vote !== serverByDid[k]) return true;
    }
    return false;
  }, [drafts, state.caller_votes]);

  const submit = async (isFinal: boolean) => {
    if (submitting) return;
    if (isFinal && !allVoted) {
      setError(
        `Cannot finalise: ${decisions.length - draftCount} decision(s) without a vote.`,
      );
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const votes = Object.values(drafts).map((v) => ({
        decision_id: v.decision_id,
        vote: v.vote,
        comment: v.comment ?? null,
      }));
      await submitV2Review(codelistId, votes, isFinal);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  };

  return (
    <>
      {/* Independent-voting banner — Watson 2017 anchoring-bias
          messaging. Only relevant during in_review when the caller
          is a reviewer and hasn't yet finalised. */}
      {isReviewer && state.status === "in_review" && !state.caller_finalised && (
        <div className="mb-3 px-3 py-2 bg-blue-50 border border-blue-200 rounded text-xs text-blue-900">
          <strong>Voting independently.</strong>{" "}
          {state.peer_name ?? "The other reviewer"}&apos;s votes will be
          visible after you finalise.
        </div>
      )}
      {isReviewer && state.caller_finalised && state.status === "in_review" && (
        <div className="mb-3 px-3 py-2 bg-blue-50 border border-blue-200 rounded text-xs text-blue-900">
          <strong>Independent voting complete.</strong>{" "}
          {state.peer_name ?? "The other reviewer"}{" "}
          {state.peer_finalised ? "has finalised" : "has not yet finalised"}.
        </div>
      )}

      <div className="border border-gray-200 rounded overflow-hidden mb-6">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-600">
            <tr>
              <th scope="col" className="px-3 py-2">Code</th>
              <th scope="col" className="px-3 py-2">Term</th>
              <th scope="col" className="px-3 py-2">AI</th>
              <th scope="col" className="px-3 py-2">Your vote</th>
              {peerVisible && (
                <th scope="col" className="px-3 py-2">
                  {state.peer_name ?? "Peer"}
                </th>
              )}
              <th scope="col" className="px-3 py-2">Rationale</th>
            </tr>
          </thead>
          <tbody>
            {sortedDecisions.map((d) => {
              const isDisputed = inAdjudication && disputedSet.has(d.id);
              const callerVote = drafts[d.id]?.vote;
              const peerVote = peerVisible ? peerVoteByDid[d.id] : undefined;
              return (
                <tr
                  key={d.id}
                  className={`border-t border-gray-100 align-top ${
                    isDisputed ? "border-l-4 border-l-red-500 bg-red-50/40" : ""
                  }`}
                >
                  <td className="px-3 py-2 font-mono text-xs">
                    <div className="flex items-center gap-1.5">
                      {isDisputed && (
                        <span className="inline-block text-[10px] uppercase font-semibold tracking-wide bg-red-100 text-red-800 border border-red-300 px-1.5 py-0.5 rounded">
                          Disputed
                        </span>
                      )}
                      <span>{d.code}</span>
                    </div>
                    <div className="text-[10px] text-gray-500 mt-0.5">
                      {d.vocabulary}
                      {d.is_umls_suggestion ? " · UMLS" : ""}
                    </div>
                  </td>
                  <td className="px-3 py-2">{d.term}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block text-xs px-2 py-0.5 rounded border ${VOTE_COLORS[d.ai_decision]}`}
                    >
                      {VOTE_LABELS[d.ai_decision]}
                    </span>
                  </td>
                  {/* Your vote — buttons during voting; pill when locked. */}
                  <td className="px-3 py-2">
                    {votesLocked ? (
                      callerVote ? (
                        <span
                          className={`inline-block text-xs px-2 py-0.5 rounded border ${VOTE_COLORS[callerVote]}`}
                        >
                          {VOTE_LABELS[callerVote]}
                        </span>
                      ) : (
                        <span className="text-xs text-gray-400">—</span>
                      )
                    ) : (
                      <div className="flex gap-1">
                        {(["include", "exclude", "uncertain"] as const).map((opt) => (
                          <button
                            key={opt}
                            type="button"
                            onClick={() => setVote(d.id, opt)}
                            className={`px-2 py-1 text-xs rounded border ${
                              callerVote === opt
                                ? VOTE_COLORS[opt] + " font-semibold"
                                : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
                            }`}
                          >
                            {VOTE_LABELS[opt]}
                          </button>
                        ))}
                      </div>
                    )}
                  </td>
                  {/* Peer's vote — visible only post-self-finalisation. */}
                  {peerVisible && (
                    <td className="px-3 py-2">
                      {peerVote ? (
                        <span
                          className={`inline-block text-xs px-2 py-0.5 rounded border ${VOTE_COLORS[peerVote]}`}
                        >
                          {VOTE_LABELS[peerVote]}
                        </span>
                      ) : (
                        <span className="text-xs text-gray-400">—</span>
                      )}
                    </td>
                  )}
                  <td className="px-3 py-2 text-xs text-gray-700">
                    <div className="italic text-gray-500">
                      AI: {d.ai_rationale || "—"}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Submit / finalise controls. Only shown when the caller is a
          non-finalised reviewer in in_review. Adjudication uses the
          ConsensusForm separately; terminal states have no controls. */}
      {isReviewer &&
        state.status === "in_review" &&
        !state.caller_finalised && (
          <div className="border border-gray-200 rounded p-4 bg-gray-50 mb-4">
            <div className="text-xs text-gray-700 mb-3">
              {draftCount}/{decisions.length} decision
              {decisions.length === 1 ? "" : "s"} voted.
              {!allVoted && (
                <span className="ml-1 text-amber-800">
                  Vote on every decision before finalising.
                </span>
              )}
            </div>
            {error && (
              <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 mb-3">
                {error}
              </div>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => submit(false)}
                disabled={submitting || !dirty}
                className="px-4 py-1.5 bg-white text-gray-700 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                Save votes
              </button>
              <button
                type="button"
                onClick={() => submit(true)}
                disabled={submitting || !allVoted}
                title={
                  !allVoted
                    ? "Vote on every decision before finalising"
                    : "Lock your votes; status auto-advances when both reviewers finalise"
                }
                className="px-4 py-1.5 bg-[#00436C] text-white text-sm font-medium rounded hover:bg-[#005EA5] disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {submitting ? "Submitting…" : "Finalise my votes"}
              </button>
            </div>
          </div>
        )}
    </>
  );
}

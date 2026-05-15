"use client";

import { useMemo, useState } from "react";
import {
  submitConsensus,
  type CodelistDecision,
  type ConsensusResolution,
  type VotingState,
  type VoteValue,
} from "@/lib/api";

const VOTE_LABELS: Record<VoteValue, string> = {
  include: "Include",
  exclude: "Exclude",
  uncertain: "Review",
};

interface ConsensusFormProps {
  codelistId: string;
  decisions: CodelistDecision[];  // codelist's full decision list
  state: VotingState;
  onResolved: () => void;  // parent refetches after acknowledge=true success
}

interface DraftResolution {
  final_decision: VoteValue;
  rationale: string;
  // Tracks whether the row was modified by the local reviewer (used
  // to grey out the Acknowledge button; client-side guidance only,
  // server still byte-equality-validates).
  touched: boolean;
}

/**
 * Adjudication-state consensus UI. Two-phase: propose
 * (acknowledge=false) and acknowledge (acknowledge=true). Required
 * resolutions cover every disputed decision; an expander reveals
 * non-disputed rows so the reviewer can re-resolve them per
 * Delphi practice (Watson 2017: discussion may overturn earlier
 * unanimous agreement).
 *
 * The Acknowledge button greys out the moment the reviewer edits
 * any field — the server still validates byte-equality with the
 * prior proposal, so the client greying is guidance, not a
 * trust boundary.
 */
export function ConsensusForm({
  codelistId,
  decisions,
  state,
  onResolved,
}: ConsensusFormProps) {
  const isReviewer = state.is_caller_a_reviewer;
  const proposal = state.proposed_consensus;
  const callerHasProposed =
    proposal !== null && proposal.proposer_id === state.caller_id;

  // Initialise draft state. If a prior proposal exists, seed
  // from it (lets the caller "Acknowledge" without re-typing).
  // Otherwise, seed from the current decisions' human_decision.
  const initial = useMemo(() => {
    const map: Record<number, DraftResolution> = {};
    if (proposal) {
      for (const r of proposal.resolutions) {
        map[r.decision_id] = {
          final_decision: r.final_decision,
          rationale: r.rationale,
          touched: false,
        };
      }
    }
    for (const d of decisions) {
      if (!(d.id in map)) {
        map[d.id] = {
          final_decision: d.human_decision,
          rationale: "",
          touched: false,
        };
      }
    }
    return map;
  }, [decisions, proposal]);

  const [drafts, setDrafts] = useState<Record<number, DraftResolution>>(initial);
  const [showOptional, setShowOptional] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const disputedSet = useMemo(
    () => new Set(state.disputed_decision_ids),
    [state.disputed_decision_ids],
  );

  // Decisions split into disputed (always shown, required) and
  // non-disputed (optional, behind expander).
  const disputed = decisions.filter((d) => disputedSet.has(d.id));
  const nonDisputed = decisions.filter((d) => !disputedSet.has(d.id));

  const setField = (
    decisionId: number,
    field: "final_decision" | "rationale",
    value: string,
  ) => {
    setDrafts((prev) => ({
      ...prev,
      [decisionId]: {
        ...prev[decisionId],
        [field]: value,
        touched: true,
      },
    }));
  };

  const buildResolutions = (): ConsensusResolution[] => {
    const includeNonDisputed = showOptional;
    const out: ConsensusResolution[] = [];
    for (const d of decisions) {
      const draft = drafts[d.id];
      if (!draft) continue;
      const isDisputed = disputedSet.has(d.id);
      // Always include disputed; include non-disputed only when the
      // reviewer has the optional panel open AND has touched the row
      // (so re-resolving stays explicit, never accidental).
      if (isDisputed || (includeNonDisputed && draft.touched)) {
        out.push({
          decision_id: d.id,
          final_decision: draft.final_decision,
          rationale: draft.rationale.trim(),
        });
      }
    }
    return out;
  };

  // Validation: every disputed row must have a non-empty rationale;
  // every included non-disputed row likewise.
  const invalidIds = useMemo(() => {
    const out: number[] = [];
    for (const d of decisions) {
      const draft = drafts[d.id];
      if (!draft) continue;
      const isDisputed = disputedSet.has(d.id);
      const willInclude =
        isDisputed || (showOptional && draft.touched);
      if (willInclude && !draft.rationale.trim()) {
        out.push(d.id);
      }
    }
    return out;
  }, [decisions, drafts, disputedSet, showOptional]);

  // Acknowledge-eligibility: a non-proposing reviewer can ACK only
  // if their resolution set byte-equals the proposal. Client check
  // is exact-match per (decision_id, final_decision, rationale)
  // — the server runs the canonical comparison too.
  const canAcknowledge = useMemo(() => {
    if (!proposal || callerHasProposed) return false;
    if (!isReviewer) return false;
    const built = buildResolutions();
    if (built.length !== proposal.resolutions.length) return false;
    const byId: Record<number, ConsensusResolution> = {};
    for (const r of built) byId[r.decision_id] = r;
    for (const r of proposal.resolutions) {
      const local = byId[r.decision_id];
      if (!local) return false;
      if (
        local.final_decision !== r.final_decision ||
        local.rationale.trim() !== r.rationale.trim()
      ) {
        return false;
      }
    }
    return true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drafts, proposal, callerHasProposed, isReviewer]);

  const decisionByCode: Record<number, CodelistDecision> = useMemo(() => {
    const m: Record<number, CodelistDecision> = {};
    for (const d of decisions) m[d.id] = d;
    return m;
  }, [decisions]);

  const handleSubmit = async (acknowledge: boolean) => {
    if (submitting) return;
    if (invalidIds.length > 0) {
      setError(
        `Missing rationale for decision${invalidIds.length === 1 ? "" : "s"}: ${invalidIds.join(", ")}`,
      );
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await submitConsensus(codelistId, buildResolutions(), acknowledge);
      onResolved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  };

  if (!isReviewer) {
    // Creator monitoring; can see the proposal but cannot interact.
    if (proposal === null) {
      return (
        <section className="mb-4 border border-gray-200 bg-white px-4 py-3">
          <h3 className="text-sm font-medium text-[#00436C] mb-1">
            Adjudication in progress
          </h3>
          <p className="text-xs text-gray-600">
            Reviewers are working through {disputed.length} disagreement
            {disputed.length === 1 ? "" : "s"}. No proposal has been
            submitted yet.
          </p>
        </section>
      );
    }
    return (
      <section className="mb-4 border border-gray-200 bg-white px-4 py-3">
        <h3 className="text-sm font-medium text-[#00436C] mb-1">
          Consensus proposal under review
        </h3>
        <p className="text-xs text-gray-600 mb-2">
          Proposed by <strong>{proposal.proposer_name}</strong>; awaiting
          the other reviewer&apos;s acknowledgement.
        </p>
        <ul className="text-xs text-gray-700 space-y-1">
          {proposal.resolutions.map((r) => {
            const d = decisionByCode[r.decision_id];
            return (
              <li key={r.decision_id} className="border-l-2 border-gray-300 pl-2">
                <span className="font-mono">{d?.code ?? `id ${r.decision_id}`}</span>
                {" → "}
                <strong>{VOTE_LABELS[r.final_decision]}</strong>
                <div className="text-gray-500 italic">{r.rationale}</div>
              </li>
            );
          })}
        </ul>
      </section>
    );
  }

  return (
    <section className="mb-4 border border-gray-200 bg-white px-4 py-3">
      <h3 className="text-sm font-medium text-[#00436C] mb-1">
        Consensus
      </h3>
      <p className="text-xs text-gray-500 mb-3">
        Resolve every disputed decision below with a final value and a
        rationale. The other reviewer must acknowledge your proposal as
        you wrote it; if they want a different resolution they
        counter-propose.
      </p>

      {proposal && !callerHasProposed && (
        <div className="mb-3 px-3 py-2 bg-blue-50 border border-blue-200 rounded text-xs text-blue-900">
          <strong>{proposal.proposer_name}</strong> has proposed a resolution.
          Acknowledge to approve, or edit any field to counter-propose.
        </div>
      )}
      {callerHasProposed && (
        <div className="mb-3 px-3 py-2 bg-blue-50 border border-blue-200 rounded text-xs text-blue-900">
          Your proposal is awaiting the other reviewer&apos;s acknowledgement.
          You can also counter-propose by editing fields and submitting again.
        </div>
      )}

      {disputed.length === 0 ? (
        <p className="text-xs text-gray-600">
          No disagreements were detected at finalisation. Refresh the page
          if you see this — the codelist may have just changed state.
        </p>
      ) : (
        <div className="space-y-3">
          {disputed.map((d) => {
            const draft = drafts[d.id];
            const peerVote = state.peer_votes?.find(
              (v) => v.decision_id === d.id,
            );
            const callerVote = state.caller_votes.find(
              (v) => v.decision_id === d.id,
            );
            const needsRationale =
              !draft?.rationale.trim() && invalidIds.includes(d.id);
            return (
              <div
                key={d.id}
                className="border border-gray-200 rounded p-3 bg-gray-50"
              >
                <div className="flex items-baseline justify-between mb-2">
                  <div>
                    <span className="font-mono text-sm">{d.code}</span>
                    <span className="ml-2 text-xs text-gray-500">
                      {d.vocabulary} · {d.term}
                    </span>
                  </div>
                  <div className="text-[11px] text-gray-600">
                    {callerVote && (
                      <span className="mr-3">
                        You voted: <strong>{VOTE_LABELS[callerVote.vote]}</strong>
                      </span>
                    )}
                    {peerVote && (
                      <span>
                        {state.peer_name ?? "Peer"} voted:{" "}
                        <strong>{VOTE_LABELS[peerVote.vote]}</strong>
                      </span>
                    )}
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                  <label className="text-xs">
                    <span className="block text-gray-700 mb-1">
                      Final decision
                    </span>
                    <select
                      value={draft?.final_decision ?? "uncertain"}
                      onChange={(e) =>
                        setField(d.id, "final_decision", e.target.value)
                      }
                      className="w-full px-2 py-1 border border-gray-300 rounded text-sm"
                    >
                      <option value="include">Include</option>
                      <option value="exclude">Exclude</option>
                      <option value="uncertain">Review (uncertain)</option>
                    </select>
                  </label>
                  <label className="text-xs md:col-span-2">
                    <span className="block text-gray-700 mb-1">
                      Rationale (required)
                    </span>
                    <textarea
                      value={draft?.rationale ?? ""}
                      onChange={(e) =>
                        setField(d.id, "rationale", e.target.value)
                      }
                      rows={2}
                      placeholder="Why is this the right resolution?"
                      className={`w-full px-2 py-1 border rounded text-xs ${
                        needsRationale
                          ? "border-red-400 bg-red-50"
                          : "border-gray-300"
                      }`}
                    />
                  </label>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Optional re-resolution of unanimous decisions. Collapsed by
          default per the spec — Delphi allows it but it should be a
          deliberate action, not the obvious default. */}
      {nonDisputed.length > 0 && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setShowOptional((v) => !v)}
            className="text-xs text-[#005EA5] hover:underline"
          >
            {showOptional ? "▾" : "▸"} Re-resolve unanimous decisions ({nonDisputed.length})
          </button>
          {showOptional && (
            <div className="mt-2 space-y-2">
              <p className="text-[11px] text-gray-500">
                Discussion may overturn earlier unanimous agreement
                (Watson 2017). Editing a row here counts as a
                re-resolution — both reviewers must acknowledge the change.
              </p>
              {nonDisputed.map((d) => {
                const draft = drafts[d.id];
                const needsRationale =
                  draft?.touched &&
                  !draft.rationale.trim() &&
                  invalidIds.includes(d.id);
                return (
                  <div
                    key={d.id}
                    className="border border-gray-200 rounded p-2 bg-white text-xs"
                  >
                    <div className="flex items-baseline justify-between">
                      <span className="font-mono">{d.code}</span>
                      <span className="text-gray-500">
                        {d.vocabulary} · current: {VOTE_LABELS[d.human_decision]}
                      </span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-2">
                      <select
                        value={draft?.final_decision ?? d.human_decision}
                        onChange={(e) =>
                          setField(d.id, "final_decision", e.target.value)
                        }
                        className="px-2 py-1 border border-gray-300 rounded text-xs"
                      >
                        <option value="include">Include</option>
                        <option value="exclude">Exclude</option>
                        <option value="uncertain">Review (uncertain)</option>
                      </select>
                      <textarea
                        value={draft?.rationale ?? ""}
                        onChange={(e) =>
                          setField(d.id, "rationale", e.target.value)
                        }
                        rows={1}
                        placeholder="Rationale (required if you re-resolve)"
                        className={`md:col-span-2 px-2 py-1 border rounded text-xs ${
                          needsRationale
                            ? "border-red-400 bg-red-50"
                            : "border-gray-300"
                        }`}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="mt-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
          {error}
        </div>
      )}
      <div className="mt-4 flex gap-2">
        {/* Acknowledge button: only enabled when a prior proposal exists
            from the OTHER reviewer AND the caller's resolutions match
            byte-for-byte. Server still validates. */}
        {proposal !== null && !callerHasProposed && (
          <button
            type="button"
            onClick={() => handleSubmit(true)}
            disabled={!canAcknowledge || submitting || invalidIds.length > 0}
            title={
              !canAcknowledge
                ? "Your resolutions must match the prior proposal exactly to acknowledge it"
                : "Acknowledge the proposal — the codelist is approved on submit"
            }
            className="px-4 py-1.5 bg-green-700 text-white text-sm font-medium rounded hover:bg-green-800 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? "Acknowledging…" : "Acknowledge"}
          </button>
        )}
        <button
          type="button"
          onClick={() => handleSubmit(false)}
          disabled={submitting || invalidIds.length > 0}
          className="px-4 py-1.5 bg-[#00436C] text-white text-sm font-medium rounded hover:bg-[#005EA5] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {submitting
            ? "Submitting…"
            : proposal === null
            ? "Propose consensus"
            : "Counter-propose"}
        </button>
      </div>
    </section>
  );
}

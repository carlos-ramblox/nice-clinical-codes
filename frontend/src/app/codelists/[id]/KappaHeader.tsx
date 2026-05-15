"use client";

import type { CodelistStatus, VotingState } from "@/lib/api";

// Landis & Koch (1977) qualitative bands. Mirrors
// backend/app/services/agreement.py::landis_koch_label so the UI
// shows the same labels the audit-log and any methods-paper
// downstream would. Keeping the table in the frontend (vs always
// surfacing the label from the API) lets the kappa header re-render
// instantly as the value changes; the values are stable per the
// 1977 paper anyway.
function landisKochLabel(kappa: number | null): string {
  if (kappa === null || Number.isNaN(kappa)) return "n/a";
  if (kappa < 0) return "poor";
  if (kappa < 0.21) return "slight";
  if (kappa < 0.41) return "fair";
  if (kappa < 0.61) return "moderate";
  if (kappa < 0.81) return "substantial";
  return "almost perfect";
}

// 0.41 is the user-pinned amber-warning threshold (anything below
// "moderate" surfaces a Watson 2017 Stage 3 warning per the persona
// audit). The strip styles itself accordingly.
function bandTone(kappa: number | null): "neutral" | "amber" | "ok" {
  if (kappa === null || Number.isNaN(kappa)) return "neutral";
  if (kappa < 0.41) return "amber";
  return "ok";
}

// Three-band agreement bar — fills left-to-right, coloured per
// band. Visual intent: the reviewer can tell from a glance whether
// kappa sits in the "discuss before consensus" zone vs the
// "expected, no special action" zone vs "high agreement". Numbers
// stay on the left of the strip; this is the at-a-glance signal.
function AgreementBar({ kappa }: { kappa: number | null }) {
  if (kappa === null || Number.isNaN(kappa)) {
    return (
      <div
        className="h-2 w-full bg-gray-200 rounded"
        aria-label="agreement bar pending"
      />
    );
  }
  // Map [-1, 1] → [0, 100]%. Negative kappas still render
  // (they're real values; we want the reviewer to see the bar
  // sitting near zero rather than empty).
  const pct = Math.max(0, Math.min(100, ((kappa + 1) / 2) * 100));
  const tone = bandTone(kappa);
  const fillColor =
    tone === "amber"
      ? "bg-amber-500"
      : kappa < 0.61
      ? "bg-green-400"
      : "bg-green-600";
  return (
    <div
      className="h-2 w-full bg-gray-200 rounded relative overflow-hidden"
      role="progressbar"
      aria-valuemin={-1}
      aria-valuemax={1}
      aria-valuenow={kappa}
    >
      <div
        className={`h-full ${fillColor}`}
        style={{ width: `${pct}%` }}
      />
      {/* 0.41 threshold tick — marks the amber/non-amber boundary */}
      <div
        className="absolute inset-y-0 w-px bg-gray-500"
        style={{ left: `${((0.41 + 1) / 2) * 100}%` }}
        aria-hidden="true"
      />
    </div>
  );
}

interface KappaHeaderProps {
  status: CodelistStatus;
  state: VotingState;
}

/**
 * Pre-decisions-table strip showing kappa value, per-reviewer
 * finalisation status, and the agreement bar.
 *
 * Visibility per status (T30 step-6 spec, item 1):
 * - draft: hidden (no reviewers yet → no kappa concept).
 * - in_review / adjudication / approved / rejected: shown.
 *
 * Pre-finalisation (in_review with at least one reviewer not
 * finalised), kappa is "—" with a "Pending until both reviewers
 * finalise" subtitle — never a placeholder number.
 *
 * Below-threshold (κ < 0.41) renders the strip with an amber
 * left-border and a one-line caption. Below-chance (κ < 0) uses
 * stronger copy. Both are informational, not gating — Landis–Koch
 * is descriptive, and gating approval at a kappa threshold would
 * be a clinical-safety policy decision we haven't made.
 */
export function KappaHeader({ status, state }: KappaHeaderProps) {
  if (status === "draft") return null;

  const kappa = state.agreement_kappa;
  const tone = bandTone(kappa);
  const label = landisKochLabel(kappa);
  const bothFinalised = state.caller_finalised && state.peer_finalised;
  const finalisedCount =
    (state.caller_finalised ? 1 : 0) + (state.peer_finalised ? 1 : 0);
  const totalReviewers = state.reviewer_ids.length;
  const disputeCount = state.disputed_decision_ids.length;

  // Border-left treatment per band. Amber for the warning band,
  // gray for "still computing" / unflagged.
  const borderClass =
    tone === "amber"
      ? "border-l-4 border-amber-500"
      : "border-l-4 border-transparent";

  return (
    <section
      className={`mb-4 border border-gray-200 ${borderClass} bg-white px-4 py-3`}
      aria-label="Reviewer agreement"
    >
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-center">
        {/* Left: kappa value + Landis-Koch label */}
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wide">
            Cohen&apos;s κ
          </div>
          <div className="flex items-baseline gap-2 mt-0.5">
            <span className="text-2xl font-mono text-[#00436C] tabular-nums">
              {kappa === null || Number.isNaN(kappa)
                ? "—"
                : kappa.toFixed(3)}
            </span>
            <span className="text-sm text-gray-700">{label}</span>
          </div>
          {kappa === null && (
            <div className="text-[11px] text-gray-500 mt-0.5">
              Pending until both reviewers finalise.
            </div>
          )}
        </div>

        {/* Middle: per-reviewer finalisation status */}
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wide">
            Reviewer status
          </div>
          <div className="text-sm text-gray-700 mt-0.5">
            {finalisedCount}/{totalReviewers} reviewer{totalReviewers === 1 ? "" : "s"} finalised
          </div>
          {status === "adjudication" && disputeCount > 0 && (
            <div className="text-[11px] text-amber-800 mt-0.5">
              {disputeCount} disagreement{disputeCount === 1 ? "" : "s"} in adjudication
            </div>
          )}
          {bothFinalised && status === "in_review" && (
            <div className="text-[11px] text-gray-500 mt-0.5">
              Computing disposition…
            </div>
          )}
        </div>

        {/* Right: agreement bar */}
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wide mb-1">
            Agreement
          </div>
          <AgreementBar kappa={kappa} />
          <div className="flex justify-between text-[10px] text-gray-400 mt-0.5">
            <span>−1</span>
            <span>0</span>
            <span>0.41</span>
            <span>1</span>
          </div>
        </div>
      </div>

      {/* Warning band caption — informational, not blocking. */}
      {kappa !== null && kappa < 0 && (
        <p className="mt-3 text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded p-2">
          Agreement is below chance (κ &lt; 0). The reviewers may be applying
          conflicting frames; reconsider the review brief before consensus.
        </p>
      )}
      {kappa !== null && kappa >= 0 && kappa < 0.41 && (
        <p className="mt-3 text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded p-2">
          Reviewer agreement is below the moderate threshold (κ &lt; 0.41).
          Consider whether further discussion is needed before consensus.
        </p>
      )}
    </section>
  );
}

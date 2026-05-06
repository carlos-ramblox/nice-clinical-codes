"use client";

import { useEffect, useState } from "react";
import { assignReviewers, listDemoUsers, type User } from "@/lib/api";

interface ReviewerAssignmentPanelProps {
  codelistId: string;
  creatorId: number;
  onAssigned: () => void;  // parent re-fetches the codelist + voting-state
}

/**
 * Creator-only panel that assigns exactly two reviewers to a draft
 * codelist, transitioning it from v1 (single-reviewer) to v2
 * (two-reviewer Delphi). Once submitted the panel is no longer
 * shown — the API rejects re-assignment after the draft → in_review
 * transition (see step 5's status guard).
 *
 * v1 of T30 caps at exactly two reviewers (Cohen's kappa is n=2);
 * the dropdowns are populated from /api/auth/users minus the
 * creator. A search-as-you-type variant is deferred — the demo has
 * ~10 users, two dropdowns are clearer than a typeahead.
 */
export function ReviewerAssignmentPanel({
  codelistId,
  creatorId,
  onAssigned,
}: ReviewerAssignmentPanelProps) {
  const [users, setUsers] = useState<User[] | null>(null);
  const [reviewerA, setReviewerA] = useState<number | "">("");
  const [reviewerB, setReviewerB] = useState<number | "">("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listDemoUsers()
      .then((list) => {
        if (!cancelled) setUsers(list.filter((u) => u.id !== creatorId));
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [creatorId]);

  const candidatesForA = users ?? [];
  const candidatesForB = (users ?? []).filter((u) => u.id !== reviewerA);

  const canSubmit =
    reviewerA !== "" && reviewerB !== "" && reviewerA !== reviewerB && !submitting;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await assignReviewers(codelistId, [
        Number(reviewerA),
        Number(reviewerB),
      ]);
      onAssigned();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  };

  return (
    <section className="mb-4 border border-gray-200 bg-white px-4 py-3">
      <h3 className="text-sm font-medium text-[#00436C] mb-1">
        Assign two reviewers
      </h3>
      <p className="text-xs text-gray-500 mb-3">
        Two reviewers vote independently. Cohen&apos;s κ is computed when
        both finalise, and disagreements go to a consensus step. The pair
        is fixed once you assign — to change reviewers you&apos;d need to
        fork the codelist.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
        <label className="text-xs">
          <span className="block text-gray-700 mb-1">Reviewer A</span>
          <select
            value={reviewerA}
            onChange={(e) =>
              setReviewerA(e.target.value === "" ? "" : Number(e.target.value))
            }
            disabled={users === null || submitting}
            className="w-full px-2 py-1 border border-gray-300 rounded text-sm"
          >
            <option value="">— select —</option>
            {candidatesForA.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          <span className="block text-gray-700 mb-1">Reviewer B</span>
          <select
            value={reviewerB}
            onChange={(e) =>
              setReviewerB(e.target.value === "" ? "" : Number(e.target.value))
            }
            disabled={users === null || submitting || reviewerA === ""}
            className="w-full px-2 py-1 border border-gray-300 rounded text-sm"
          >
            <option value="">— select —</option>
            {candidatesForB.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && (
        <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 mb-2">
          {error}
        </div>
      )}
      <button
        onClick={handleSubmit}
        disabled={!canSubmit}
        className="px-4 py-1.5 bg-[#00436C] text-white text-sm font-medium rounded hover:bg-[#005EA5] disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {submitting ? "Assigning…" : "Assign and start review"}
      </button>
      <p className="mt-2 text-[11px] text-gray-400">
        Submitting moves the codelist to <code>in_review</code>.
      </p>
    </section>
  );
}

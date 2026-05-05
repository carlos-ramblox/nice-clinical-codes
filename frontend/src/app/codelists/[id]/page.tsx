"use client";

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  getCodelist,
  getCrossReference,
  submitReview,
  type Codelist,
  type CodelistDecision,
  type CrossReferenceRow,
  type ReviewDecisionInput,
} from "@/lib/api";
import { useUser } from "@/lib/useUser";
import { ConfirmModal } from "../../ConfirmModal";

type HumanDecision = "include" | "exclude" | "uncertain";

// Server returns decisions in uncertainty-sampling order (Settles 2009):
// LLM-flagged `uncertain` first, then ascending |2*confidence - 1|. The
// reviewer can override that with the column-header sort below.
type SortMode = "uncertainty" | "code" | "decision" | "confidence";

interface DraftState {
  human_decision: HumanDecision;
  override_comment: string;
}

const decisionLabel: Record<HumanDecision, string> = {
  include: "Include",
  exclude: "Exclude",
  uncertain: "Review",
};
const decisionColor: Record<HumanDecision, string> = {
  include: "bg-green-100 text-green-800 border-green-300",
  exclude: "bg-red-100 text-red-800 border-red-300",
  uncertain: "bg-amber-100 text-amber-800 border-amber-300",
};

// Persona pre-flight expectation #4 named a 5% Jaccard threshold below
// which the panel should explicitly warn the user that no candidate
// phenotype meaningfully overlaps. The pre-flight itself flagged the
// number as a guess that should be calibrated against a sample of
// methods-paper citations once usage data exists; until then this
// constant is the single source of truth so the calibration is a
// one-line change.
const LOW_OVERLAP_JACCARD_THRESHOLD = 0.05;

function CrossReferencePanel({
  codelistId,
  codelistQuery,
}: {
  codelistId: string;
  codelistQuery: string;
}) {
  // Read-mode validation: rank up to 5 HDR UK phenotypes by code-set
  // overlap with this codelist's *included* codes. Default collapsed
  // because the underlying request makes one Haiku judge call + up to
  // 5 HDR UK codelist fetches; we don't want to pay that on every
  // page load. Both layers cache (5-min discovery cache + 7-day
  // per-phenotype file cache) so repeat expands are essentially free.
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<CrossReferenceRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (refresh: boolean = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getCrossReference(codelistId, refresh);
      setRows(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cross-reference failed");
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    if (next && rows == null && !loading) {
      load(false);
    }
  };

  const formatPct = (n: number) => `${(n * 100).toFixed(0)}%`;

  return (
    <section className="mb-4 border border-gray-200 bg-white">
      <button
        type="button"
        onClick={handleToggle}
        aria-expanded={open}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-gray-50"
      >
        <span className="text-sm font-medium text-[#00436C]">
          Cross-reference with HDR UK Phenotype Library
          {rows != null && rows.length > 0 && (
            <span className="ml-2 text-xs text-gray-500 font-normal">
              {rows.length} match{rows.length === 1 ? "" : "es"}
            </span>
          )}
        </span>
        <span className="text-xs text-gray-500">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="border-t border-gray-200 px-4 py-3">
          {loading && (
            <p className="text-xs text-gray-500">Computing overlap…</p>
          )}
          {error && (
            <p className="text-xs text-red-600">Cross-reference failed: {error}</p>
          )}
          {!loading && !error && rows != null && rows.length === 0 && (
            <p className="text-xs text-gray-600">
              No published HDR UK phenotype meaningfully overlaps with this codelist
              — re-examine before publishing, or{" "}
              <Link
                href={`/?q=${encodeURIComponent(codelistQuery)}`}
                className="text-[#005EA5] hover:underline"
              >
                browse candidate phenotypes for this query
              </Link>
              {" "}on the search page.
            </p>
          )}
          {!loading && rows != null && rows.length > 0 && (
            <>
              {/* Persona pre-flight flag #4: if no candidate reaches a
                  material overlap threshold the panel should say so
                  explicitly. We keep all rows visible for transparency
                  but prepend the warning so the user does not mis-read
                  low-overlap results as a clean match. */}
              {Math.max(...rows.map((r) => r.overlap_jaccard)) < LOW_OVERLAP_JACCARD_THRESHOLD && (
                <p className="mb-3 px-3 py-2 bg-amber-50 border border-amber-200 text-xs text-amber-900">
                  Low overlap with all candidate phenotypes — re-examine before publishing.
                  The rows below are surfaced for transparency, not as endorsements.
                </p>
              )}
              <ul className="divide-y divide-gray-100">
                {rows.map((row) => (
                  <li key={row.phenotype_id} className="py-2.5">
                    <div className="flex items-baseline justify-between gap-3">
                      <a
                        href={row.hdruk_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[#005EA5] hover:underline font-medium text-sm"
                      >
                        {row.name}{" "}
                        <span className="text-gray-400 font-normal">({row.phenotype_id})</span>
                      </a>
                      <span className="shrink-0 text-sm font-mono text-[#00436C]">
                        {formatPct(row.overlap_jaccard)} Jaccard
                      </span>
                    </div>
                    {row.relevance_rationale && (
                      <p className="mt-1 text-xs text-gray-600 italic">
                        {row.relevance_rationale}
                      </p>
                    )}
                    <details className="mt-1 text-xs text-gray-700">
                      <summary className="cursor-pointer text-gray-500 hover:text-gray-700">
                        Show breakdown
                      </summary>
                      <div className="mt-1 pl-3 space-y-0.5 text-gray-600">
                        <div>
                          {formatPct(row.overlap_generated_in_phenotype)} of this codelist&apos;s{" "}
                          {row.n_generated_codes} included codes appear in the phenotype
                        </div>
                        <div>
                          {formatPct(row.overlap_phenotype_in_generated)} of the phenotype&apos;s{" "}
                          {row.n_phenotype_codes} codes appear in this codelist
                        </div>
                        <div className="text-gray-400">
                          intersection: {row.n_intersection} codes
                        </div>
                        {row.data_sources.length > 0 && (
                          <div className="text-gray-400">
                            data sources: {row.data_sources.slice(0, 4).join(", ")}
                          </div>
                        )}
                        {row.first_publication && (
                          <div className="mt-1 text-gray-500">
                            <span className="text-gray-400">cite: </span>
                            {row.first_publication}
                          </div>
                        )}
                      </div>
                    </details>
                  </li>
                ))}
              </ul>
              <div className="mt-2 pt-2 border-t border-gray-100 flex items-center justify-between">
                <span className="text-[11px] text-gray-400">
                  Read-only validation. Click a phenotype to view it on the HDR UK Phenotype Library.
                </span>
                <button
                  type="button"
                  onClick={() => load(true)}
                  disabled={loading}
                  className="text-xs text-[#005EA5] hover:underline disabled:opacity-50"
                >
                  Refresh
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}


function SortableTh({
  label,
  mode,
  active,
  onClick,
  title,
}: {
  label: string;
  mode: SortMode;
  active: SortMode;
  onClick: (m: SortMode) => void;
  title?: string;
}) {
  const isActive = active === mode;
  return (
    <th
      scope="col"
      className="px-3 py-2"
      aria-sort={isActive ? "ascending" : "none"}
    >
      <button
        type="button"
        onClick={() => onClick(mode)}
        title={title}
        className={`tracking-wide ${
          isActive ? "text-[#00436C] font-semibold" : "text-gray-600 hover:text-[#00436C]"
        }`}
      >
        {label}
        <span className="ml-1 text-[10px]" aria-hidden="true">
          {isActive ? "↑" : "↕"}
        </span>
      </button>
    </th>
  );
}

export default function CodelistReviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { user, loading: userLoading } = useUser();
  const router = useRouter();

  const [codelist, setCodelist] = useState<Codelist | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // local draft of the reviewer's decisions, keyed by decision id
  const [drafts, setDrafts] = useState<Record<number, DraftState>>({});
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [confirmAction, setConfirmAction] = useState<"approve" | "reject" | null>(null);
  const [filter, setFilter] = useState<"all" | HumanDecision>("all");
  const [sortMode, setSortMode] = useState<SortMode>("uncertainty");

  useEffect(() => {
    if (!userLoading && !user) {
      router.push(`/login?next=/codelists/${id}`);
    }
  }, [userLoading, user, router, id]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    setLoading(true);
    getCodelist(id)
      .then((cl) => {
        if (cancelled) return;
        setCodelist(cl);
        const init: Record<number, DraftState> = {};
        for (const d of cl.decisions) {
          init[d.id] = {
            human_decision: d.human_decision,
            override_comment: d.override_comment ?? "",
          };
        }
        setDrafts(init);
      })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [user, id]);

  // Warn before leaving with unsaved overrides
  useEffect(() => {
    if (!codelist) return;
    const dirty = codelist.decisions.some((d) => {
      const s = drafts[d.id];
      if (!s) return false;
      return s.human_decision !== d.human_decision || s.override_comment !== (d.override_comment ?? "");
    });
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [drafts, codelist]);

  const counts = useMemo(() => {
    const c = { include: 0, exclude: 0, uncertain: 0, overrides: 0 };
    if (!codelist) return c;
    for (const d of codelist.decisions) {
      const hd = drafts[d.id]?.human_decision ?? d.human_decision;
      c[hd] += 1;
      if (hd !== d.ai_decision) c.overrides += 1;
    }
    return c;
  }, [codelist, drafts]);

  const sortedDecisions = useMemo(() => {
    if (!codelist) return [];
    // Default: trust the server's uncertainty-first order (cheaper than
    // recomputing |2c-1| client-side and keeps a single source of truth).
    if (sortMode === "uncertainty") return codelist.decisions;
    const arr = [...codelist.decisions];
    if (sortMode === "code") {
      arr.sort((a, b) => a.code.localeCompare(b.code));
    } else if (sortMode === "confidence") {
      arr.sort(
        (a, b) =>
          a.ai_confidence - b.ai_confidence || a.code.localeCompare(b.code),
      );
    } else if (sortMode === "decision") {
      // Sort against the persisted human_decision, not the reviewer's
      // in-progress draft. Two reasons: rows don't jump out from under
      // the cursor as the reviewer changes them; and the memo doesn't
      // need `drafts` as a dep, so override-comment keystrokes don't
      // trigger an O(n log n) re-sort.
      const order: Record<HumanDecision, number> = { include: 0, exclude: 1, uncertain: 2 };
      arr.sort(
        (a, b) =>
          order[a.human_decision] - order[b.human_decision]
          || a.code.localeCompare(b.code),
      );
    }
    return arr;
  }, [codelist, sortMode]);

  const filteredDecisions = useMemo(() => {
    if (filter === "all") return sortedDecisions;
    return sortedDecisions.filter(
      (d) => (drafts[d.id]?.human_decision ?? d.human_decision) === filter,
    );
  }, [sortedDecisions, drafts, filter]);

  const isTerminal =
    codelist?.status === "approved" || codelist?.status === "rejected";

  const setDecision = (d: CodelistDecision, hd: HumanDecision) => {
    setDrafts((prev) => ({
      ...prev,
      [d.id]: {
        human_decision: hd,
        override_comment: prev[d.id]?.override_comment ?? "",
      },
    }));
  };
  const setComment = (id: number, comment: string) => {
    setDrafts((prev) => ({
      ...prev,
      [id]: {
        human_decision: prev[id]?.human_decision ?? "uncertain",
        override_comment: comment,
      },
    }));
  };

  // client-side validation — override requires non-empty rationale
  const invalid = useMemo(() => {
    if (!codelist) return [];
    const errs: { code: string; reason: string }[] = [];
    for (const d of codelist.decisions) {
      const state = drafts[d.id];
      if (!state) continue;
      if (
        state.human_decision !== d.ai_decision &&
        state.override_comment.trim().length < 5
      ) {
        errs.push({
          code: d.code,
          reason: "override rationale (≥5 chars) required",
        });
      }
    }
    return errs;
  }, [codelist, drafts]);

  const submit = async (action: "approve" | "reject") => {
    if (!codelist) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload: ReviewDecisionInput[] = codelist.decisions.map((d) => {
        const state = drafts[d.id];
        return {
          id: d.id,
          human_decision: state?.human_decision ?? d.human_decision,
          override_comment: state?.override_comment?.trim() || null,
        };
      });
      await submitReview(codelist.id, payload, action, notes.trim() || null);
      router.push(`/codelists/${codelist.id}/audit`);
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
      setConfirmAction(null);
    }
  };

  if (!user || loading) return (
    <div className="max-w-6xl mx-auto px-6 py-8 animate-pulse">
      <div className="h-6 bg-gray-200 rounded w-64 mb-4" />
      <div className="h-4 bg-gray-200 rounded w-48 mb-8" />
      <div className="space-y-3">
        {[...Array(5)].map((_, i) => <div key={i} className="h-12 bg-gray-200 rounded" />)}
      </div>
    </div>
  );
  if (error && !codelist) {
    return <div className="max-w-6xl mx-auto px-6 py-8 text-sm text-red-700">{error}</div>;
  }
  if (!codelist) return null;

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div>
          <Link
            href="/codelists"
            className="text-xs text-[#00436C] hover:underline"
          >
            ← Codelists
          </Link>
          <h1 className="text-2xl font-serif font-medium text-[#00436C] mt-1">
            {codelist.name}
            <span className="ml-2 text-sm text-gray-400">v{codelist.version}</span>
          </h1>
          <p className="text-sm text-gray-600 mt-1">
            Query: <span className="font-medium">{codelist.query || "—"}</span>
          </p>
          <p className="text-xs text-gray-500 mt-1">
            Created by {codelist.created_by_name} on{" "}
            {new Date(codelist.created_at).toLocaleString()}
          </p>
        </div>
        <div className="text-right">
          <span
            className={`inline-block text-xs px-2 py-0.5 rounded border ${
              codelist.status === "approved"
                ? "bg-green-100 text-green-800 border-green-300"
                : codelist.status === "rejected"
                ? "bg-red-100 text-red-800 border-red-300"
                : "bg-gray-100 text-gray-800 border-gray-300"
            }`}
          >
            {codelist.status}
          </span>
          {isTerminal && codelist.signature_hash && (
            <div className="mt-2 text-xs font-mono text-gray-500">
              sig {codelist.signature_hash.slice(0, 16)}…
            </div>
          )}
          <Link
            href={`/codelists/${codelist.id}/audit`}
            className="block mt-2 text-xs text-[#00436C] hover:underline"
          >
            View audit log →
          </Link>
        </div>
      </div>

      {/* HDR UK cross-reference panel (T35) — read-only post-hoc validation */}
      <CrossReferencePanel
        codelistId={codelist.id}
        codelistQuery={codelist.query || codelist.name}
      />

      {/* Stats + filter */}
      <div className="flex items-center gap-4 border-y border-gray-200 py-2 text-xs mb-4">
        <span className="text-gray-500">
          {codelist.decisions.length} codes total
        </span>
        <span className="text-green-700">Include: {counts.include}</span>
        <span className="text-red-700">Exclude: {counts.exclude}</span>
        <span className="text-amber-700">Review: {counts.uncertain}</span>
        <span className="ml-auto text-gray-700">
          Overrides: <strong>{counts.overrides}</strong>
        </span>
      </div>
      <div className="flex gap-1 mb-3 text-xs">
        {(["all", "include", "exclude", "uncertain"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1 rounded ${
              filter === f
                ? "bg-[#00436C] text-white"
                : "bg-gray-100 text-gray-700 hover:bg-gray-200"
            }`}
          >
            {f === "all" ? "All" : decisionLabel[f]}
          </button>
        ))}
      </div>

      {/* Sort indicator (default order is uncertainty-first per Settles 2009) */}
      <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
        <span>
          Sort:{" "}
          <span className="font-medium text-gray-700">
            {sortMode === "uncertainty"
              ? "Uncertainty (least sure first)"
              : sortMode === "code"
              ? "Code (A→Z)"
              : sortMode === "confidence"
              ? "Confidence (low→high)"
              : "Decision (include first)"}
          </span>
        </span>
        {sortMode !== "uncertainty" && (
          <button
            type="button"
            onClick={() => setSortMode("uncertainty")}
            className="text-[#00436C] hover:underline"
          >
            Reset to uncertainty order
          </button>
        )}
      </div>

      {/* Decisions table */}
      <div className="border border-gray-200 rounded overflow-hidden mb-6">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-600">
            <tr>
              <SortableTh
                label="Code"
                mode="code"
                active={sortMode}
                onClick={setSortMode}
                title="Sort alphabetically by code."
              />
              <th scope="col" className="px-3 py-2">Term</th>
              <th scope="col" className="px-3 py-2">AI</th>
              <SortableTh
                label="Conf"
                mode="confidence"
                active={sortMode}
                onClick={setSortMode}
                title="Sort by LLM confidence ascending. Default order is uncertainty-first (|2c−1| ascending), which is the active-learning sampling rule (Settles 2009)."
              />
              <SortableTh
                label="Decision"
                mode="decision"
                active={sortMode}
                onClick={setSortMode}
                title="Sort by current decision: include → exclude → review."
              />
              <th scope="col" className="px-3 py-2">Rationale / override</th>
            </tr>
          </thead>
          <tbody>
            {filteredDecisions.map((d) => {
              const state = drafts[d.id];
              const hd = state?.human_decision ?? d.human_decision;
              const isOverride = hd !== d.ai_decision;
              const needsReason =
                isOverride && (state?.override_comment?.trim().length ?? 0) < 5;
              return (
                <tr key={d.id} className="border-t border-gray-100 align-top">
                  <td className="px-3 py-2 font-mono text-xs">
                    {d.code}
                    <div className="text-[10px] text-gray-500">
                      {d.vocabulary}
                      {d.is_umls_suggestion ? " · UMLS" : ""}
                    </div>
                  </td>
                  <td className="px-3 py-2">{d.term}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block text-xs px-2 py-0.5 rounded border ${decisionColor[d.ai_decision as HumanDecision]}`}
                    >
                      {decisionLabel[d.ai_decision as HumanDecision]}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-gray-700 tabular-nums">
                    {Math.round(d.ai_confidence * 100)}%
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex gap-1">
                      {(["include", "exclude", "uncertain"] as const).map(
                        (opt) => (
                          <button
                            key={opt}
                            disabled={isTerminal}
                            onClick={() => setDecision(d, opt)}
                            className={`px-2 py-1 text-xs rounded border ${
                              hd === opt
                                ? decisionColor[opt] + " font-semibold"
                                : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
                            } disabled:opacity-50 disabled:cursor-not-allowed`}
                          >
                            {decisionLabel[opt]}
                          </button>
                        )
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-700">
                    <div className="mb-1 italic text-gray-500">
                      AI: {d.ai_rationale || "—"}
                    </div>
                    {isOverride && !isTerminal && (
                      <textarea
                        value={state?.override_comment ?? ""}
                        onChange={(e) => setComment(d.id, e.target.value)}
                        placeholder="Override reason (required, ≥5 chars)"
                        rows={2}
                        maxLength={500}
                        className={`w-full px-2 py-1 border rounded text-xs ${
                          needsReason
                            ? "border-red-400 bg-red-50"
                            : "border-gray-300"
                        }`}
                      />
                    )}
                    {isOverride && isTerminal && d.override_comment && (
                      <div className="text-red-800">
                        Override reason: {d.override_comment}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Submit */}
      {!isTerminal && (
        <div className="border border-gray-200 rounded p-4 bg-gray-50">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Review notes (optional)
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Approved for Diabetes Guidance v3.2 draft"
            rows={2}
            maxLength={1000}
            className="w-full px-3 py-2 border border-gray-300 rounded text-sm mb-3"
          />
          {invalid.length > 0 && (
            <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 mb-3">
              {invalid.length} override{invalid.length > 1 ? "s" : ""} missing rationale:
              {" "}
              {invalid.slice(0, 5).map((i) => i.code).join(", ")}
              {invalid.length > 5 && "…"}
            </div>
          )}
          {error && (
            <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 mb-3">
              {error}
            </div>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => setConfirmAction("approve")}
              disabled={submitting || invalid.length > 0}
              className="px-4 py-2 bg-[#00436C] text-white text-sm font-medium rounded hover:bg-[#005EA5] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? "Submitting…" : "Approve codelist"}
            </button>
            <button
              onClick={() => setConfirmAction("reject")}
              disabled={submitting || invalid.length > 0}
              className="px-4 py-2 bg-white text-gray-700 text-sm font-medium rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-50"
            >
              Reject
            </button>
          </div>
        </div>
      )}

      {/* Approve/reject confirmation modal */}
      <ConfirmModal
        open={confirmAction !== null}
        title={confirmAction === "approve" ? "Approve codelist" : "Reject codelist"}
        confirmLabel={confirmAction === "approve" ? "Approve" : "Reject"}
        loadingLabel={confirmAction === "approve" ? "Approving…" : "Rejecting…"}
        variant={confirmAction === "reject" ? "danger" : "primary"}
        loading={submitting}
        onConfirm={() => {
          if (confirmAction) submit(confirmAction);
        }}
        onCancel={() => setConfirmAction(null)}
      >
        {confirmAction === "approve" ? (
          <>
            <p className="mb-2">
              <strong>{counts.include}</strong> included,{" "}
              <strong>{counts.exclude}</strong> excluded,{" "}
              <strong>{counts.uncertain}</strong> uncertain.
            </p>
            <p className="mb-2">
              <strong>{counts.overrides}</strong> override(s) from the AI&apos;s original decisions.
            </p>
            <p className="text-xs text-gray-500">
              This is irreversible. A SHA-256 signature will be generated and the
              codelist locked for audit.
            </p>
          </>
        ) : (
          <p>
            Reject this codelist? The author will need to create a new draft.
          </p>
        )}
      </ConfirmModal>
    </div>
  );
}

"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import {
  exportPublicCodelistCsv,
  exportPublicCodelistOhdsi,
  getPublicCodelist,
  type OhdsiExport,
  type PublicCodelist,
} from "@/lib/api";
import { downloadBlob, slugify } from "@/lib/download";

// T32 — public detail view. Read-only, no review controls, no audit-log
// link, no override-comment column. The page deliberately mirrors the
// auth-side codelist detail layout so a researcher who clicks through
// from the gallery has the same mental model as a logged-in reviewer.
//
// Permalink: the URL itself is the canonical permalink. The signature_hash
// short-form has a copy button alongside it because that's the value
// downstream consumers will want to cite.

export default function PublicCodelistPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [codelist, setCodelist] = useState<PublicCodelist | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [hashCopied, setHashCopied] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);

  const [ohdsiBusy, setOhdsiBusy] = useState(false);
  const [ohdsiExport, setOhdsiExport] = useState<OhdsiExport | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [csvBusy, setCsvBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getPublicCodelist(id)
      .then((cl) => { if (!cancelled) setCodelist(cl); })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id]);

  const copyHash = async () => {
    if (!codelist?.signature_hash) return;
    await navigator.clipboard.writeText(codelist.signature_hash);
    setHashCopied(true);
    window.setTimeout(() => setHashCopied(false), 2000);
  };

  const copyPermalink = async () => {
    // window is only defined client-side; the route is "use client" so
    // this is fine, but guard anyway in case of a future SSR pivot.
    if (typeof window === "undefined") return;
    await navigator.clipboard.writeText(window.location.href);
    setLinkCopied(true);
    window.setTimeout(() => setLinkCopied(false), 2000);
  };

  const handleCsv = async () => {
    if (!codelist || csvBusy) return;
    setCsvBusy(true);
    setExportError(null);
    try {
      const blob = await exportPublicCodelistCsv(codelist.id);
      downloadBlob(blob, `${slugify(codelist.name)}.csv`);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "CSV export failed");
    } finally {
      setCsvBusy(false);
    }
  };

  const handleOhdsi = async () => {
    if (!codelist || ohdsiBusy) return;
    setOhdsiBusy(true);
    setExportError(null);
    try {
      const data = await exportPublicCodelistOhdsi(codelist.id);
      setOhdsiExport(data);
      const blob = new Blob([JSON.stringify(data.concept_set, null, 2)], {
        type: "application/json",
      });
      downloadBlob(blob, `${slugify(codelist.name)}.ohdsi.json`);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "OHDSI export failed");
    } finally {
      setOhdsiBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto px-6 py-8 animate-pulse">
        <div className="h-6 bg-gray-200 rounded w-64 mb-4" />
        <div className="h-4 bg-gray-200 rounded w-48 mb-8" />
        <div className="space-y-3">
          {[...Array(5)].map((_, i) => <div key={i} className="h-10 bg-gray-200 rounded" />)}
        </div>
      </div>
    );
  }
  if (error || !codelist) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-12 text-center">
        <p className="text-sm text-gray-700">
          This codelist isn&apos;t in the public gallery — it may not be approved
          yet, or its owner has opted it out.
        </p>
        <Link href="/gallery" className="mt-4 inline-block text-sm text-[#00436C] hover:underline">
          ← Back to the gallery
        </Link>
      </div>
    );
  }

  const decisions = codelist.decisions;
  const includedCount = decisions.filter((d) => d.human_decision === "include").length;
  const excludedCount = decisions.filter((d) => d.human_decision === "exclude").length;
  const uncertainCount = decisions.filter((d) => d.human_decision === "uncertain").length;

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="flex items-start justify-between mb-4 gap-4 flex-wrap">
        <div>
          <Link href="/gallery" className="text-xs text-[#00436C] hover:underline">
            ← Gallery
          </Link>
          <h1 className="font-[family-name:var(--font-lora)] text-2xl font-medium text-[#00436C] mt-1">
            {codelist.name}
            <span className="ml-2 text-sm text-gray-400">v{codelist.version}</span>
          </h1>
          <p className="text-sm text-gray-600 mt-1">
            Query: <span className="font-medium">{codelist.query || "—"}</span>
          </p>
          <p className="text-xs text-gray-500 mt-1">
            Submitted by {codelist.created_by_initials || "—"}
            {codelist.reviewed_by_initials && (
              <> · Reviewed by {codelist.reviewed_by_initials}</>
            )}
            {codelist.reviewed_at && (
              <> on {new Date(codelist.reviewed_at).toLocaleDateString()}</>
            )}
          </p>
          <button
            type="button"
            onClick={copyPermalink}
            className="mt-2 inline-flex items-center gap-1 text-xs text-[#005EA5] hover:underline"
            title="Copy this codelist's permalink to the clipboard"
          >
            {linkCopied ? "Permalink copied" : "Copy permalink"}
          </button>
        </div>
        <div className="text-right">
          <span className="inline-block text-xs px-2 py-0.5 rounded border bg-green-100 text-green-800 border-green-300">
            approved
          </span>
          {codelist.signature_hash && (
            <div className="mt-2 text-xs">
              <div className="font-mono text-gray-500">
                sig {codelist.signature_hash.slice(0, 16)}…
              </div>
              <button
                type="button"
                onClick={copyHash}
                className="mt-1 px-2 py-0.5 border border-gray-300 text-gray-700 hover:bg-gray-50"
                title="Copy full SHA-256 signature"
              >
                {hashCopied ? "Copied" : "Copy hash"}
              </button>
            </div>
          )}
          <div className="mt-3 flex flex-col items-end gap-1">
            <div className="flex items-center gap-2">
              <button
                onClick={handleCsv}
                disabled={csvBusy}
                className="px-3 py-1 border border-gray-300 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                {csvBusy ? "Exporting…" : "Download CSV"}
              </button>
              <button
                onClick={handleOhdsi}
                disabled={ohdsiBusy}
                className="px-3 py-1 border border-gray-300 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                title="OHDSI concept-set JSON for ATLAS / DARWIN-EU"
              >
                {ohdsiBusy ? "Exporting…" : "OHDSI concept set"}
              </button>
            </div>
            {ohdsiExport && (
              <div className="text-xs text-gray-700">
                <span className="font-semibold text-[#00436C]">
                  {ohdsiExport.concept_set.expression.items.length}
                </span>{" "}
                mapped ·{" "}
                <span className="font-semibold text-[#7C2A00]">
                  {ohdsiExport.unmapped.length}
                </span>{" "}
                unmapped
              </div>
            )}
            {exportError && (
              <div className="text-xs text-red-700">{exportError}</div>
            )}
          </div>
        </div>
      </div>

      {/* Redaction notice — visitors should know this is the public copy. */}
      <div className="mb-4 px-3 py-2 bg-amber-50 border border-amber-200 text-xs text-amber-900">
        This is the public copy of an approved codelist. Reviewer identities,
        override rationales, and algorithmic-suggestion rows are redacted; the
        full audit log lives behind clinician sign-in.
      </div>

      {/* Study-intent criteria — same component as auth view, read-only here. */}
      {((codelist.include_criteria && codelist.include_criteria.length > 0)
        || (codelist.exclude_criteria && codelist.exclude_criteria.length > 0)) && (
        <section className="mb-4 border border-gray-200 bg-white px-4 py-3">
          <h3 className="text-sm font-medium text-[#00436C] mb-2">
            Study-intent criteria
          </h3>
          <dl className="text-xs space-y-1">
            {codelist.include_criteria && codelist.include_criteria.length > 0 && (
              <div>
                <dt className="inline font-medium text-gray-700">Include: </dt>
                <dd className="inline text-gray-600">
                  {codelist.include_criteria.join(", ")}
                </dd>
              </div>
            )}
            {codelist.exclude_criteria && codelist.exclude_criteria.length > 0 && (
              <div>
                <dt className="inline font-medium text-gray-700">Exclude: </dt>
                <dd className="inline text-gray-600">
                  {codelist.exclude_criteria.join(", ")}
                </dd>
              </div>
            )}
          </dl>
        </section>
      )}

      {/* Adopted phenotypes — citations the author picked from HDR UK. */}
      {codelist.adopted_phenotypes && codelist.adopted_phenotypes.length > 0 && (
        <section className="mb-4 border border-gray-200 bg-white px-4 py-3">
          <h3 className="text-sm font-medium text-[#00436C] mb-2">
            Adopted phenotypes
          </h3>
          <ul className="space-y-1.5">
            {codelist.adopted_phenotypes.map((a) => (
              <li key={a.phenotype_id} className="text-xs">
                <a
                  href={a.hdruk_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[#005EA5] hover:underline font-medium"
                >
                  {a.name}{" "}
                  <span className="text-gray-400 font-normal">
                    ({a.phenotype_id}
                    {a.phenotype_version_id != null && ` v${a.phenotype_version_id}`})
                  </span>
                </a>
                {a.first_publication && (
                  <span className="text-gray-500"> — {a.first_publication}</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex items-center gap-4 border-y border-gray-200 py-2 text-xs mb-4">
        <span className="text-gray-500">
          {decisions.length} codes total
        </span>
        <span className="text-green-700">Include: {includedCount}</span>
        <span className="text-red-700">Exclude: {excludedCount}</span>
        <span className="text-amber-700">Uncertain: {uncertainCount}</span>
      </div>

      <div className="border border-gray-200 rounded overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-600">
            <tr>
              <th scope="col" className="px-3 py-2">Code</th>
              <th scope="col" className="px-3 py-2">Term</th>
              <th scope="col" className="px-3 py-2">Decision</th>
              <th scope="col" className="px-3 py-2">AI confidence</th>
              <th scope="col" className="px-3 py-2">Rationale</th>
            </tr>
          </thead>
          <tbody>
            {decisions.map((d) => (
              <tr key={d.id} className="border-t border-gray-100 align-top">
                <td className="px-3 py-2 font-mono text-xs">
                  {d.code}
                  <div className="text-[10px] text-gray-500">{d.vocabulary}</div>
                </td>
                <td className="px-3 py-2">{d.term}</td>
                <td className="px-3 py-2">
                  <span
                    className={`inline-block text-xs px-2 py-0.5 rounded border ${
                      d.human_decision === "include"
                        ? "bg-green-100 text-green-800 border-green-300"
                        : d.human_decision === "exclude"
                        ? "bg-red-100 text-red-800 border-red-300"
                        : "bg-amber-100 text-amber-800 border-amber-300"
                    }`}
                  >
                    {d.human_decision}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono text-xs text-gray-700 tabular-nums">
                  {Math.round(d.ai_confidence * 100)}%
                </td>
                <td className="px-3 py-2 text-xs text-gray-600 italic">
                  {d.ai_rationale || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

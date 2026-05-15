"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  listPublicCodelists,
  type PublicCodelistSummary,
} from "@/lib/api";

// T32 — public list view of approved codelists. No auth: intentionally
// the front door for visitors who haven't logged in. The auth-side
// `/codelists` page stays unchanged; this is a separate, redacted
// surface, hence the parallel route rather than a "anonymous mode" on
// the existing one.
export default function GalleryPage() {
  const [rows, setRows] = useState<PublicCodelistSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listPublicCodelists({ limit: 200 })
      .then((data) => { if (!cancelled) setRows(data); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="mb-6">
        <h1 className="font-[family-name:var(--font-lora)] text-3xl font-semibold text-[#00436C]">
          Gallery of approved codelists
        </h1>
        <p className="text-sm text-gray-600 mt-2 max-w-3xl">
          Approved, signed clinical codelists published from clinicalcodes.uk.
          Reviewer identities, override comments, and UMLS-suggestion rows
          are redacted from these public copies — see{" "}
          <Link href="/" className="text-[#005EA5] hover:underline">the
          search page</Link>{" "}for the full reviewer view (sign-in required).
        </p>
      </div>

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3 mb-4">
          {error}
        </div>
      )}

      {rows == null && !error && (
        <div className="animate-pulse space-y-3">
          {[...Array(4)].map((_, i) => <div key={i} className="h-12 bg-gray-200 rounded" />)}
        </div>
      )}

      {rows != null && rows.length === 0 && (
        <div className="text-center py-12 border border-dashed border-gray-300 rounded">
          <p className="text-sm text-gray-500">
            No approved codelists yet. Sign in and approve a codelist to populate
            this gallery, or read the methodology in our{" "}
            <Link href="/" className="text-[#005EA5] hover:underline">search page</Link>.
          </p>
        </div>
      )}

      {rows != null && rows.length > 0 && (
        <table className="w-full text-sm border border-gray-200 rounded overflow-hidden">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-600">
            <tr>
              <th scope="col" className="px-4 py-2">Name</th>
              <th scope="col" className="px-4 py-2">Query</th>
              <th scope="col" className="px-4 py-2 text-right">Included</th>
              <th scope="col" className="px-4 py-2 text-right">Codes</th>
              <th scope="col" className="px-4 py-2">Signature</th>
              <th scope="col" className="px-4 py-2">Approved</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-gray-100 hover:bg-blue-50/50">
                <td className="px-4 py-2">
                  <Link
                    href={`/gallery/${r.id}`}
                    className="font-medium text-[#00436C] hover:underline"
                  >
                    {r.name}
                  </Link>
                  {(r.created_by_initials || r.reviewed_by_initials) && (
                    <span className="ml-2 text-xs text-gray-400" title="Author / Reviewer">
                      {r.created_by_initials}
                      {r.reviewed_by_initials &&
                        r.reviewed_by_initials !== r.created_by_initials &&
                        ` · ${r.reviewed_by_initials}`}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2 text-gray-700">
                  {r.query || <em className="text-gray-400">—</em>}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {r.included_count}
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-gray-500">
                  {r.decisions_count}
                </td>
                <td className="px-4 py-2 font-mono text-xs text-gray-500">
                  {r.signature_hash ? `${r.signature_hash.slice(0, 12)}…` : "—"}
                </td>
                <td className="px-4 py-2 text-gray-500 text-xs">
                  {r.reviewed_at
                    ? new Date(r.reviewed_at).toLocaleDateString()
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

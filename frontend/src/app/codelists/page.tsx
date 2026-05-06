"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  listCodelists,
  type CodelistSummary,
  type CodelistStatus,
} from "@/lib/api";
import { useUser } from "@/lib/useUser";

type Filter = "mine" | "pending" | "approved";

const statusStyles: Record<CodelistStatus, string> = {
  draft: "bg-gray-100 text-gray-800 border-gray-300",
  in_review: "bg-blue-100 text-blue-800 border-blue-300",
  // T30: adjudication is the v2-only state between in_review and
  // approved; amber matches the kappa-warning treatment on the
  // detail page for visual consistency.
  adjudication: "bg-amber-100 text-amber-800 border-amber-300",
  approved: "bg-green-100 text-green-800 border-green-300",
  rejected: "bg-red-100 text-red-800 border-red-300",
};

export default function CodelistsPage() {
  const { user, loading: userLoading } = useUser();
  const router = useRouter();

  const [filter, setFilter] = useState<Filter>("mine");
  const [rows, setRows] = useState<CodelistSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!userLoading && !user) {
      router.push("/login?next=/codelists");
    }
  }, [userLoading, user, router]);

  useEffect(() => {
    if (!user) return;
    setLoading(true);
    let cancelled = false;
    const opts =
      filter === "mine"
        ? { mine: true }
        : filter === "pending"
        ? { status: "draft" as CodelistStatus }
        : { status: "approved" as CodelistStatus };
    listCodelists(opts)
      .then((data) => { if (!cancelled) setRows(data); })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [user, filter]);

  if (!user) return (
    <div className="max-w-5xl mx-auto px-6 py-8 animate-pulse">
      <div className="h-8 bg-gray-200 rounded w-48 mb-6" />
      <div className="h-4 bg-gray-200 rounded w-full mb-3" />
      <div className="h-4 bg-gray-200 rounded w-3/4" />
    </div>
  );

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="text-3xl font-serif font-medium text-[#00436C]">
          Codelists
        </h1>
        <Link
          href="/"
          className="text-sm text-[#00436C] hover:underline"
        >
          ← New search
        </Link>
      </div>

      <div className="flex gap-1 border-b border-gray-200 mb-4">
        {(
          [
            ["mine", "My drafts"],
            ["pending", "All drafts"],
            ["approved", "Approved"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            onClick={() => setFilter(k)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              filter === k
                ? "border-[#00436C] text-[#00436C]"
                : "border-transparent text-gray-600 hover:text-gray-900"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3 mb-4">
          {error}
        </div>
      )}

      {loading && (
        <div className="animate-pulse space-y-3">
          {[...Array(3)].map((_, i) => <div key={i} className="h-10 bg-gray-200 rounded" />)}
        </div>
      )}

      {!loading && rows.length === 0 && (
        <div className="text-center py-12 border border-dashed border-gray-300 rounded">
          <p className="text-sm text-gray-500">
            No codelists yet.{" "}
            <Link href="/" className="text-[#00436C] hover:underline">
              Run a search
            </Link>{" "}
            to create one.
          </p>
        </div>
      )}

      {!loading && rows.length > 0 && (
        <table className="w-full text-sm border border-gray-200 rounded overflow-hidden">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-600">
            <tr>
              <th scope="col" className="px-4 py-2">Name</th>
              <th scope="col" className="px-4 py-2">Query</th>
              <th scope="col" className="px-4 py-2">Status</th>
              <th scope="col" className="px-4 py-2 text-right">Codes</th>
              <th scope="col" className="px-4 py-2">Created by</th>
              <th scope="col" className="px-4 py-2">Created</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                className="border-t border-gray-100 hover:bg-blue-50/50"
              >
                <td className="px-4 py-2">
                  <Link
                    href={`/codelists/${r.id}`}
                    className="font-medium text-[#00436C] hover:underline"
                  >
                    {r.name}
                  </Link>
                  <span className="ml-2 text-xs text-gray-400">
                    v{r.version}
                  </span>
                  {/* Only meaningful on approved rows -- a draft with
                      private=1 isn't hiding anything because drafts
                      aren't on the gallery in the first place. */}
                  {r.status === "approved" && r.private ? (
                    <span
                      className="ml-2 text-xs text-gray-500 border border-gray-300 px-1 py-0.5"
                      title="Hidden from the public gallery"
                    >
                      hidden
                    </span>
                  ) : null}
                </td>
                <td className="px-4 py-2 text-gray-700">
                  {r.query || <em className="text-gray-400">—</em>}
                </td>
                <td className="px-4 py-2">
                  <span
                    className={`inline-block text-xs px-2 py-0.5 rounded border ${statusStyles[r.status]}`}
                  >
                    {r.status}
                  </span>
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {r.decision_count}
                </td>
                <td className="px-4 py-2 text-gray-700">
                  {r.created_by_name || "—"}
                </td>
                <td className="px-4 py-2 text-gray-500 text-xs">
                  {new Date(r.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

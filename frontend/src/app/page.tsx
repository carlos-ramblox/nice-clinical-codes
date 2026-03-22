"use client";

import { useState } from "react";
import type { CodeResult } from "@/lib/api";

const MOCK_RESULTS: CodeResult[] = [
  {
    code: "E11.9",
    term: "Type 2 diabetes mellitus without complications",
    vocabulary: "ICD-10",
    decision: "include",
    confidence: 0.98,
    rationale: "Direct match for T2DM diagnosis code",
    sources: ["NICE Guideline NG28"],
    usage_frequency: 482000,
    classifier_score: 0.96,
  },
  {
    code: "44054006",
    term: "Type 2 diabetes mellitus (disorder)",
    vocabulary: "SNOMED CT",
    decision: "include",
    confidence: 0.99,
    rationale: "Primary SNOMED concept for T2DM",
    sources: ["NHS Digital", "NICE NG28"],
    usage_frequency: 510000,
    classifier_score: 0.98,
  },
  {
    code: "I10",
    term: "Essential (primary) hypertension",
    vocabulary: "ICD-10",
    decision: "uncertain",
    confidence: 0.85,
    rationale: "Comorbidity — may need clinical review for inclusion scope",
    sources: ["NICE Guideline NG136", "Cochrane"],
    usage_frequency: 390000,
    classifier_score: 0.78,
  },
  {
    code: "59621000",
    term: "Essential hypertension (disorder)",
    vocabulary: "SNOMED CT",
    decision: "include",
    confidence: 0.97,
    rationale: "Primary SNOMED concept for essential hypertension",
    sources: ["NHS Digital", "NICE NG136"],
    usage_frequency: 445000,
    classifier_score: 0.95,
  },
  {
    code: "E10.9",
    term: "Type 1 diabetes mellitus without complications",
    vocabulary: "ICD-10",
    decision: "exclude",
    confidence: 0.95,
    rationale: "Type 1 diabetes, not type 2 — different condition",
    sources: ["NICE Guideline NG17"],
    usage_frequency: 120000,
    classifier_score: 0.12,
  },
  {
    code: "46635009",
    term: "Type 1 diabetes mellitus (disorder)",
    vocabulary: "SNOMED CT",
    decision: "exclude",
    confidence: 0.96,
    rationale: "Type 1 diabetes — excluded from T2DM code list",
    sources: ["NHS Digital", "NICE NG17"],
    usage_frequency: 135000,
    classifier_score: 0.1,
  },
];

function DecisionBadge({ decision }: { decision: string }) {
  const config = {
    include: { bg: "bg-green-100", text: "text-green-800", border: "border-green-300", icon: "✓", label: "Included" },
    exclude: { bg: "bg-red-100", text: "text-red-800", border: "border-red-300", icon: "✕", label: "Excluded" },
    uncertain: { bg: "bg-amber-100", text: "text-amber-800", border: "border-amber-300", icon: "?", label: "Review" },
  }[decision] || { bg: "bg-gray-100", text: "text-gray-800", border: "border-gray-300", icon: "—", label: decision };

  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border ${config.bg} ${config.text} ${config.border}`}>
      <span className="text-[10px]">{config.icon}</span>
      {config.label}
    </span>
  );
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<CodeResult[] | null>(null);
  const [selectedCode, setSelectedCode] = useState<CodeResult | null>(null);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    // TODO: hook up to backend API
    await new Promise((r) => setTimeout(r, 800));
    setResults(MOCK_RESULTS);
    setSelectedCode(MOCK_RESULTS[0]);
    setLoading(false);
  };

  return (
    <div className="max-w-7xl mx-auto px-6 py-8">
      {/* Search */}
      <div className="flex justify-center mb-10">
        <form onSubmit={handleSearch} className="w-full max-w-3xl">
          <div className="flex border border-gray-300 bg-white overflow-hidden focus-within:ring-2 focus-within:ring-[#005EA5] focus-within:border-transparent">
            <div className="flex items-center pl-4 text-gray-400">
              <svg width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="11" cy="11" r="8" />
                <path d="m21 21-4.35-4.35" />
              </svg>
            </div>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Enter clinical condition (e.g. type 2 diabetes with hypertension)"
              className="flex-1 px-3 py-3 focus:outline-none"
            />
            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="px-8 bg-[#005EA5] text-white font-medium hover:bg-[#00436E] disabled:opacity-50 transition-colors"
            >
              {loading ? "..." : "Search"}
            </button>
          </div>
        </form>
      </div>

      {/* Results + Provenance */}
      {results && (
        <div className="flex gap-6">
          {/* Table */}
          <div className="flex-1 bg-white border border-gray-200">
            <div className="px-5 py-3 border-b border-gray-200">
              <h3 className="font-[family-name:var(--font-lora)] text-lg font-semibold">
                Results
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-[#005EA5] text-white text-left">
                    <th className="px-4 py-2.5 font-medium">Code</th>
                    <th className="px-4 py-2.5 font-medium">Term</th>
                    <th className="px-4 py-2.5 font-medium">System</th>
                    <th className="px-4 py-2.5 font-medium">Decision</th>
                    <th className="px-4 py-2.5 font-medium">Confidence %</th>
                    <th className="px-4 py-2.5 font-medium">Sources</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => (
                    <tr
                      key={r.code}
                      tabIndex={0}
                      role="button"
                      aria-label={`View details for ${r.term}`}
                      onClick={() => setSelectedCode(r)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setSelectedCode(r);
                        }
                      }}
                      className={`border-b border-gray-100 cursor-pointer transition-colors ${
                        selectedCode?.code === r.code
                          ? "bg-blue-50"
                          : i % 2 === 0
                          ? "bg-white"
                          : "bg-gray-50/50"
                      } hover:bg-blue-50 focus:bg-blue-50 focus:outline-none`}
                    >
                      <td className="px-4 py-3 font-mono text-xs">{r.code}</td>
                      <td className="px-4 py-3">{r.term}</td>
                      <td className="px-4 py-3 text-gray-600">{r.vocabulary}</td>
                      <td className="px-4 py-3">
                        <DecisionBadge decision={r.decision} />
                      </td>
                      <td className="px-4 py-3">{Math.round(r.confidence * 100)}%</td>
                      <td className="px-4 py-3 text-gray-600 text-xs">
                        {r.sources.join(", ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-5 py-3 flex justify-end border-t border-gray-200">
              <button
                className="inline-flex items-center gap-2 px-5 py-2 bg-[#005EA5] text-white text-sm font-medium hover:bg-[#00436E] transition-colors disabled:opacity-50"
                disabled
              >
                <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
                </svg>
                Export CSV
              </button>
            </div>
          </div>

          {/* Provenance panel */}
          <div className="w-80 shrink-0">
            <div className="bg-white border border-[#005EA5] sticky top-6">
              <div className="bg-[#005EA5] text-white px-5 py-3">
                <h3 className="font-[family-name:var(--font-lora)] font-semibold">
                  Provenance Details
                </h3>
              </div>
              {selectedCode ? (
                <dl className="px-5 py-4 space-y-4 text-sm">
                  <div>
                    <dt className="font-semibold">Source Guideline</dt>
                    <dd className="text-gray-600 mt-0.5">{selectedCode.sources.join(", ")}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold">Search Date</dt>
                    <dd className="text-gray-600 mt-0.5">{new Date().toISOString().split("T")[0]}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold">Search Query</dt>
                    <dd className="text-gray-600 mt-0.5">{query}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold">Decision Rationale</dt>
                    <dd className="text-gray-600 mt-0.5">{selectedCode.rationale}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold">Classifier Score</dt>
                    <dd className="text-gray-600 mt-0.5">
                      {selectedCode.classifier_score != null
                        ? `${Math.round(selectedCode.classifier_score * 100)}%`
                        : "N/A"}
                    </dd>
                  </div>
                  <div>
                    <dt className="font-semibold">Algorithm Version</dt>
                    <dd className="text-gray-600 mt-0.5">0.1.0</dd>
                  </div>
                </dl>
              ) : (
                <div className="px-5 py-8 text-center text-gray-400 text-sm">
                  Click a row to view details
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!results && !loading && (
        <div className="text-center mt-20">
          <h2 className="font-[family-name:var(--font-lora)] text-2xl font-semibold text-gray-700 mb-2">
            Clinical Code Search
          </h2>
          <p className="text-gray-500 text-sm">
            Search for SNOMED CT and ICD-10 codes across NHS reference sets,
            QOF business rules, and published code lists.
          </p>
        </div>
      )}
    </div>
  );
}

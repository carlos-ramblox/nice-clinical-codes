"use client";

import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import {
  searchCodes,
  exportCodes,
  exportCodesOhdsi,
  createCodelist,
  discoverPhenotypes,
} from "@/lib/api";
import type {
  CodeResult,
  SearchResponse,
  PhenotypeDiscoveryResult,
  AdoptedPhenotype,
  OhdsiExport,
} from "@/lib/api";
import { useUser } from "@/lib/useUser";
import { downloadBlob } from "@/lib/download";
import { getRecent, pushRecent, formatAgo, type RecentSearch } from "@/lib/recentSearches";
import { ConfirmModal } from "./ConfirmModal";

const PAGE_SIZE = 20;

const LOADING_STEPS = [
  { label: "Parsing your query...", delay: 0 },
  { label: "Searching OMOPHub for SNOMED and ICD-10 codes...", delay: 1500 },
  { label: "Querying QOF business rules...", delay: 3000 },
  { label: "Checking published code lists on OpenCodelists...", delay: 5000 },
  { label: "Running semantic search across embedded codes...", delay: 7000 },
  { label: "Merging and deduplicating results...", delay: 10000 },
  { label: "Scoring codes with LLM reasoning...", delay: 13000 },
  { label: "Almost done — assembling final results...", delay: 21000 },
];

// T31: render the OpenCodeCounts-derived usage column for one code.
// Three branches:
//   counted          → "12,540" + setting-aware badge ("GP" or "HES")
//   withheld_below_5 → "<5" with a tooltip explaining NHS Digital's
//                      1-4 privacy rule
//   not_in_dataset   → em-dash with a tooltip explaining absence
// Setting is inferred from usage_source so "12,540" never shows
// without a clarifier. Counts are NHS Digital's published rounded
// values for SNOMED primary care; ICD-10/OPCS-4 are unrounded.
function UsageCell({ row }: { row: CodeResult }) {
  const status = row.usage_status;
  if (status === "counted" && typeof row.usage_frequency === "number") {
    // Use the machine-readable usage_setting from the API rather than
    // substring-matching usage_source — robust to future rewording of
    // the attribution string.
    const isHes = row.usage_setting === "secondary_care_hes";
    const settingLabel = isHes ? "HES" : "GP";
    const settingTitle = isHes
      ? "NHS Digital HES inpatient FCEs (Apr 2024 – Mar 2025)"
      : "NHS Digital primary-care SNOMED reporting (Aug 2024 – Jul 2025)";
    return (
      <span className="whitespace-nowrap" title={row.usage_source ?? settingTitle}>
        <span className="tabular-nums">{row.usage_frequency.toLocaleString()}</span>
        <span
          className="ml-1.5 inline-flex items-center px-1 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-700 border border-gray-200"
          aria-label={settingTitle}
        >
          {settingLabel}
        </span>
      </span>
    );
  }
  if (status === "withheld_below_5") {
    return (
      <span
        className="text-amber-700 text-xs"
        title="NHS Digital withholds counts of 1-4 under their privacy policy. Code was used at least once."
      >
        &lt;5
      </span>
    );
  }
  return (
    <span
      className="text-gray-400"
      title="Code is not present in the NHS Digital usage dataset."
    >
      —
    </span>
  );
}


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

function LoadingProgress() {
  const [step, setStep] = useState(0);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const timer = setInterval(() => {
      const ms = Date.now() - start;
      setElapsed(ms);
      const nextStep = LOADING_STEPS.findLastIndex((s) => ms >= s.delay);
      if (nextStep >= 0) setStep(nextStep);
    }, 500);
    return () => clearInterval(timer);
  }, []);

  const progress = Math.min((elapsed / 25000) * 100, 95);

  return (
    <div className="max-w-xl mx-auto py-16">
      <div className="flex flex-col items-center gap-6">
        <div className="h-10 w-10 border-4 border-[#005EA5] border-t-transparent rounded-full animate-spin" />

        <p className="text-gray-700 text-sm font-medium text-center" aria-live="polite">
          {LOADING_STEPS[step]?.label ?? "Processing..."}
        </p>

        <div
          className="w-full bg-gray-200 h-2 overflow-hidden"
          role="progressbar"
          aria-valuenow={Math.round(progress)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Pipeline progress"
        >
          <div
            className="h-full bg-[#005EA5] transition-all duration-1000 ease-out"
            style={{ width: `${progress}%` }}
          />
        </div>

        <div className="flex items-center gap-4 text-xs text-gray-400">
          <span>{Math.floor(elapsed / 1000)}s elapsed</span>
          <span>Step {step + 1} of {LOADING_STEPS.length}</span>
        </div>

        <div className="flex flex-wrap justify-center gap-1.5 mt-2">
          {LOADING_STEPS.map((s, i) => (
            <div
              key={i}
              className={`h-1.5 w-8 transition-colors duration-300 ${
                i <= step ? "bg-[#005EA5]" : "bg-gray-200"
              }`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function PhenotypeDiscoverySidebar({
  rows,
  adoptedIds,
  onAdopt,
  onUnadopt,
}: {
  rows: PhenotypeDiscoveryResult[];
  adoptedIds: Set<string>;
  onAdopt: (row: PhenotypeDiscoveryResult) => void;
  onUnadopt: (phenotypeId: string) => void;
}) {
  // Read-mode panel: surfaces 3-5 candidate HDR UK phenotypes whose
  // clinical scope fits the user's query. Each row links out to the
  // authoritative HDR UK detail page; the rationale is shown as a
  // visible caption (not a tooltip) so the persona can decide whether
  // a row deserves the click-through. No "use this phenotype" action,
  // no auto-import — that's deferred to T34b / T35.
  //
  // Empty state: when discovery has fetched but found nothing, render
  // a one-line hint that tells the user the dual-path explicitly
  // ("none matched, click Search to generate fresh"). This is the
  // alternative we surface so a less-expert user doesn't read the
  // silent disappearance of the sidebar as a failure.
  if (rows.length === 0) {
    return (
      <aside
        aria-label="No HDR UK phenotypes matched"
        className="max-w-5xl mx-auto mb-6 border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600"
      >
        No published HDR UK phenotypes match this query &mdash; click <span className="font-medium">Search</span> to
        generate a fresh codelist from the multi-source pipeline.
      </aside>
    );
  }
  return (
    <aside
      aria-label="Related HDR UK phenotypes"
      className="max-w-5xl mx-auto mb-6 border border-gray-200 bg-white"
    >
      <div className="px-4 py-2.5 border-b border-gray-200">
        <h3 className="font-[family-name:var(--font-lora)] text-sm font-semibold text-[#00436C]">
          Related HDR UK phenotypes
        </h3>
        <p className="text-[11px] text-gray-500 mt-0.5">
          Found {rows.length} published {rows.length === 1 ? "codelist" : "codelists"} that may answer your question.
          {" "}None fit? Click <span className="font-medium">Search</span> to generate a fresh codelist instead.
        </p>
      </div>
      <ul className="divide-y divide-gray-100">
        {rows.map((row) => (
          <li key={row.phenotype_id} className="px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <a
                href={row.hdruk_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[#005EA5] hover:underline font-medium text-sm"
              >
                {row.name}{" "}
                <span className="text-gray-400 font-normal">({row.phenotype_id})</span>
                <svg
                  className="inline-block ml-1 -mt-0.5"
                  width="11"
                  height="11"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden="true"
                >
                  <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
                  <path d="M15 3h6v6" />
                  <path d="M10 14L21 3" />
                </svg>
              </a>
              <span
                className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wide ${
                  row.relevance_verdict === "relevant"
                    ? "bg-green-50 text-green-700 border border-green-200"
                    : "bg-amber-50 text-amber-700 border border-amber-200"
                }`}
              >
                {row.relevance_verdict}
              </span>
            </div>
            <div className="mt-1.5">
              {/* T34b: explicit adoption action. Recorded in the user's
                  in-memory state and applied on Save-as-Draft as a
                  phenotype_adopted audit-log event. The button morphs
                  to "Adopted" once the user has adopted; clicking again
                  is a no-op (the parent component dedups by phenotype id). */}
              {adoptedIds.has(row.phenotype_id) ? (
                <span className="text-[11px] inline-flex items-center gap-2">
                  <span className="text-green-700 font-medium">
                    ✓ Adopted — will cite on save
                  </span>
                  <button
                    type="button"
                    onClick={() => onUnadopt(row.phenotype_id)}
                    className="text-gray-500 hover:text-gray-700 underline"
                    aria-label={`Remove ${row.phenotype_id} from adoptions`}
                  >
                    remove
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => onAdopt(row)}
                  className="text-[11px] text-[#005EA5] hover:underline"
                >
                  + Use this phenotype as a citation
                </button>
              )}
            </div>
            {(row.type.length > 0 || row.coding_systems.length > 0 || row.data_sources.length > 0) && (
              <div className="mt-1 flex flex-wrap gap-1 text-[11px]">
                {row.type.map((t) => (
                  <span key={`t-${t}`} className="px-1.5 py-0.5 bg-gray-100 text-gray-700 rounded">
                    {t}
                  </span>
                ))}
                {row.coding_systems.slice(0, 4).map((c) => (
                  <span key={`c-${c}`} className="px-1.5 py-0.5 bg-blue-50 text-blue-700 rounded">
                    {c}
                  </span>
                ))}
                {row.data_sources.slice(0, 3).map((d) => (
                  <span key={`d-${d}`} className="px-1.5 py-0.5 bg-gray-50 text-gray-600 rounded">
                    {d}
                  </span>
                ))}
              </div>
            )}
            <p className="mt-1.5 text-xs text-gray-600 italic">
              {row.relevance_rationale}
            </p>
            {row.first_publication && (
              <p className="mt-1 text-[11px] text-gray-400 line-clamp-2">
                {row.first_publication}
              </p>
            )}
          </li>
        ))}
      </ul>
      <div className="px-4 py-2 border-t border-gray-100 text-[11px] text-gray-400">
        These phenotypes are surfaced for citation and adjudication, not auto-merged into
        your generated codelist.
      </div>
    </aside>
  );
}


function DecisionFilter({
  filter,
  onChange,
  counts,
}: {
  filter: string;
  onChange: (f: string) => void;
  counts: { include: number; exclude: number; uncertain: number; all: number };
}) {
  const tabs = [
    { key: "all", label: "All", count: counts.all, color: "text-gray-700" },
    { key: "include", label: "Included", count: counts.include, color: "text-green-700" },
    { key: "exclude", label: "Excluded", count: counts.exclude, color: "text-red-700" },
    { key: "uncertain", label: "Review", count: counts.uncertain, color: "text-amber-700" },
  ];

  return (
    <div className="flex gap-1">
      {tabs.map((t) => (
        <button
          key={t.key}
          onClick={() => onChange(t.key)}
          className={`px-3 py-1 text-xs font-medium transition-colors ${
            filter === t.key
              ? "bg-[#005EA5] text-white"
              : `bg-gray-100 ${t.color} hover:bg-gray-200`
          }`}
        >
          {t.label} ({t.count})
        </button>
      ))}
    </div>
  );
}

// T29 — comma-separated exclusion phrases the user types in the
// Exclusions input. Split, trim, drop empty, dedupe (case-insensitive).
// Returned in input order so the structured criteria the backend
// receives are predictable.
function parseExclusionsInput(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(trimmed);
  }
  return out;
}

export default function Home() {
  const [query, setQuery] = useState("");
  // T29 — kept across searches on purpose: a user iterating on
  // "diabetes" then "asthma" with the same "gestational" exclusion
  // shouldn't have to retype it.
  const [exclusionsInput, setExclusionsInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [searchedAt, setSearchedAt] = useState<string>("");
  const [selectedCode, setSelectedCode] = useState<CodeResult | null>(null);
  const [exporting, setExporting] = useState(false);
  const [ohdsiExport, setOhdsiExport] = useState<OhdsiExport | null>(null);
  const [ohdsiCopied, setOhdsiCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [page, setPage] = useState(1);
  const [decisionFilter, setDecisionFilter] = useState("all");

  const { user } = useUser();
  const router = useRouter();

  const [recent, setRecent] = useState<RecentSearch[]>([]);
  useEffect(() => { setRecent(getRecent()); }, []);

  // T34b: adopted-phenotype state for the discovery sidebar.
  // Session-scoped on purpose: no localStorage, no server pre-state.
  // The persona model is "browse-then-decide": adoptions are a UI-layer
  // marker until the user commits the whole package via Save-as-Draft.
  // If they leave the page without saving, the adoptions disappear --
  // matches the "I was browsing, I didn't commit" mental model. A
  // future maintainer thinking about persisting these to localStorage
  // should re-read the persona pre-flight first.
  const [adoptedPhenotypes, setAdoptedPhenotypes] = useState<AdoptedPhenotype[]>([]);
  const adoptedIds = useMemo(
    () => new Set(adoptedPhenotypes.map((a) => a.phenotype_id)),
    [adoptedPhenotypes],
  );
  const handleAdoptPhenotype = (row: PhenotypeDiscoveryResult) => {
    setAdoptedPhenotypes((prev) => {
      if (prev.some((a) => a.phenotype_id === row.phenotype_id)) return prev;
      return [
        ...prev,
        {
          phenotype_id: row.phenotype_id,
          // Capture the version id at adoption time so the citation
          // stays pinned to the version the user actually consulted.
          phenotype_version_id: row.phenotype_version_id,
          name: row.name,
          hdruk_url: row.hdruk_url,
          first_publication: row.first_publication,
        },
      ];
    });
  };
  const handleUnadoptPhenotype = (phenotypeId: string) => {
    // Session-only undo: lets a user un-adopt before they save without
    // having to refresh the page (which would lose every other adoption
    // collected during the browse). After save, undo lives in the codelist
    // detail page's audit log -- not yet exposed but available there.
    setAdoptedPhenotypes((prev) => prev.filter((a) => a.phenotype_id !== phenotypeId));
  };

  // HDR UK phenotype discovery (T34): surface candidate published
  // phenotypes from the HDR UK Phenotype Library as the user types,
  // before they commit to running the pipeline. Debounced 300 ms;
  // gate at >=3 chars so we don't spam Haiku on partial words.
  //
  // Tri-state: null = not yet fetched (or query too short, or the
  // fetch errored — we don't want to mis-attribute "no matches" on a
  // transient HDR UK 500). [] = fetched and the judge admitted
  // nothing — render the explicit "none matched, click Search"
  // hint. Non-empty array = fetched with results — render the full
  // sidebar. The render condition further hides the sidebar during
  // a pipeline run and once results are showing.
  const [discoveryRows, setDiscoveryRows] = useState<PhenotypeDiscoveryResult[] | null>(null);
  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 3) {
      setDiscoveryRows(null);
      return;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const data = await discoverPhenotypes(trimmed, 5, ctrl.signal);
        setDiscoveryRows(data);
      } catch {
        // Transient HDR UK / Haiku failure: hide the sidebar entirely
        // (set to null, not []) so the user does not read the empty
        // hint as authoritative. The main Search affordance is
        // unaffected.
        setDiscoveryRows(null);
      }
    }, 300);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [query]);

  const SAMPLE_QUERIES = [
    "Type 2 diabetes",
    "Asthma with COPD",
    "Hypertension in pregnancy",
    "Chronic kidney disease",
  ];

  const handleSaveAsDraft = () => {
    if (!response?.search_id) return;
    if (!user) {
      router.push(`/login?next=/`);
      return;
    }
    const defaultName = response.query
      ? `${response.query} — ${new Date().toLocaleDateString()}`
      : `Codelist — ${new Date().toLocaleString()}`;
    setDraftName(defaultName);
    setSaveError(null);
    setShowSaveModal(true);
  };

  const confirmSave = async () => {
    if (!response?.search_id || !draftName.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      // T34b: adoptions accumulated during the discovery-sidebar browse
      // are submitted alongside the codelist. The backend records each
      // as a phenotype_adopted audit-log event for tamper-evidence.
      const cl = await createCodelist(
        response.search_id,
        draftName.trim(),
        adoptedPhenotypes,
      );
      setShowSaveModal(false);
      router.push(`/codelists/${cl.id}`);
    } catch (e) {
      setSaveError(String(e));
      setSaving(false);
    }
  };

  const runSearch = async (q: string) => {
    if (!q.trim() || loading) return;

    setLoading(true);
    setError(null);
    setResponse(null);
    setSelectedCode(null);
    setPage(1);
    setDecisionFilter("all");
    setOhdsiExport(null);
    setOhdsiCopied(false);

    try {
      // T29 — pass the parsed exclusions through to the backend. Empty
      // array is byte-identical on the wire to the pre-T29 request body
      // (searchCodes drops the field when empty).
      const exclusions = parseExclusionsInput(exclusionsInput);
      const data = await searchCodes(q, { exclusions });
      setResponse(data);
      setSearchedAt(new Date().toISOString().split("T")[0]);
      pushRecent({ query: q, codeCount: data.results.length, at: new Date().toISOString() });
      setRecent(getRecent());
      if (data.results.length > 0) {
        setSelectedCode(data.results[0]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    runSearch(query);
  };

  const runQuery = (q: string) => {
    setQuery(q);
    runSearch(q);
  };

  const handleExport = async (format: "csv" | "xlsx") => {
    if (!response?.search_id || exporting) return;
    setExporting(true);
    try {
      const blob = await exportCodes(response.search_id, format);
      downloadBlob(blob, `codelist_${response.search_id}.${format}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  const handleOhdsiExport = async () => {
    if (!response?.search_id || exporting) return;
    setExporting(true);
    try {
      const data = await exportCodesOhdsi(response.search_id);
      setOhdsiExport(data);
      setOhdsiCopied(false);
      const blob = new Blob([JSON.stringify(data.concept_set, null, 2)], {
        type: "application/json",
      });
      const slug = (response.query || "codelist")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 60) || "codelist";
      downloadBlob(blob, `${slug}.ohdsi.json`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "OHDSI export failed");
    } finally {
      setExporting(false);
    }
  };

  const handleOhdsiCopy = async () => {
    if (!ohdsiExport) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(ohdsiExport.concept_set, null, 2));
      setOhdsiCopied(true);
      window.setTimeout(() => setOhdsiCopied(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Copy failed");
    }
  };

  const results = response?.results ?? null;

  // UMLS-enriched codes are suggestions (synonym/narrower/sibling expansion from
  // the UMLS Metathesaurus), not direct retrievals. Route them to the Review tab
  // regardless of the LLM's decision so reviewers can validate them separately.
  const isUmlsSuggestion = (r: CodeResult) =>
    r.sources?.some((s) => s.startsWith("UMLS")) ?? false;

  const filteredResults = useMemo(() => {
    if (!results) return [];
    if (decisionFilter === "all") return results;
    if (decisionFilter === "uncertain") {
      return results.filter((r) => r.decision === "uncertain" || isUmlsSuggestion(r));
    }
    return results.filter((r) => r.decision === decisionFilter && !isUmlsSuggestion(r));
  }, [results, decisionFilter]);

  const totalPages = Math.ceil(filteredResults.length / PAGE_SIZE);
  const pagedResults = filteredResults.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const decisionCounts = useMemo(() => {
    if (!results) return { all: 0, include: 0, exclude: 0, uncertain: 0 };
    const nonUmls = results.filter((r) => !isUmlsSuggestion(r));
    const reviewCount = results.filter(
      (r) => r.decision === "uncertain" || isUmlsSuggestion(r)
    ).length;
    return {
      all: results.length,
      include: nonUmls.filter((r) => r.decision === "include").length,
      exclude: nonUmls.filter((r) => r.decision === "exclude").length,
      uncertain: reviewCount,
    };
  }, [results]);

  // reset page when filter changes
  useEffect(() => { setPage(1); }, [decisionFilter]);

  return (
    <div className="max-w-7xl mx-auto px-6 py-8">
      {/* Hero heading — visible when no results */}
      {!results && !loading && (
        <div className="max-w-5xl mx-auto text-center mb-6 mt-4">
          <h1 className="font-[family-name:var(--font-lora)] text-3xl lg:text-4xl font-semibold text-[#00436C] mb-3">
            Generate a clinical code list
          </h1>
          <p className="text-gray-600 text-base">
            Search SNOMED CT and ICD-10 codes across NHS reference sets, QOF rules,
            and published codelists.
          </p>
        </div>
      )}

      {/* Search */}
      <div className="flex justify-center mb-6 w-full">
        <form onSubmit={handleSearch} className="w-full max-w-5xl">
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
              aria-label="Search clinical terms"
              className="flex-1 px-3 py-3 focus:outline-none"
            />
            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="px-4 sm:px-8 bg-[#005EA5] text-white font-medium hover:bg-[#00436C] disabled:opacity-50 transition-colors whitespace-nowrap"
            >
              {/* When discovery surfaced published phenotypes, the button
                  morphs to "Generate fresh codelist" so the user reads
                  it as the alternative-path-action rather than the only
                  thing to click. Otherwise stays as plain "Search". */}
              {loading
                ? "Searching..."
                : (discoveryRows && discoveryRows.length > 0)
                  ? "Generate fresh codelist"
                  : "Search"}
            </button>
          </div>
          {/* T29 — optional structured exclusions (Bennett 2023 mode 3).
              A separate compact row keeps the primary search affordance
              visually unchanged for users who don't need carve-outs. */}
          <div className="mt-2 flex items-center gap-2 text-xs">
            <label
              htmlFor="exclusions-input"
              className="text-gray-500 shrink-0"
              title='Comma-separated exclusion phrases. Each becomes a structured exclusion criterion (Bennett 2023 mode 3): the LLM excludes codes whose meaning falls under that term, and the carve-out is recorded in the codelist signature on approval.'
            >
              Exclude:
            </label>
            <input
              id="exclusions-input"
              type="text"
              value={exclusionsInput}
              onChange={(e) => setExclusionsInput(e.target.value)}
              placeholder="optional, comma-separated (e.g. gestational, type 1)"
              aria-label="Exclusion criteria, comma-separated"
              className="flex-1 px-2 py-1 border border-gray-200 rounded focus:outline-none focus:border-[#005EA5] text-gray-700"
              maxLength={300}
            />
          </div>
        </form>
      </div>

      {/* HDR UK phenotype discovery sidebar (T34) + adoption (T34b) */}
      {!loading && !results && discoveryRows !== null && (
        <PhenotypeDiscoverySidebar
          rows={discoveryRows}
          adoptedIds={adoptedIds}
          onAdopt={handleAdoptPhenotype}
          onUnadopt={handleUnadoptPhenotype}
        />
      )}

      {/* Loading */}
      {loading && <LoadingProgress />}

      {/* Error */}
      {error && (
        <div className="max-w-3xl mx-auto bg-red-50 border border-red-200 text-red-700 px-5 py-4 text-sm">
          <p className="font-semibold">Search failed</p>
          <p className="mt-1">{error}</p>
          <button onClick={() => setError(null)} className="mt-2 text-red-600 underline text-xs">
            Dismiss
          </button>
        </div>
      )}

      {/* Results + Provenance */}
      {results && results.length > 0 && (
        <div className="flex gap-6">
          {/* Table */}
          <div className="flex-1 bg-white border border-gray-200">
            <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between flex-wrap gap-2">
              <div className="flex items-center gap-4">
                <h3 className="font-[family-name:var(--font-lora)] text-lg font-semibold">Results</h3>
                <DecisionFilter filter={decisionFilter} onChange={setDecisionFilter} counts={decisionCounts} />
              </div>
              {response?.elapsed_seconds && (
                <span className="text-xs text-gray-400">{response.elapsed_seconds}s</span>
              )}
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
                    <th
                      className="px-4 py-2.5 font-medium"
                      // T31: column-header tooltip cites OpenCodeCounts and
                      // names the rounding/withholding rules. The setting
                      // badge ("GP" or "HES") on each cell distinguishes
                      // primary care from HES inpatient counts so the bare
                      // number is never read out of context.
                      title={
                        "Annual code-usage frequency from NHS Digital. " +
                        "Methodology follows Bennett Institute's OpenCodeCounts. " +
                        "SNOMED primary-care counts are rounded to the nearest 10 " +
                        "with 1-4 withheld; ICD-10 and OPCS-4 inpatient counts " +
                        "(HES) are unrounded with zero-usage codes excluded."
                      }
                    >
                      Usage
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {pagedResults.map((r, i) => (
                    <tr
                      key={`${r.code}-${r.vocabulary}`}
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
                        selectedCode?.code === r.code && selectedCode?.vocabulary === r.vocabulary
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
                        {isUmlsSuggestion(r) && (
                          <span
                            className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-100 text-purple-800 border border-purple-300"
                            title="Expanded from UMLS — review as a suggestion, not a direct match"
                          >
                            Suggested
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">{Math.round(r.confidence * 100)}%</td>
                      <td className="px-4 py-3 text-gray-600 text-xs">
                        {r.sources.join(", ")}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <UsageCell row={r} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination + Export. flex-wrap keeps the action buttons
                on one line on narrow viewports (the right column carries
                a 320px Provenance panel). */}
            <div className="px-5 py-3 flex flex-wrap items-center justify-between gap-y-3 border-t border-gray-200">
              <div className="flex items-center gap-2 text-xs text-gray-500">
                <span>
                  Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, filteredResults.length)} of {filteredResults.length}
                </span>
                {totalPages > 1 && (
                  <div className="flex gap-1 ml-2">
                    <button
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                      disabled={page === 1}
                      className="px-2 py-1 border border-gray-300 hover:bg-gray-50 disabled:opacity-30"
                    >
                      Prev
                    </button>
                    {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                      <button
                        key={p}
                        onClick={() => setPage(p)}
                        className={`px-2 py-1 border ${
                          p === page
                            ? "bg-[#005EA5] text-white border-[#005EA5]"
                            : "border-gray-300 hover:bg-gray-50"
                        }`}
                      >
                        {p}
                      </button>
                    ))}
                    <button
                      onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                      disabled={page === totalPages}
                      className="px-2 py-1 border border-gray-300 hover:bg-gray-50 disabled:opacity-30"
                    >
                      Next
                    </button>
                  </div>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={handleSaveAsDraft}
                  disabled={saving || !response?.search_id}
                  className="inline-flex items-center gap-2 whitespace-nowrap px-4 py-2 bg-[#00436C] text-white text-sm font-medium hover:bg-[#005EA5] transition-colors disabled:opacity-50"
                  title={user ? "Save as a reviewable draft codelist" : "Sign in to save"}
                >
                  {saving ? "Saving…" : "Save as draft"}
                </button>
                <button
                  onClick={() => handleExport("csv")}
                  disabled={exporting || !response?.search_id}
                  className="inline-flex items-center gap-2 whitespace-nowrap px-5 py-2 bg-[#005EA5] text-white text-sm font-medium hover:bg-[#00436C] transition-colors disabled:opacity-50"
                >
                  <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
                  </svg>
                  {exporting ? "Exporting..." : "Export CSV"}
                </button>
                <button
                  onClick={() => handleExport("xlsx")}
                  disabled={exporting || !response?.search_id}
                  className="inline-flex items-center gap-2 whitespace-nowrap px-4 py-2 border border-gray-300 text-gray-700 text-sm font-medium hover:bg-gray-50 transition-colors disabled:opacity-50"
                >
                  Export Excel
                </button>
                <button
                  onClick={handleOhdsiExport}
                  disabled={exporting || !response?.search_id}
                  className="inline-flex items-center gap-2 whitespace-nowrap px-4 py-2 border border-gray-300 text-gray-700 text-sm font-medium hover:bg-gray-50 transition-colors disabled:opacity-50"
                  title="Download OHDSI concept-set JSON (ATLAS / CodelistGenerator)"
                >
                  OHDSI concept set
                </button>
              </div>
              {ohdsiExport && (
                /* basis-full keeps the badge + Copy JSON on their own row. */
                <div className="basis-full flex items-center justify-end gap-3 text-xs">
                  <span
                    className="text-gray-700 whitespace-nowrap"
                    title="Mapped: items with an OMOP concept_id ATLAS will accept. Unmapped: codes the corpus could not resolve to OMOP — surfaced separately, not invented."
                  >
                    <span className="font-semibold text-[#00436C]">
                      {ohdsiExport.concept_set.expression.items.length}
                    </span>{" "}
                    mapped ·{" "}
                    <span className="font-semibold text-[#7C2A00]">
                      {ohdsiExport.unmapped.length}
                    </span>{" "}
                    unmapped
                  </span>
                  <button
                    onClick={handleOhdsiCopy}
                    className="inline-flex items-center gap-1 whitespace-nowrap px-3 py-1 border border-gray-300 text-gray-700 hover:bg-gray-50 transition-colors"
                    title="Copy concept_set JSON to clipboard for paste into ATLAS"
                  >
                    {ohdsiCopied ? "Copied" : "Copy JSON"}
                  </button>
                </div>
              )}
              {saveError && (
                <div className="ml-auto text-xs text-red-700">{saveError}</div>
              )}
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
                    <dd className="text-gray-600 mt-0.5">{searchedAt || "—"}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold">Search Query</dt>
                    <dd className="text-gray-600 mt-0.5">{response?.query ?? query}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold">Decision Rationale</dt>
                    <dd className="text-gray-600 mt-0.5">{selectedCode.rationale}</dd>
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

      {/* No results */}
      {results && results.length === 0 && (
        <div className="text-center py-16 text-gray-400 text-sm">
          No codes found for this query.
        </div>
      )}

      {/* Sample chips + recent searches — only when empty */}
      {!results && !loading && !error && (
        <div className="max-w-5xl mx-auto mt-2 w-full">
          <div className="flex flex-wrap items-center gap-2 mb-10">
            <span className="text-xs text-gray-600 mr-1">Try an example:</span>
            {SAMPLE_QUERIES.map((q) => (
              <button
                key={q}
                onClick={() => runQuery(q)}
                className="px-3 py-1 text-xs text-[#00436C] border border-[#00436C] rounded hover:bg-[#00436C] hover:text-white transition-colors focus:outline-none focus:ring-2 focus:ring-[#005EA5]"
              >
                {q}
              </button>
            ))}
          </div>

          {recent.length > 0 && (
            <div>
              <h2 className="font-[family-name:var(--font-lora)] text-lg font-semibold text-[#00436C] mb-3">
                Recent searches
              </h2>
              <ul className="divide-y divide-gray-300 border-y border-gray-300">
                {recent.map((r) => (
                  <li key={r.at}>
                    <button
                      onClick={() => runQuery(r.query)}
                      className="w-full flex items-center justify-between px-3 py-3 text-sm hover:bg-gray-50 text-left focus:outline-none focus:ring-2 focus:ring-[#005EA5]"
                    >
                      <span>
                        <span className="font-medium">{r.query}</span>
                        <span className="text-gray-500"> · {r.codeCount.toLocaleString()} codes found</span>
                      </span>
                      <span className="text-xs text-gray-400">{formatAgo(r.at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Save-as-draft modal */}
      <ConfirmModal
        open={showSaveModal}
        title="Save as draft codelist"
        confirmLabel="Save"
        loadingLabel="Saving…"
        confirmDisabled={!draftName.trim()}
        loading={saving}
        onConfirm={confirmSave}
        onCancel={() => setShowSaveModal(false)}
      >
        <p className="mb-3">
          This will persist {response?.results.length ?? 0} codes for clinical review.
        </p>
        <label htmlFor="draft-name" className="block text-sm font-medium text-gray-700 mb-1">
          Codelist name
        </label>
        <input
          id="draft-name"
          type="text"
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          maxLength={200}
          onKeyDown={(e) => { if (e.key === "Enter" && draftName.trim()) confirmSave(); }}
          className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:border-[#005EA5]"
        />
        {saveError && (
          <div className="mt-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
            {saveError}
          </div>
        )}
      </ConfirmModal>
    </div>
  );
}

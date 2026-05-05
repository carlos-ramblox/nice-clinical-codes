const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

// all HITL endpoints need the session cookie — always include credentials
const AUTH_FETCH: RequestInit = { credentials: "include" };

export interface CodeResult {
  code: string;
  term: string;
  vocabulary: string;
  decision: "include" | "exclude" | "uncertain";
  confidence: number;
  rationale: string;
  sources: string[];
  usage_frequency: number | null;
}

export interface SearchResponse {
  search_id: string;
  query: string;
  conditions_parsed: Record<string, unknown>[];
  results: CodeResult[];
  summary: Record<string, unknown>;
  provenance_trail: Record<string, unknown>[];
  elapsed_seconds: number;
}

export async function searchCodes(
  query: string,
): Promise<SearchResponse> {
  const res = await fetch(`${API_BASE}/search`, {
    ...AUTH_FETCH,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  if (!res.ok) {
    throw new Error(`Search failed: ${res.status}`);
  }
  return res.json();
}

// --- HDR UK phenotype discovery (T34) --------------------------------------

export interface PhenotypeDiscoveryResult {
  phenotype_id: string;
  name: string;
  type: string[];
  coding_systems: string[];
  data_sources: string[];
  first_publication: string;
  hdruk_url: string;
  relevance_rationale: string;
  relevance_verdict: "relevant" | "uncertain";
}

export interface CrossReferenceRow {
  phenotype_id: string;
  name: string;
  hdruk_url: string;
  overlap_jaccard: number;
  overlap_generated_in_phenotype: number;
  overlap_phenotype_in_generated: number;
  n_generated_codes: number;
  n_phenotype_codes: number;
  n_intersection: number;
  data_sources: string[];
  first_publication: string;
  relevance_rationale: string;
}

export async function getCrossReference(
  codelistId: string,
  refresh: boolean = false,
): Promise<CrossReferenceRow[]> {
  const params = refresh ? "?refresh=true" : "";
  const res = await fetch(
    `${API_BASE}/codelists/${codelistId}/cross-reference${params}`,
    AUTH_FETCH,
  );
  if (!res.ok) throw new Error(`Cross-reference failed: ${res.status}`);
  return res.json();
}

export async function discoverPhenotypes(
  query: string,
  topK: number = 5,
  signal?: AbortSignal,
): Promise<PhenotypeDiscoveryResult[]> {
  const params = new URLSearchParams({ query, top_k: String(topK) });
  const res = await fetch(`${API_BASE}/phenotypes/discover?${params.toString()}`, {
    ...AUTH_FETCH,
    signal,
  });
  if (!res.ok) {
    // Discovery is supplementary; surface the error to the caller but
    // the calling component should hide the sidebar rather than
    // showing a red banner — this is "browse-mode" content, not the
    // main search result the user clicked for.
    throw new Error(`Discover failed: ${res.status}`);
  }
  return res.json();
}

export async function exportCodes(
  searchId: string,
  outputFormat: "csv" | "xlsx" = "csv"
): Promise<Blob> {
  const res = await fetch(
    `${API_BASE}/export/${searchId}?output_format=${outputFormat}`,
    AUTH_FETCH
  );
  if (!res.ok) {
    throw new Error(`Export failed: ${res.status}`);
  }
  return res.blob();
}

// --- HITL: auth ------------------------------------------------------------

export interface User {
  id: number;
  name: string;
  email: string;
  role: "reviewer" | "admin";
}

export async function listDemoUsers(): Promise<User[]> {
  const res = await fetch(`${API_BASE}/auth/users`, AUTH_FETCH);
  if (!res.ok) throw new Error(`List users failed: ${res.status}`);
  return res.json();
}

export async function login(userId: number): Promise<User> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    ...AUTH_FETCH,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!res.ok) throw new Error(`Login failed: ${res.status}`);
  return res.json();
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { ...AUTH_FETCH, method: "POST" });
}

export async function getMe(): Promise<User | null> {
  const res = await fetch(`${API_BASE}/auth/me`, AUTH_FETCH);
  if (res.status === 401) return null;
  if (!res.ok) throw new Error(`Me failed: ${res.status}`);
  return res.json();
}

// --- HITL: codelists -------------------------------------------------------

export type CodelistStatus = "draft" | "in_review" | "approved" | "rejected";

export interface CodelistSummary {
  id: string;
  name: string;
  version: number;
  status: CodelistStatus;
  query: string;
  created_by: number;
  created_by_name: string | null;
  created_at: string;
  reviewed_by: number | null;
  reviewed_at: string | null;
  decision_count: number;
}

export interface CodelistDecision {
  id: number;
  code: string;
  term: string;
  vocabulary: string;
  ai_decision: "include" | "exclude" | "uncertain";
  ai_confidence: number;
  ai_rationale: string;
  human_decision: "include" | "exclude" | "uncertain";
  override_comment: string | null;
  sources: string[];
  is_umls_suggestion: number;
}

export interface AdoptedPhenotype {
  phenotype_id: string;
  name: string;
  hdruk_url: string;
  first_publication: string;
}

export interface Codelist extends CodelistSummary {
  review_notes: string | null;
  signature_hash: string | null;
  reviewed_by_name?: string | null;
  decisions: CodelistDecision[];
  adopted_phenotypes: AdoptedPhenotype[];
}

export interface AuditEvent {
  id: number;
  event: string;
  timestamp: string;
  user_id: number | null;
  user_name: string | null;
  details: Record<string, unknown>;
}

export async function listCodelists(opts: {
  mine?: boolean;
  status?: CodelistStatus;
  limit?: number;
} = {}): Promise<CodelistSummary[]> {
  const params = new URLSearchParams();
  if (opts.mine) params.set("mine", "true");
  if (opts.status) params.set("status", opts.status);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await fetch(
    `${API_BASE}/codelists${qs ? `?${qs}` : ""}`,
    AUTH_FETCH
  );
  if (!res.ok) throw new Error(`List codelists failed: ${res.status}`);
  return res.json();
}

export async function getCodelist(id: string): Promise<Codelist> {
  const res = await fetch(`${API_BASE}/codelists/${id}`, AUTH_FETCH);
  if (!res.ok) throw new Error(`Get codelist failed: ${res.status}`);
  return res.json();
}

export async function getAudit(id: string): Promise<AuditEvent[]> {
  const res = await fetch(`${API_BASE}/codelists/${id}/audit`, AUTH_FETCH);
  if (!res.ok) throw new Error(`Get audit failed: ${res.status}`);
  return res.json();
}

export async function createCodelist(
  searchId: string,
  name: string,
  adoptedPhenotypes: AdoptedPhenotype[] = [],
): Promise<Codelist> {
  const res = await fetch(`${API_BASE}/codelists`, {
    ...AUTH_FETCH,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      search_id: searchId,
      name,
      adopted_phenotypes: adoptedPhenotypes,
    }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Create codelist failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export interface ReviewDecisionInput {
  id: number;
  human_decision: "include" | "exclude" | "uncertain";
  override_comment?: string | null;
}

export interface ReviewResult {
  codelist_id: string;
  status: CodelistStatus;
  override_count: number;
  signature_hash: string | null;
  reviewed_by: string;
}

export async function submitReview(
  id: string,
  decisions: ReviewDecisionInput[],
  action: "approve" | "reject",
  notes?: string | null,
): Promise<ReviewResult> {
  const res = await fetch(`${API_BASE}/codelists/${id}/review`, {
    ...AUTH_FETCH,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decisions, action, notes }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Review failed: ${res.status} ${detail}`);
  }
  return res.json();
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

// all HITL endpoints need the session cookie — always include credentials
const AUTH_FETCH: RequestInit = { credentials: "include" };

// usage_status disambiguates the three meanings of usage_frequency=null
// (T31):
//   "counted"           - usage_frequency is a real number; render it.
//   "withheld_below_5"  - NHS Digital suppressed a count of 1-4; UI
//                         renders "<5" rather than "—".
//   "not_in_dataset"    - the code is absent from the upstream NHS
//                         Digital publication; UI renders "—".
// usage_source is the per-row attribution string the column-header
// tooltip cites (e.g. "NHS Digital primary care SNOMED reporting").
export type UsageStatus = "counted" | "withheld_below_5" | "not_in_dataset";
// Machine-readable setting for the Usage column's GP/HES badge.
// Decoupled from usage_source (which is the human-readable
// attribution string) so a future rename of the attribution string
// cannot silently break the badge logic.
export type UsageSetting = "primary_care" | "secondary_care_hes";

// dm+d four-level hierarchy badge (T37). Surfaced for dm+d rows; null
// for everything else, including BNF rows.
export type DmdLevel = "Ingredient" | "VTM" | "VMP" | "AMP";

export interface CodeResult {
  code: string;
  term: string;
  vocabulary: string;
  decision: "include" | "exclude" | "uncertain";
  confidence: number;
  rationale: string;
  sources: string[];
  usage_frequency: number | null;
  usage_status: UsageStatus | null;
  usage_source: string | null;
  usage_setting: UsageSetting | null;
  concept_id: number | null;
  dmd_level: DmdLevel | null;
}

// T30 — display sort for the live search page; not the codelist-review SortMode.
export type SortMode = "default" | "vocabulary" | "usage" | "confidence";

export const SORT_MODES: { value: SortMode; label: string }[] = [
  { value: "default",    label: "Default" },
  { value: "vocabulary", label: "Vocabulary" },
  { value: "usage",      label: "Usage" },
  { value: "confidence", label: "Confidence" },
];

export const DEFAULT_SORT_MODE: SortMode = "default";

// T37 disambiguation. Hand-mirrored from backend DisambiguationEntry in
// routes.py — keep in sync (no codegen).
export type DisambiguationReason =
  | "ambiguous_abbreviation"
  | "low_parse_confidence"
  | "non_english_input"
  | "possible_misspelling";

export interface DisambiguationEntry {
  original_term: string;
  interpreted_as: string;
  alternatives: string[];
  reason: DisambiguationReason;
  detected_language: string;
}

// hand-mirrored from routes.py ComorbiditySuggestion; keep in sync (no codegen)
export interface ComorbiditySuggestion {
  condition_name: string;
  rationale: string;
  confidence: number;
  suggested_by: string[];
  cui?: string | null;
}

export interface SearchResponse {
  search_id: string;
  query: string;
  conditions_parsed: Record<string, unknown>[];
  results: CodeResult[];
  summary: Record<string, unknown>;
  provenance_trail: Record<string, unknown>[];
  elapsed_seconds: number;
  include_descendants: boolean;
  disambiguation?: DisambiguationEntry[] | null;
  comorbidity_suggestions?: ComorbiditySuggestion[] | null;
}

export interface SearchOptions {
  // T29 — structured study-intent criteria. Empty arrays preserve the
  // pre-T29 request body exactly (the backend treats absent and []
  // identically).
  inclusions?: string[];
  exclusions?: string[];
  includeDescendants?: boolean;
}

export async function searchCodes(
  query: string,
  opts: SearchOptions = {},
): Promise<SearchResponse> {
  const body: Record<string, unknown> = { query };
  if (opts.inclusions && opts.inclusions.length > 0) body.inclusions = opts.inclusions;
  if (opts.exclusions && opts.exclusions.length > 0) body.exclusions = opts.exclusions;
  if (opts.includeDescendants !== undefined) body.include_descendants = opts.includeDescendants;
  const res = await fetch(`${API_BASE}/search`, {
    ...AUTH_FETCH,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`Search failed: ${res.status}`);
  }
  return res.json();
}

// Parse-only disambiguation for the type-ahead banner (T37). Runs the query
// parser alone (no retrieval/scoring) so the "did you mean…?" hint can surface
// before a full search. Returns [] when nothing is ambiguous.
export async function disambiguateQuery(
  query: string,
  signal?: AbortSignal,
): Promise<DisambiguationEntry[]> {
  const params = new URLSearchParams({ query });
  const res = await fetch(`${API_BASE}/disambiguate?${params.toString()}`, {
    ...AUTH_FETCH,
    signal,
  });
  if (!res.ok) throw new Error(`Disambiguate failed: ${res.status}`);
  return res.json();
}

// --- HDR UK phenotype discovery (T34) --------------------------------------

export interface PhenotypeDiscoveryResult {
  phenotype_id: string;
  phenotype_version_id: number | null;
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

// --- OHDSI concept-set JSON export ----------------------------------------
// Mirrors backend/app/exports/ohdsi.py::to_ohdsi_concept_set.
// UPPERCASE concept keys are the OHDSI shape; ATLAS pastes them as-is.

export interface OhdsiConceptItem {
  concept: {
    CONCEPT_ID: number;
    VOCABULARY_ID: string;
    CONCEPT_CODE: string;
    CONCEPT_NAME: string;
  };
  isExcluded: boolean;
  includeDescendants: boolean;
  includeMapped: boolean;
}

export interface OhdsiUnmappedRow {
  code: string;
  vocabulary: string;
  term: string;
  decision: "include" | "exclude";
}

export interface OhdsiExport {
  concept_set: {
    id: number;
    name: string;
    expression: { items: OhdsiConceptItem[] };
  };
  unmapped: OhdsiUnmappedRow[];
}

export async function exportCodesOhdsi(searchId: string): Promise<OhdsiExport> {
  const res = await fetch(
    `${API_BASE}/export/${searchId}?output_format=ohdsi`,
    AUTH_FETCH,
  );
  if (!res.ok) throw new Error(`OHDSI export failed: ${res.status}`);
  return res.json();
}

export async function exportCodelistOhdsi(codelistId: string): Promise<OhdsiExport> {
  const res = await fetch(
    `${API_BASE}/codelists/${codelistId}/export?format=ohdsi`,
    AUTH_FETCH,
  );
  if (!res.ok) throw new Error(`OHDSI export failed: ${res.status}`);
  return res.json();
}

export async function exportCodelistOpenCodelists(codelistId: string): Promise<Blob> {
  const res = await fetch(
    `${API_BASE}/codelists/${codelistId}/export.opencodelists.csv`,
    AUTH_FETCH,
  );
  if (!res.ok) {
    let detail = `OpenCodelists export failed: ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // non-JSON error body (e.g. proxy 502)
    }
    throw new Error(detail);
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

// T30: ``adjudication`` is the v2-only state that sits between
// in_review and approved when reviewers disagree at finalisation.
// Legacy v1 codelists never enter it; the union covers both flows
// because the same UI dispatches on signature_version.
export type CodelistStatus =
  | "draft"
  | "in_review"
  | "adjudication"
  | "approved"
  | "rejected";

// T30: vote labels match the include/exclude/uncertain decision
// values; the DB CHECK on decision_votes.vote enforces the same set.
export type VoteValue = "include" | "exclude" | "uncertain";

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
  // T32: 0/1 flag. Surfaced so the My Codelists list can render a
  // "hidden from gallery" indicator without re-fetching each row.
  private?: 0 | 1;
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
  dmd_level: DmdLevel | null;
}

export interface AdoptedPhenotype {
  phenotype_id: string;
  phenotype_version_id: number | null;
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
  // T29 — study-intent criteria captured at /api/search time and
  // persisted on the codelist. Empty arrays for pre-T29 codelists
  // (the column DEFAULT '[]' migration covers older rows).
  include_criteria: string[];
  exclude_criteria: string[];
  include_descendants: boolean;
  // T32 — owner-flippable opt-out from the public gallery. SQLite
  // INTEGER 0/1 over the JSON wire; coerce to bool at the use site.
  private?: 0 | 1;
  // T30 — v2 fields. For legacy v1 codelists: signature_version=1,
  // reviewer_ids=[], agreement_kappa=null. The UI dispatches on
  // signature_version, not on reviewer_ids length, because the
  // version is the immutable post-creation anchor.
  signature_version: 1 | 2;
  reviewer_ids: number[];
  agreement_kappa: number | null;
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

// T32 — owner-only opt-out from the public /gallery surface. The route
// 403s for non-owners and 404s for missing codelists; surfaced separately
// so the codelist-detail page can render distinct error states.
//
// The response's `private` field is the raw 0/1 int (same shape as
// list/detail GETs), so a caller can patch a list row in place without
// reconciling two formats. Coerce with `Boolean(...)` at the use site.
export async function setCodelistPrivacy(
  id: string,
  isPrivate: boolean,
): Promise<{ id: string; private: 0 | 1; status: CodelistStatus }> {
  const res = await fetch(`${API_BASE}/codelists/${id}/privacy`, {
    ...AUTH_FETCH,
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ private: isPrivate }),
  });
  if (!res.ok) throw new Error(`Privacy update failed: ${res.status}`);
  return res.json();
}

// --- T32: public gallery (no auth) ----------------------------------------
//
// Mirrors a redacted subset of the auth-side codelist read shape. Names
// are reduced to initials, override comments are gone, UMLS-suggestion
// rows are filtered out before the body leaves the API. `redacted: true`
// is set on the detail body so the gallery UI can surface that
// distinction to a visitor.

export interface PublicCodelistSummary {
  id: string;
  name: string;
  version: number;
  status: "approved";
  query: string;
  created_at: string;
  reviewed_at: string | null;
  signature_hash: string | null;
  decisions_count: number;
  included_count: number;
  created_by_initials: string;
  reviewed_by_initials: string;
}

// Same fields as CodelistDecision minus override_comment (redacted),
// minus is_umls_suggestion (the public route drops those rows entirely).
export interface PublicCodelistDecision {
  id: number;
  code: string;
  term: string;
  vocabulary: string;
  ai_decision: "include" | "exclude" | "uncertain";
  ai_confidence: number;
  ai_rationale: string;
  human_decision: "include" | "exclude" | "uncertain";
  sources: string[];
  concept_id: number | null;
  dmd_level: DmdLevel | null;
}

export interface PublicCodelist extends PublicCodelistSummary {
  redacted: true;
  decisions: PublicCodelistDecision[];
  adopted_phenotypes: AdoptedPhenotype[];
  include_criteria: string[];
  exclude_criteria: string[];
  include_descendants: boolean;
}

// PUBLIC_FETCH skips credentials so cookies aren't sent on the gallery
// surface; the routes don't read them but the cleaner separation matches
// the spirit of "this is the unauthenticated view".
const PUBLIC_FETCH: RequestInit = { credentials: "omit" };

export async function getPublicCount(): Promise<number> {
  const res = await fetch(`${API_BASE}/public/codelists/count`, PUBLIC_FETCH);
  if (!res.ok) throw new Error(`Public count failed: ${res.status}`);
  const body = (await res.json()) as { count: number };
  return body.count;
}

export async function listPublicCodelists(opts: {
  limit?: number;
  offset?: number;
} = {}): Promise<PublicCodelistSummary[]> {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.offset) params.set("offset", String(opts.offset));
  const qs = params.toString();
  const res = await fetch(
    `${API_BASE}/public/codelists${qs ? `?${qs}` : ""}`,
    PUBLIC_FETCH,
  );
  if (!res.ok) throw new Error(`List public codelists failed: ${res.status}`);
  return res.json();
}

export async function getPublicCodelist(id: string): Promise<PublicCodelist> {
  const res = await fetch(`${API_BASE}/public/codelists/${id}`, PUBLIC_FETCH);
  if (!res.ok) throw new Error(`Get public codelist failed: ${res.status}`);
  return res.json();
}

export async function exportPublicCodelistOhdsi(id: string): Promise<OhdsiExport> {
  const res = await fetch(
    `${API_BASE}/public/codelists/${id}/export?format=ohdsi`,
    PUBLIC_FETCH,
  );
  if (!res.ok) throw new Error(`Public OHDSI export failed: ${res.status}`);
  return res.json();
}

export async function exportPublicCodelistCsv(id: string): Promise<Blob> {
  const res = await fetch(
    `${API_BASE}/public/codelists/${id}/export?format=csv`,
    PUBLIC_FETCH,
  );
  if (!res.ok) throw new Error(`Public CSV export failed: ${res.status}`);
  return res.blob();
}

// --- T30: two-reviewer Delphi (v2 path) -----------------------------------

export interface VoteRow {
  decision_id: number;
  vote: VoteValue;
  comment: string | null;
}

export interface PeerVoteRow extends VoteRow {
  reviewer_id: number;
}

export interface ConsensusResolution {
  decision_id: number;
  final_decision: VoteValue;
  rationale: string;
}

export interface ProposedConsensus {
  proposer_id: number;
  proposer_name: string;
  resolutions: ConsensusResolution[];
  proposed_at: string;
}

// Caller-aware view of a v2 codelist's per-reviewer voting state.
// Matches ``backend/app/db/hitl_store.py::get_voting_state`` exactly.
// The peer_votes privacy filter is the load-bearing anchoring-bias
// guard from Watson 2017 — null until the caller has finalised.
export interface VotingState {
  status: CodelistStatus;
  signature_version: 1 | 2;
  reviewer_ids: number[];
  reviewer_names: Record<string, string>;  // user_id stringified by JSON
  caller_id: number;
  is_caller_a_reviewer: boolean;
  caller_finalised: boolean;
  peer_id: number | null;
  peer_name: string | null;
  peer_finalised: boolean;
  caller_votes: VoteRow[];
  peer_votes: PeerVoteRow[] | null;  // null until caller finalises
  disputed_decision_ids: number[];
  agreement_kappa: number | null;
  proposed_consensus: ProposedConsensus | null;
}

export async function getVotingState(id: string): Promise<VotingState> {
  const res = await fetch(
    `${API_BASE}/codelists/${id}/voting-state`,
    AUTH_FETCH,
  );
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Voting state failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export interface AssignReviewersResult {
  id: string;
  reviewer_ids: number[];
  status: CodelistStatus;
  signature_version: 1 | 2;
}

export async function assignReviewers(
  id: string,
  reviewerIds: number[],
): Promise<AssignReviewersResult> {
  const res = await fetch(`${API_BASE}/codelists/${id}/reviewers`, {
    ...AUTH_FETCH,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reviewer_ids: reviewerIds }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Assign reviewers failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export interface V2ReviewResult {
  codelist_id: string;
  status: CodelistStatus;
  is_final: boolean;
  agreement_kappa?: number | null;
  signature_hash?: string;
  disagreements?: number[];
  reviewer: string;
}

export async function submitV2Review(
  id: string,
  votes: VoteRow[],
  isFinal: boolean,
): Promise<V2ReviewResult> {
  const res = await fetch(`${API_BASE}/codelists/${id}/review`, {
    ...AUTH_FETCH,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ votes, is_final: isFinal }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Submit votes failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export interface ConsensusResult {
  status: CodelistStatus;
  acknowledged: boolean;
  signature_hash?: string;
}

export async function submitConsensus(
  id: string,
  resolutions: ConsensusResolution[],
  acknowledge: boolean,
): Promise<ConsensusResult> {
  const res = await fetch(`${API_BASE}/codelists/${id}/consensus`, {
    ...AUTH_FETCH,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolutions, acknowledge }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Consensus failed: ${res.status} ${detail}`);
  }
  return res.json();
}

export async function rejectCodelistV2(
  id: string,
  reason: string,
): Promise<{ status: CodelistStatus; reason: string }> {
  const res = await fetch(`${API_BASE}/codelists/${id}/reject`, {
    ...AUTH_FETCH,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Reject failed: ${res.status} ${detail}`);
  }
  return res.json();
}

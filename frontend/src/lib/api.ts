const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

export interface CodeResult {
  code: string;
  term: string;
  vocabulary: string;
  decision: "include" | "exclude" | "uncertain";
  confidence: number;
  rationale: string;
  sources: string[];
  usage_frequency: number | null;
  classifier_score: number | null;
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
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  if (!res.ok) {
    throw new Error(`Search failed: ${res.status}`);
  }
  return res.json();
}

export async function exportCodes(
  searchId: string,
  outputFormat: "csv" | "xlsx" = "csv"
): Promise<Blob> {
  const res = await fetch(
    `${API_BASE}/export/${searchId}?output_format=${outputFormat}`
  );
  if (!res.ok) {
    throw new Error(`Export failed: ${res.status}`);
  }
  return res.blob();
}

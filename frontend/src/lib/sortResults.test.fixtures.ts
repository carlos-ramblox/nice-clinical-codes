import type { CodeResult } from "./api";

// Fields sortResults never reads; stubbed to satisfy the CodeResult shape.
const STUB: Omit<
  CodeResult,
  "code" | "vocabulary" | "confidence" | "usage_frequency" | "usage_status"
> = {
  term: "stub",
  decision: "include",
  rationale: "",
  sources: [],
  usage_source: null,
  usage_setting: null,
  concept_id: null,
  dmd_level: null,
};

export const FIXTURE_RESULTS: CodeResult[] = [
  { ...STUB, code: "I10",      vocabulary: "ICD-10 (WHO)", confidence: 0.95, usage_frequency: 10000, usage_status: "counted" },
  { ...STUB, code: "44054006", vocabulary: "SNOMED CT",    confidence: 0.90, usage_frequency: 5000,  usage_status: "counted" },
  { ...STUB, code: "X99",      vocabulary: "OPCS-4",       confidence: 0.50, usage_frequency: null,  usage_status: "not_in_dataset" },
  { ...STUB, code: "Y99",      vocabulary: "SNOMED CT",    confidence: 0.30, usage_frequency: null,  usage_status: "withheld_below_5" },
  { ...STUB, code: "Z99",      vocabulary: "ICD-10 (WHO)", confidence: 0.99, usage_frequency: 50,    usage_status: "counted" },
];

export const FIXTURE_PARSED = [{ coding_systems: ["SNOMED", "ICD10"] }];

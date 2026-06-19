import type { CodeResult, SortMode } from "./api";

// Parser tokens → wire labels. Source of truth: backend/app/config.py:47-51.
const VOCAB_TOKEN_TO_LABEL: Record<string, string> = {
  SNOMED: "SNOMED CT",
  ICD10: "ICD-10 (WHO)",
  OPCS4: "OPCS-4",
};

const NON_ORDERABLE_USAGE_STATUSES = new Set(["withheld_below_5", "not_in_dataset"]);

function vocabPriorityOrder(parsedConditions: { coding_systems?: string[] }[]): string[] {
  const seen = new Set<string>();
  const order: string[] = [];
  for (const c of parsedConditions ?? []) {
    for (const tok of c.coding_systems ?? []) {
      const label = VOCAB_TOKEN_TO_LABEL[tok];
      if (label && !seen.has(label)) {
        seen.add(label);
        order.push(label);
      }
    }
  }
  return order;
}

export function sortResults(
  results: CodeResult[],
  mode: SortMode,
  parsedConditions: { coding_systems?: string[] }[],
): CodeResult[] {
  // Array.prototype.sort is stable; ties preserve response order.
  const arr = [...results];
  switch (mode) {
    case "default":
      return arr;
    case "vocabulary": {
      const priority = vocabPriorityOrder(parsedConditions);
      const rank = (vocab: string) => {
        const i = priority.indexOf(vocab);
        return i === -1 ? priority.length : i;
      };
      arr.sort((a, b) => {
        const ra = rank(a.vocabulary), rb = rank(b.vocabulary);
        if (ra !== rb) return ra - rb;
        // both outside priority list → alphabetical by vocab for stable behaviour
        if (ra === priority.length) return a.vocabulary.localeCompare(b.vocabulary);
        return 0;
      });
      return arr;
    }
    case "usage": {
      arr.sort((a, b) => {
        const aOrderable = a.usage_frequency != null && !NON_ORDERABLE_USAGE_STATUSES.has(a.usage_status ?? "");
        const bOrderable = b.usage_frequency != null && !NON_ORDERABLE_USAGE_STATUSES.has(b.usage_status ?? "");
        if (aOrderable && !bOrderable) return -1;
        if (!aOrderable && bOrderable) return 1;
        if (!aOrderable && !bOrderable) return 0;
        return (b.usage_frequency ?? 0) - (a.usage_frequency ?? 0);
      });
      return arr;
    }
    case "confidence": {
      arr.sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0));
      return arr;
    }
  }
}

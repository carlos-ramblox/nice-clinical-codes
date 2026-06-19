import { describe, it, expect } from "vitest";
import { sortResults } from "./sortResults";
import { FIXTURE_RESULTS, FIXTURE_PARSED } from "./sortResults.test.fixtures";

const codes = (rows: { code: string }[]) => rows.map((r) => r.code);

describe("sortResults", () => {
  it("default mode preserves the server response order", () => {
    expect(codes(sortResults(FIXTURE_RESULTS, "default", FIXTURE_PARSED))).toEqual([
      "I10", "44054006", "X99", "Y99", "Z99",
    ]);
  });

  it("vocabulary mode ranks query vocabs first then alphabetises the tail", () => {
    expect(codes(sortResults(FIXTURE_RESULTS, "vocabulary", FIXTURE_PARSED))).toEqual([
      "44054006", "Y99", "I10", "Z99", "X99",
    ]);
  });

  it("usage mode sorts by frequency desc with nulls/withheld at the bottom", () => {
    expect(codes(sortResults(FIXTURE_RESULTS, "usage", FIXTURE_PARSED))).toEqual([
      "I10", "44054006", "Z99", "X99", "Y99",
    ]);
  });

  it("confidence mode sorts by confidence descending", () => {
    expect(codes(sortResults(FIXTURE_RESULTS, "confidence", FIXTURE_PARSED))).toEqual([
      "Z99", "I10", "44054006", "X99", "Y99",
    ]);
  });
});

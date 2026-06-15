import type { DisambiguationEntry } from "@/lib/api";

// "Did you mean…?" aside (T37). Two variants:
//   "result"   — shown alongside results after a search ran; states the
//                interpretation used and offers alternatives as re-runs.
//   "preflight"— shown at type-time before a search; surfaces the ambiguity
//                up front so the user picks the right term before spending a
//                full pipeline run on a best guess.
// Renders nothing when there is nothing ambiguous, so the happy path stays
// banner-free.
export function DidYouMeanBanner({
  entries,
  onReRun,
  variant = "result",
}: {
  entries: DisambiguationEntry[];
  onReRun: (term: string) => void;
  variant?: "result" | "preflight";
}) {
  if (entries.length === 0) return null;

  const altButton = (term: string, key: string) => (
    <button
      key={key}
      type="button"
      onClick={() => onReRun(term)}
      className="mx-0.5 px-2 py-0.5 border border-gray-300 bg-white text-[#005EA5] rounded hover:bg-[#005EA5] hover:text-white transition-colors focus:outline-none focus:ring-2 focus:ring-[#005EA5]"
    >
      {term}
    </button>
  );

  return (
    <aside
      aria-label="Query interpretation suggestions"
      className="max-w-5xl mx-auto mb-6 border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600"
    >
      <ul className="space-y-1.5">
        {entries.map((e, i) => {
          // Drop any alternative identical to the chosen interpretation (e.g.
          // a spelling correction equals interpreted_as) so we never offer a
          // re-run to the same term.
          const alts = e.alternatives.filter(
            (a) => a.toLowerCase() !== e.interpreted_as.toLowerCase(),
          );
          if (variant === "preflight") {
            // Pre-search: offer the best guess and every alternative as
            // equal, one-click choices.
            const choices = [e.interpreted_as, ...alts];
            return (
              <li key={`${e.original_term}-${i}`}>
                <span className="font-medium">&ldquo;{e.original_term}&rdquo;</span> looks
                ambiguous. Search for: {choices.map((c, j) => altButton(c, `${c}-${j}`))}?
              </li>
            );
          }
          return (
            <li key={`${e.original_term}-${i}`}>
              Interpreted{" "}
              <span className="font-medium">&ldquo;{e.original_term}&rdquo;</span> as{" "}
              <span className="font-medium">&ldquo;{e.interpreted_as}&rdquo;</span>.
              {alts.length > 0 && (
                <>
                  {" "}
                  Did you mean: {alts.map((a, j) => altButton(a, `${a}-${j}`))}?
                </>
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

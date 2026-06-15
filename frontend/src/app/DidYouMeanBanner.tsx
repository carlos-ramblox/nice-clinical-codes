import type { DisambiguationEntry } from "@/lib/api";

// Non-blocking "Did you mean…?" aside (T37). Informational only: the
// response always carries scored results; this offers one-click re-runs
// against an alternative interpretation. Renders nothing when there is
// nothing ambiguous, so the happy path stays banner-free.
export function DidYouMeanBanner({
  entries,
  onReRun,
}: {
  entries: DisambiguationEntry[];
  onReRun: (alt: string) => void;
}) {
  if (entries.length === 0) return null;

  return (
    <aside
      aria-label="Query interpretation suggestions"
      className="max-w-5xl mx-auto mb-6 border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600"
    >
      <ul className="space-y-1.5">
        {entries.map((e, i) => (
          <li key={`${e.original_term}-${i}`}>
            Interpreted{" "}
            <span className="font-medium">&ldquo;{e.original_term}&rdquo;</span> as{" "}
            <span className="font-medium">&ldquo;{e.interpreted_as}&rdquo;</span>.
            {e.alternatives.length > 0 && (
              <>
                {" "}
                Did you mean:{" "}
                {e.alternatives.map((alt, j) => (
                  <button
                    key={`${alt}-${j}`}
                    type="button"
                    onClick={() => onReRun(alt)}
                    className="mx-0.5 px-2 py-0.5 border border-gray-300 bg-white text-[#005EA5] rounded hover:bg-[#005EA5] hover:text-white transition-colors focus:outline-none focus:ring-2 focus:ring-[#005EA5]"
                  >
                    {alt}
                  </button>
                ))}
                ?
              </>
            )}
          </li>
        ))}
      </ul>
    </aside>
  );
}

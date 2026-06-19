"use client";

import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { SORT_MODES, type SortMode } from "@/lib/api";

function SortByControl({
  value,
  onChange,
}: {
  value: SortMode;
  onChange: (m: SortMode) => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const select = (mode: SortMode) => {
    onChange(mode);
    const next = new URLSearchParams(params.toString());
    if (mode === "default") next.delete("sort");
    else next.set("sort", mode);
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  return (
    <div className="flex items-center gap-1">
      <span className="text-xs text-gray-500 mr-1">Sort by:</span>
      {SORT_MODES.map((m) => (
        <button
          key={m.value}
          onClick={() => select(m.value)}
          className={`px-3 py-1 text-xs font-medium transition-colors ${
            value === m.value
              ? "bg-[#005EA5] text-white"
              : "bg-gray-100 text-gray-700 hover:bg-gray-200"
          }`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}

export function SortBy(props: { value: SortMode; onChange: (m: SortMode) => void }) {
  return (
    <Suspense fallback={null}>
      <SortByControl {...props} />
    </Suspense>
  );
}

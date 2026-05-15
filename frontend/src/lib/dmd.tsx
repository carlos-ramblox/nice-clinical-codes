import type { DmdLevel } from "@/lib/api";

export const DMD_LEVEL_TOOLTIPS: Record<DmdLevel, string> = {
  Ingredient: "Ingredient — chemical substance. Broadest level; cohort captures every brand, strength, and formulation.",
  VTM: "VTM (Virtual Therapeutic Moiety) — generic substance without route or strength.",
  VMP: "VMP (Virtual Medicinal Product) — generic with route and strength (e.g. 'Metformin 500mg tablets').",
  AMP: "AMP (Actual Medicinal Product) — brand-specific (e.g. 'Glucophage 500mg tablets, Merck Serono').",
};

const BASE_CLASSES = "rounded bg-indigo-100 text-indigo-800 border border-indigo-300";

export function DmdLevelBadge({
  level,
  compact = false,
}: {
  level: DmdLevel;
  compact?: boolean;
}) {
  const sizing = compact
    ? "ml-1 px-1"
    : "ml-1.5 inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium";
  return (
    <span className={`${sizing} ${BASE_CLASSES}`} title={DMD_LEVEL_TOOLTIPS[level]}>
      {level}
    </span>
  );
}

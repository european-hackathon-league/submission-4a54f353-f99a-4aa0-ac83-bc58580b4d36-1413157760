import { changeAmbitionLabel, costSensitivityLabel, marketLabel, positioningLabel, releaseLabel, targetCustomerLabel, type BriefFieldKey, type BriefValues, type Market, type ReleaseTarget } from "@/contracts";

/**
 * Where the onboarding form starts for questions nobody answered yet (a brief pre-filled
 * from data holds only market weights, the protect list and the don't-fight-on list).
 * These are a starting point, never stored until the PM saves the brief.
 */
export const BRIEF_START: BriefValues = {
  thesis: "",
  targetCustomer: { who: "both", positioning: "premium" },
  marketWeights: { EU: 0.25, US: 0.25, CN: 0.25, RoW: 0.25 },
  changeAmbition: { level: "balanced", innovationSlots: 3 },
  protectList: [],
  dontFightOn: [],
  givens: { platform: "", sop: "", frozen: [], openReleases: ["next_gen", "lci", "ota"], cycle: "WLTP" },
  costEnvelope: { sensitivity: "medium", perCarBand: "" },
};

/** Short human text for a brief value (brief page, conflicts, proposals). UI-only. */
export function showBriefValue(field: BriefFieldKey | string, v: unknown): string {
  if (v === undefined || v === null) return "—";
  const x = v as Record<string, unknown>;
  switch (field) {
    case "targetCustomer":
      return `${targetCustomerLabel[x.who as "both"]} · ${positioningLabel[x.positioning as "base"]}`;
    case "changeAmbition":
      return `${changeAmbitionLabel[x.level as "balanced"]}, ${x.innovationSlots} innovation${x.innovationSlots === 1 ? "" : "s"}`;
    case "costEnvelope":
      return `${costSensitivityLabel[x.sensitivity as "low"]} sensitivity · ${x.perCarBand}`;
    case "marketWeights":
      return (Object.entries(x) as [Market, number][])
        .filter(([, w]) => Number(w) > 0)
        .sort((a, b) => Number(b[1]) - Number(a[1]))
        .map(([k, w]) => `${marketLabel[k]} ${Math.round(Number(w) * 100)}%`)
        .join(" · ");
    case "givens":
      return `${x.platform ? `${x.platform}; ` : ""}SOP ${x.sop}; frozen: ${(x.frozen as string[]).join(", ") || "nothing"}; open: ${(x.openReleases as ReleaseTarget[]).map((r) => releaseLabel[r]).join(", ")}; cycle ${x.cycle}`;
    default:
      return Array.isArray(v) ? v.join(" · ") : String(v);
  }
}

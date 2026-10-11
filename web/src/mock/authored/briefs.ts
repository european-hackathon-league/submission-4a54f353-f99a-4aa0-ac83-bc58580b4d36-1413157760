import type { BriefFieldKey, BriefValues, CaseStance, Series } from "@/contracts";

/**
 * Layer 1 (series briefs) and seeded layer-2 stances for the demo personas.
 * The seed fills the data-derived fields (market weights from 2030 sales,
 * protect list from the vehicle plan, SOP from the plan) and their meta.
 * Wei has no overlay on purpose: he onboards live by pasting a strategy doc.
 */

export interface BriefSpec {
  id: string;
  series: Series;
  ownerId: string;
  createdAt: string;
  /** Everything except the fields the seed derives from data. */
  values: Omit<BriefValues, "marketWeights" | "protectList" | "givens"> & { givens: Omit<BriefValues["givens"], "sop"> };
  /** Fields the PM typed (source "pm"); the rest are "brand" or "data". */
  inheritedFromBrand: BriefFieldKey[];
}

export const briefSpecs: BriefSpec[] = [
  {
    id: "BRIEF-G60",
    series: "G60",
    ownerId: "u-lena",
    createdAt: "2026-09-03T08:30:00Z",
    values: {
      thesis: "The most complete business saloon: keep the driving dynamics, fix the everyday annoyances, close the range gap to the A6 e-tron.",
      targetCustomer: { who: "keep_owners", positioning: "premium" },
      changeAmbition: { level: "evolution", innovationSlots: 2 },
      dontFightOn: ["Massage and lounge comfort (Mercedes owns it)", "Screen count race of the Chinese EV brands"],
      givens: {
        platform: "CLAR successor with Neue Klasse electrics for the BEV",
        frozen: ["Platform and battery package for the mid-life update"],
        openReleases: ["next_gen", "ota"],
        cycle: "WLTP",
      },
      costEnvelope: { sensitivity: "medium", perCarBand: "≤ +3% material cost vs. today" },
    },
    inheritedFromBrand: ["dontFightOn"],
  },
  {
    id: "BRIEF-F70",
    series: "F70",
    ownerId: "u-sofia",
    createdAt: "2026-09-04T09:00:00Z",
    values: {
      thesis: "Keep the sportiest compact and win back value-minded buyers we lose to MG4 and BYD Dolphin.",
      targetCustomer: { who: "both", positioning: "base" },
      changeAmbition: { level: "evolution", innovationSlots: 1 },
      dontFightOn: ["Lowest price in the segment"],
      givens: { platform: "FAAR successor", frozen: [], openReleases: ["next_gen", "lci", "ota"], cycle: "WLTP" },
      costEnvelope: { sensitivity: "high", perCarBand: "≤ +1% material cost vs. today" },
    },
    inheritedFromBrand: [],
  },
  {
    id: "BRIEF-G70",
    series: "G70",
    ownerId: "u-daniel",
    createdAt: "2026-09-04T11:00:00Z",
    values: {
      thesis: "A mid-life update that makes the flagship feel effortless: rear comfort, quietness and tech without menus.",
      targetCustomer: { who: "keep_owners", positioning: "luxury" },
      changeAmbition: { level: "evolution", innovationSlots: 1 },
      dontFightOn: ["Massage and lounge comfort (Mercedes owns it)"],
      givens: { platform: "Current G70 platform (frozen for the mid-life update)", frozen: ["Body structure", "Powertrain"], openReleases: ["lci", "ota"], cycle: "WLTP" },
      costEnvelope: { sensitivity: "low", perCarBand: "Flexible within the luxury budget" },
    },
    inheritedFromBrand: ["dontFightOn"],
  },
];

/** Seeded layer-2 stances (one PM, one case). Lena's climate stance shows overrides and inside knowledge. */
export const stanceSpecs: Record<string, CaseStance[]> = {
  "CASE-G60-0005": [
    {
      authorId: "u-lena",
      overrides: { costEnvelope: { sensitivity: "low", perCarBand: "Up to €25 per car for the climate bar" } },
      overrideReasons: { costEnvelope: "Climate is a top-3 complaint in the US; Controlling already accepted €18–25 per car." },
      evidenceTakes: [{ id: "ET-1", text: "Focus on BEV owners: pre-conditioning expectations are highest for the i5", effect: "focus", powertrain: "BEV" }],
      inside: [{ id: "IN-1", kind: "engineering_on_it", text: "Climate team is validating a new compressor generation for the successor." }],
      ambition: "match",
      pendingShape: [],
      updatedAt: "2026-10-10T15:20:00Z",
    },
  ],
};

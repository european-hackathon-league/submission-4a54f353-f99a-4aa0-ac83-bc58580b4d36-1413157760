import type { BriefConflict, BriefFieldKey, BriefValues, Dataset, ResolvedBrief, Series, SeriesBrief, User } from "@/contracts";
import { BriefFieldKey as BriefFieldKeys, briefFieldLabel, changeAmbitionLabel, costSensitivityLabel, positioningLabel, releaseLabel, targetCustomerLabel } from "@/contracts";
import type { ProposalDraft } from "./ai/chat";

/** Layer 1: shared brief per series + regional overlay per PM. */

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function sharedBrief(d: Dataset, series: Series): SeriesBrief | undefined {
  return d.briefs.find((b) => b.kind === "shared" && b.series === series);
}

export function overlayOf(d: Dataset, series: Series, userId: string): SeriesBrief | undefined {
  return d.briefs.find((b) => b.kind === "overlay" && b.series === series && b.ownerId === userId);
}

export function conflictsOf(shared: SeriesBrief, overlay: SeriesBrief): BriefConflict[] {
  return BriefFieldKeys.options
    .filter((k) => overlay.values[k] !== undefined && !same(overlay.values[k], shared.values[k]) && overlay.meta[k]?.resolution?.status !== "aligned")
    .map((k) => ({
      field: k,
      overlayId: overlay.id,
      sharedValue: shared.values[k],
      overlayValue: overlay.values[k],
      reason: overlay.meta[k]?.reason ?? "",
      status: overlay.meta[k]?.resolution?.status === "regional_variant" ? ("regional_variant" as const) : ("open" as const),
    }));
}

/** The brief a user drafts with for one series: shared values, their overlay on top. */
export function resolveBrief(d: Dataset, series: Series, userId: string): ResolvedBrief | null {
  const shared = sharedBrief(d, series);
  if (!shared || !isComplete(shared.values)) return null;
  const overlay = overlayOf(d, series, userId);
  const values = { ...shared.values, ...(overlay?.values ?? {}) } as BriefValues;
  const origin = Object.fromEntries(
    BriefFieldKeys.options.map((k) => {
      if (overlay?.values[k] !== undefined) return [k, "overlay"];
      const src = shared.meta[k]?.source;
      return [k, src === "data" ? "data" : src === "brand" ? "brand" : "shared"];
    }),
  ) as ResolvedBrief["origin"];
  const confirmed = BriefFieldKeys.options.every((k) => shared.meta[k]?.confirmed) && (!overlay || Object.values(overlay.meta).every((m) => m?.confirmed));
  return { series, region: overlay?.region ?? "global", sharedId: shared.id, overlayId: overlay?.id ?? null, values, origin, confirmed, conflicts: overlay ? conflictsOf(shared, overlay) : [] };
}

function isComplete(v: Partial<BriefValues>): v is BriefValues {
  return BriefFieldKeys.options.every((k) => v[k] !== undefined);
}

/** A vehicle PM needs onboarding until a shared brief exists and, if someone else owns it, they have their own overlay. */
export function needsOnboarding(d: Dataset, user: User): boolean {
  if (user.role !== "vehicle_pm") return false;
  const shared = sharedBrief(d, user.series[0]);
  if (!shared) return true;
  if (shared.ownerId === user.id) return !BriefFieldKeys.options.every((k) => shared.meta[k]?.confirmed);
  const overlay = overlayOf(d, user.series[0], user.id);
  return !overlay || !Object.values(overlay.meta).every((m) => m?.confirmed);
}

/** Pre-fill for a new regional overlay: the region's market weight, to confirm. */
export function overlayPrefill(user: User): { values: Partial<BriefValues>; reasons: Partial<Record<BriefFieldKey, string>> } {
  if (user.region === "CN")
    return {
      values: { marketWeights: { EU: 0, US: 0, CN: 1, RoW: 0 } },
      reasons: { marketWeights: "China PM: your variants are judged on the Chinese market only." },
    };
  return { values: {}, reasons: {} };
}

// ---------- "Paste your strategy doc": deterministic extraction into field proposals ----------
function line(text: string, label: RegExp): string | null {
  const m = text.split(/\n+/).find((l) => label.test(l));
  return m ? m.replace(label, "").replace(/^[\s:–-]+/, "").trim() : null;
}

/** Proposals for the fields the doc states. The real brain replaces this with an LLM extraction (same output). */
export function extractFromDoc(text: string, current: BriefValues, briefId: string): ProposalDraft[] {
  const out: ProposalDraft[] = [];
  const t = text.toLowerCase();
  const propose = (field: BriefFieldKey, after: unknown, summary: string, quote: string | null) => {
    if (same(after, current[field])) return;
    out.push({
      objectType: "brief",
      objectId: briefId,
      source: "strategy_doc",
      kind: "brief_field",
      summary: `${briefFieldLabel[field]}: ${summary}`,
      changes: [{ field, before: current[field], after }],
      citations: quote ? [quote.slice(0, 200)] : [],
    });
  };

  const thesis = line(text, /^\s*thesis\b/i);
  if (thesis) propose("thesis", thesis, `"${thesis.slice(0, 80)}${thesis.length > 80 ? "…" : ""}"`, thesis);

  const tcLine = line(text, /^\s*target customer\b/i) ?? "";
  const who = /conquest|win buyers|new customers/i.test(tcLine || t) ? "conquest" : /keep|loyal|renew/i.test(tcLine) ? "keep_owners" : null;
  const tier = /\bluxury\b/i.test(tcLine) ? "luxury" : /\bpremium\b/i.test(tcLine) ? "premium" : /\bbase\b|entry/i.test(tcLine) ? "base" : null;
  if (who || tier) {
    const after = { who: who ?? current.targetCustomer.who, positioning: tier ?? current.targetCustomer.positioning };
    propose("targetCustomer", after, `${targetCustomerLabel[after.who]} · ${positioningLabel[after.positioning]}`, tcLine || null);
  }

  const ambLine = line(text, /^\s*ambition\b/i) ?? "";
  const level = /revolution/i.test(ambLine) ? "revolution" : /evolution/i.test(ambLine) ? "evolution" : null;
  const slots = ambLine.match(/(\d+)\s+(?:real\s+)?innovations?/i);
  if (level || slots) {
    const after = { level: level ?? current.changeAmbition.level, innovationSlots: slots ? Number(slots[1]) : current.changeAmbition.innovationSlots };
    propose("changeAmbition", after, `${changeAmbitionLabel[after.level]}, ${after.innovationSlots} innovations`, ambLine || null);
  }

  const givLine = line(text, /^\s*givens\b/i) ?? "";
  if (givLine) {
    const cycle = /\bcltc\b/i.test(givLine) ? "CLTC" : /\bepa\b/i.test(givLine) ? "EPA" : /\bwltp\b/i.test(givLine) ? "WLTP" : current.givens.cycle;
    const open = new Set(current.givens.openReleases);
    if (/mid-life update is (?:still )?open|lci (?:is )?open/i.test(givLine)) open.add("lci");
    const after = { ...current.givens, cycle, openReleases: (["next_gen", "lci", "ota"] as const).filter((r) => open.has(r)) };
    propose("givens", after, `${cycle} cycle; still open: ${after.openReleases.map((r) => releaseLabel[r]).join(", ")}`, givLine);
  }

  const costLine = line(text, /^\s*cost\b/i) ?? "";
  const sens = /low sensitivity|not sensitive/i.test(costLine) ? "low" : /high sensitivity|very sensitive/i.test(costLine) ? "high" : /medium/i.test(costLine) ? "medium" : null;
  if (sens) {
    const band = costLine.split(/[,.]/).slice(1).join(",").trim().replace(/[\s,.;]+$/, "") || current.costEnvelope.perCarBand;
    propose("costEnvelope", { sensitivity: sens, perCarBand: band }, `${costSensitivityLabel[sens]} sensitivity`, costLine);
  }

  const dontLine = line(text, /^\s*do not fight on\b|^\s*don't fight on\b/i);
  if (dontLine) {
    const items = dontLine.split(/[;,]/).map((s) => s.replace(/\.$/, "").trim()).filter(Boolean);
    propose("dontFightOn", items, items.join("; "), dontLine);
  }

  return out;
}
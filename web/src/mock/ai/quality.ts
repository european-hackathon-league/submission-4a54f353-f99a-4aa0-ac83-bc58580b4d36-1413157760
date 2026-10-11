import type { CheckIssue, CheckKind, Dataset, Requirement, RequirementCheck } from "@/contracts";
import { CheckKind as CheckKinds, marketLabel } from "@/contracts";
import { liveChatEnabled, liveJson } from "./live";

/**
 * Quality check on a requirement before the PM approves it: the same kind of
 * checks engineering runs on requirements in Codebeamer (vague wording,
 * duplicates, contradictions), plus the challenge scope (customer-facing only).
 * Deterministic rules always run; an LLM pass adds issues when a key is set.
 * The check only warns: the PM may approve with open issues and a reason.
 */

const VAGUE = [
  "improved",
  "improve",
  "better",
  "enhanced",
  "enhance",
  "user-friendly",
  "intuitive",
  "intuitively",
  "easy",
  "easier",
  "seamless",
  "seamlessly",
  "optimal",
  "optimized",
  "modern",
  "as needed",
  "adequate",
  "sufficient",
  "premium feel",
];

const OUT_OF_SCOPE: [RegExp, string][] = [
  [/homologat\w*/i, "homologation"],
  [/type approval/i, "type approval"],
  [/\bUN[- ]?R\s?\d+/i, "UN regulation"],
  [/ISO 26262/i, "functional safety (ISO 26262)"],
  [/emission/i, "emissions"],
  [/crash test/i, "crash testing"],
  [/\bECU\b/, "ECU / electronics architecture"],
  [/\bCAN[- ]bus\b/i, "vehicle bus architecture"],
  [/\bsupplier\b/i, "supplier topic"],
  [/part number/i, "part numbers"],
  [/\bpric(e|ing)\b/i, "pricing"],
  [/business case/i, "business case"],
];

const STOP = new Set(
  "with that this from have will when within than they them their there which while after before into over under about every each other only also very more most less least must should shall could would per all any can the and for are not".split(" "),
);

function tokens(text: string) {
  return new Set(
    text
      .toLowerCase()
      .replace(/[^a-z0-9äöüß ]+/g, " ")
      .split(/\s+/)
      .filter((w) => w.length >= 4 && !STOP.has(w)),
  );
}

function overlap(a: Set<string>, b: Set<string>) {
  if (!a.size || !b.size) return 0;
  let inter = 0;
  a.forEach((w) => b.has(w) && inter++);
  return inter / (a.size + b.size - inter);
}

const hasNumber = (s: string) => /\d/.test(s);
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

/** What engineering builds against. Metric questions are left out: they often quote customers ("'Not user-friendly' verbatims"), and assumptions may name a regulatory risk on purpose. */
function textFields(r: Requirement): [string, string][] {
  return [
    ["title", r.title],
    ["description", r.description],
    ...r.acceptanceCriteria.map((t) => ["acceptanceCriteria", t] as [string, string]),
  ];
}

export function ruleCheck(d: Dataset, r: Requirement, ranAt: string): RequirementCheck {
  const issues: Omit<CheckIssue, "id">[] = [];
  const c = d.cases.find((x) => x.id === r.caseId);
  const numericTarget = r.metrics.some((m) => hasNumber(m.target));

  // Vague wording: words an engineer cannot test against.
  const seenWords = new Set<string>();
  for (const [field, text] of textFields(r)) {
    for (const w of VAGUE) {
      if (seenWords.has(w)) continue;
      if (new RegExp(`(^|[^a-z])${w.replace(/[-\s]/g, "[-\\s]")}([^a-z]|$)`, "i").test(text)) {
        seenWords.add(w);
        issues.push({
          kind: "vague_word",
          severity: numericTarget ? "amber" : "red",
          field,
          message: `"${w}" is not testable${numericTarget ? "" : ", and no metric has a numeric target"}.`,
          suggestion: "Replace it with a measurable target (a time, share, count or temperature) or move it to a metric.",
        });
      }
    }
  }

  // Scope: the market scope should match where the evidence comes from.
  if (c) {
    const counted = c.figures.perMarket.filter((m) => m.market !== "unknown");
    const total = counted.reduce((s, m) => s + m.customers, 0);
    const top = [...counted].sort((a, b) => b.customers - a.customers)[0];
    if (r.marketScope === "global" && top && total >= 5 && top.customers / total > 0.8) {
      issues.push({
        kind: "missing_scope",
        severity: "amber",
        field: "marketScope",
        message: `Scope is global, but ${Math.round((top.customers / total) * 100)}% of the ${total} verbatims come from ${marketLabel[top.market]}.`,
        suggestion: `Confirm the other markets (e.g. an open question to the market PMs) or narrow the scope to ${marketLabel[top.market]}.`,
      });
    }
    const cn = counted.find((m) => m.market === "CN")?.customers ?? 0;
    if (r.marketScope === "CN" && cn < 3) {
      issues.push({
        kind: "missing_scope",
        severity: "amber",
        field: "marketScope",
        message: `China scope, but only ${cn} Chinese verbatim${cn === 1 ? "" : "s"} in the evidence.`,
        suggestion: "Add Chinese evidence (dealer feedback, Autohome, the CN study) before sign-off.",
      });
    }
  }

  // Out of scope: the challenge covers customer requirements only.
  const seenScope = new Set<string>();
  for (const [field, text] of [...textFields(r), ...r.metrics.map((m) => ["metrics", m.target] as [string, string])]) {
    for (const [re, label] of OUT_OF_SCOPE) {
      if (seenScope.has(label) || !re.test(text)) continue;
      seenScope.add(label);
      issues.push({
        kind: "out_of_scope",
        severity: "red",
        field,
        message: `Mentions ${label}. Regulatory, technical and pricing requirements are out of scope for the PM requirement.`,
        suggestion: "Rephrase as what the customer experiences, and hand the technical part to engineering.",
      });
    }
  }

  // Duplicates and Baukasten: compare with every other live requirement.
  const mine = tokens(`${r.title} ${r.description}`);
  for (const o of d.requirements) {
    if (o.id === r.id || o.status === "rejected" || o.variantOf === r.id || r.variantOf === o.id) continue;
    const score = overlap(mine, tokens(`${o.title} ${o.description}`));
    if (score < 0.3) continue;
    if (o.series === r.series) {
      issues.push({
        kind: "duplicate",
        severity: "red",
        field: "description",
        relatedId: o.id,
        message: `${o.id} already covers much of this (${Math.round(score * 100)}% word overlap): "${o.title}".`,
        suggestion: `Merge into ${o.id} or make the difference explicit in the title.`,
      });
    } else {
      issues.push({
        kind: "baukasten",
        severity: "amber",
        field: "description",
        relatedId: o.id,
        message: `${o.series} has a similar requirement: ${o.id} "${o.title}".`,
        suggestion: "Align the targets across series so the module can be shared.",
      });
    }
  }

  // Baukasten: the problem shows up in other series, so the module is likely shared.
  const crossPm = d.users.find((u) => u.role === "attribute_pm");
  const sharedText = /baukasten|shared (module|with)|same module/i.test(`${r.description} ${r.acceptanceCriteria.join(" ")}`);
  if (c && (c.relatedSeries.length || sharedText) && !issues.some((i) => i.kind === "baukasten")) {
    issues.push({
      kind: "baukasten",
      severity: "amber",
      field: "description",
      message: `Shared across series (${[c.series, ...c.relatedSeries].join(", ")}): a Baukasten feature.`,
      suggestion: crossPm ? `Agree the target with ${crossPm.name} (${crossPm.title}) before approval.` : "Agree the target with the cross-series PM before approval.",
    });
  }

  // Conflicts: the same metric with a different target in the same car area, or a planned removal.
  for (const o of d.requirements) {
    if (o.id === r.id || o.status === "rejected" || o.carArea !== r.carArea) continue;
    for (const m of r.metrics) {
      const other = o.metrics.find((x) => norm(x.question) === norm(m.question) && norm(x.target) !== norm(m.target));
      if (other) {
        issues.push({
          kind: "conflict",
          severity: "amber",
          field: "metrics",
          relatedId: o.id,
          message: `${o.id} sets "${other.question}" to ${other.target}; this requirement says ${m.target}.`,
          suggestion: "Pick one target or document why the series differ.",
        });
      }
    }
  }
  const plan = d.plans.find((p) => p.series === r.series);
  for (const f of plan?.features ?? []) {
    if (f.action !== "remove" || f.requirementIds.includes(r.id)) continue;
    const name = tokens(f.name);
    if (name.size >= 1 && [...name].every((w) => mine.has(w))) {
      issues.push({
        kind: "conflict",
        severity: "amber",
        field: "description",
        relatedId: f.id,
        message: `The vehicle plan removes "${f.name}", which this requirement relies on.`,
        suggestion: "Keep the feature in the plan or drop the dependency.",
      });
    }
  }

  return { requirementId: r.id, ranAt, engine: "rules", issues: issues.map((i, n) => ({ ...i, id: `${r.id}-${i.kind}-${n + 1}` })) };
}

const LLM_SYSTEM = `You review one product requirement for a BMW vehicle before the product manager approves it. Engineers will challenge every word.
Report only issues of these kinds: ${CheckKinds.options.join(", ")}.
- vague_word: wording that cannot be tested (e.g. "better", "intuitive").
- missing_scope: market or derivative unclear or not backed by the evidence.
- out_of_scope: regulatory, homologation, technical/engineering or pricing content (only customer-facing requirements are in scope).
- duplicate / conflict / baukasten: overlap or contradiction with the other requirements listed (use their IDs).
Do not repeat issues already found by the rules. Return JSON only: {"issues":[{"kind":"...","severity":"red|amber","field":"title|description|metrics|acceptanceCriteria|marketScope","message":"...","relatedId":"optional ID","suggestion":"..."}]}. Return {"issues":[]} if nothing to add.`;

export async function checkRequirement(d: Dataset, r: Requirement, ranAt: string, useLlm: boolean): Promise<RequirementCheck> {
  const base = ruleCheck(d, r, ranAt);
  if (!useLlm || !liveChatEnabled()) return base;
  const others = d.requirements
    .filter((o) => o.id !== r.id && o.status !== "rejected")
    .map((o) => `${o.id} (${o.series}, ${o.carArea}): ${o.title}`)
    .join("\n");
  const prompt = [
    `REQUIREMENT ${r.id} (${r.series}, scope ${r.marketScope}, area ${r.carArea})`,
    `Title: ${r.title}`,
    `Description: ${r.description}`,
    `Metrics: ${r.metrics.map((m) => `${m.question} -> ${m.target} (${m.basis})`).join("; ")}`,
    `Acceptance criteria: ${r.acceptanceCriteria.join("; ")}`,
    `Already found by rules: ${base.issues.map((i) => `${i.kind}: ${i.message}`).join(" | ") || "none"}`,
    `OTHER REQUIREMENTS:\n${others}`,
  ].join("\n");
  const raw = await liveJson(LLM_SYSTEM, prompt);
  if (!raw) return base;
  try {
    const parsed = JSON.parse(raw.slice(raw.indexOf("{"), raw.lastIndexOf("}") + 1)) as { issues?: Partial<CheckIssue>[] };
    const ids = new Set([...d.requirements.map((o) => o.id), ...d.plans.flatMap((p) => p.features.map((f) => f.id))]);
    const extra = (parsed.issues ?? [])
      .filter((i): i is Partial<CheckIssue> & { kind: CheckKind; message: string } => CheckKinds.options.includes(i.kind as CheckKind) && typeof i.message === "string")
      .map((i, n) => ({
        id: `${r.id}-llm-${n + 1}`,
        kind: i.kind,
        severity: i.severity === "red" ? ("red" as const) : ("amber" as const),
        field: typeof i.field === "string" ? i.field : "description",
        message: i.message,
        relatedId: i.relatedId && ids.has(i.relatedId) ? i.relatedId : undefined,
        suggestion: typeof i.suggestion === "string" ? i.suggestion : undefined,
      }));
    return { ...base, engine: "rules+llm", issues: [...base.issues, ...extra] };
  } catch {
    return base;
  }
}

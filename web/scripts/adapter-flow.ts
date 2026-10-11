/**
 * End-to-end check of the adapter on real data: one case from open to exported,
 * through the UI's REST contract, every response validated against the contract.
 * It WRITES (stance, question, confirm, requirement, sign-off): run it against a
 * local database only.
 *
 *   backend:  uv run python -m app.dev_local        (local database, offline)
 *   web:      DATA_SOURCE=backend npm run dev
 *   run:      API_BASE=http://localhost:3000/api npm run adapter-flow
 */
import assert from "node:assert/strict";
import { endpoints, type EndpointName, type ResponseOf } from "../src/contracts";

const BASE = process.env.API_BASE ?? "http://localhost:3000/api";
const U = (n: number) => `00000000-0000-4000-8000-00000000000${n}`;
const LENA = U(1);
const KATRIN = U(8);
const ENGINEER = U(4);

let calls = 0;
async function call<N extends EndpointName>(user: string, name: N, opts: { params?: Record<string, string>; query?: Record<string, string>; body?: unknown } = {}, expect = 200): Promise<ResponseOf<N>> {
  const def = endpoints[name];
  const path = def.path.replace(/:([a-zA-Z]+)/g, (_, k: string) => encodeURIComponent(opts.params![k]));
  const qs = opts.query ? "?" + new URLSearchParams(opts.query).toString() : "";
  const res = await fetch(BASE + path + qs, { method: def.method, headers: { "content-type": "application/json", "x-user-id": user }, body: "body" in def ? JSON.stringify(opts.body ?? {}) : undefined });
  const json = await res.json().catch(() => null);
  calls++;
  assert.equal(res.status, expect, `${name} ${path}: ${res.status} ${JSON.stringify(json)?.slice(0, 300)}`);
  if (expect !== 200) return json as ResponseOf<N>;
  const parsed = def.response.safeParse(json);
  assert.ok(parsed.success, `${name} breaks the contract: ${JSON.stringify(parsed.error?.issues.slice(0, 3))}`);
  console.log(`ok ${String(calls).padStart(2)} ${name}`);
  return json as ResponseOf<N>;
}

async function main() {
  const me = await call(LENA, "me");
  assert.equal(me.user.name, "Lena Hofmann");
  const series = me.workspace.series;
  const cases = await call(LENA, "cases", { query: { series } });
  const open = cases.find((c) => c.state === "new" && c.requirementIds.length === 0 && c.figures.perMarket.some((m) => m.complaints > 3));
  assert.ok(open, "no open case with complaints on the board");
  const id = open.id;
  console.log(`   case ${id}: ${open.title}`);

  const bundle = await call(LENA, "caseBundle", { params: { id } });
  assert.ok(bundle.signals.length > 0 && bundle.signals.every((s) => s.caseId === id));
  const segments = await call(LENA, "caseSegments", { params: { id } });
  await call(LENA, "saveCaseStance", { params: { id }, body: { overrides: {}, overrideReasons: {}, evidenceTakes: [], inside: [{ id: "i1", kind: "engineering_on_it", text: "Engineering is already looking at it" }], ambition: "match" } });
  const q = await call(LENA, "createQuestion", { params: { id }, body: { addressee: "engineering", question: "Is a fix feasible for the next generation?", why: "Sets the release" } });
  const note = await call(LENA, "addMeetingNote", { params: { id }, body: { title: "Engineering round", date: new Date().toISOString(), attendees: ["Engineering"], text: "Engineering: a fix is feasible for the next generation. Cost is medium." } });
  console.log(`   meeting note ${note.note.id}: ${note.proposals.length} proposals`);
  await call(ENGINEER, "patchQuestion", { params: { id: q.id }, body: { answer: "Feasible for the next generation." } });
  await call(LENA, "addComment", { body: { objectType: "case", objectId: id, body: "@Katrin please have a look" } });

  await call(LENA, "transitionCase", { params: { id }, body: { action: "start", rationale: "Looking into it" } });
  const confirmed = await call(LENA, "transitionCase", { params: { id }, body: { action: "confirm", rationale: "Confirmed after the engineering round" } });
  assert.equal(confirmed.outcome, "confirmed");
  assert.equal(confirmed.requirementIds.length, 0, "confirm must not draft: the shape questions come first");

  const shape = await call(LENA, "shapeQuestions", { params: { id } });
  await call(LENA, "askShapeQuestions", { params: { id } });
  const created = await call(LENA, "generateRequirements", { params: { id }, body: { answers: shape.questions.map((s) => ({ key: s.key, value: s.default })) } });
  assert.equal(created.length, 1);
  const rid = created[0].id;
  assert.ok(created[0].provenance.length > 0 && created[0].metrics.length > 0 && created[0].ownerId === LENA);
  console.log(`   requirement ${rid}: ${created[0].title}`);

  const rb = await call(LENA, "requirementBundle", { params: { id: rid } });
  assert.ok(rb.check && rb.versions.length === 1 && rb.case.id === id);
  const edited = await call(LENA, "patchRequirement", { params: { id: rid }, body: { patch: { description: "Customers no longer report this problem. The outcome is measured in the first feedback wave after launch." }, rationale: "Sharper wording" } });
  assert.equal(edited.version, 2);
  const fb = await call(LENA, "addFeedback", { params: { id: rid }, body: { from: "engineering", text: "The target is too tight for the first year", response: "compromise", rationale: "Measure in the second wave" } });
  await call(LENA, "decideProposal", { params: { id: fb.proposal.id }, body: { decision: "accept", rationale: "Agreed with engineering" } });
  await call(LENA, "runRequirementCheck", { params: { id: rid } });
  const after = await call(LENA, "requirementBundle", { params: { id: rid } });
  assert.equal(after.requirement.version, 3);
  assert.equal(after.feedback[0].resultingVersion, 3);

  await call(LENA, "transitionRequirement", { params: { id: rid }, body: { action: "sign_off", rationale: "x" } }, 403);
  await call(LENA, "transitionRequirement", { params: { id: rid }, body: { action: "approve", rationale: "Ready for sign-off" } });
  const signed = await call(KATRIN, "transitionRequirement", { params: { id: rid }, body: { action: "sign_off", rationale: "Fits the brief" } });
  assert.equal(signed.status, "signed_off");
  const planned = await call(LENA, "transitionRequirement", { params: { id: rid }, body: { action: "plan", rationale: "Next generation" } });
  assert.ok(planned.plannedFeatureId);
  const plan = await call(LENA, "plan", { params: { series } });
  assert.ok(plan.features.some((f) => f.requirementIds.includes(rid)));
  const deck = await call(LENA, "requirementDeck", { params: { series } });
  assert.ok(deck.slides.some((s) => s.requirementId === rid));
  const exported = await call(LENA, "exportRequirements", { body: { series, format: "codebeamer_csv" } });
  assert.ok(exported.content.includes(rid));

  const chat = await call(LENA, "postMessage", { params: { type: "case", id }, body: { content: "What do customers complain about most?" } });
  assert.equal(chat.messages.length, 2);
  const seg = segments.find((s) => s.verbatims > 0);
  if (seg) await call(LENA, "postMessage", { params: { type: "case", id }, body: { content: `What do ${seg.label} say?`, segment: seg.key } });
  await call(LENA, "messages", { params: { type: "case", id } });
  await call(LENA, "previewScores", { body: { weights: { severity: 0.6 } } });
  const scores = await call(LENA, "scores", {});
  assert.ok(scores.requirements.some((r) => r.requirementId === rid), "the exported requirement is still ranked");
  const trail = await call(LENA, "audit", { query: { objectId: rid } });
  assert.ok(trail.some((e) => e.actorType === "human") && trail.some((e) => e.actorType === "ai"));
  const inbox = await call(KATRIN, "notifications");
  assert.ok(inbox.some((n) => n.kind === "sign_off_request") && inbox.some((n) => n.kind === "mention"));
  console.log(`\n${calls} calls, all responses match the contract. ${id} -> ${rid} (exported), audit trail has ${trail.length} events.`);
}

main().catch((e) => {
  console.error(e.message ?? e);
  process.exit(1);
});

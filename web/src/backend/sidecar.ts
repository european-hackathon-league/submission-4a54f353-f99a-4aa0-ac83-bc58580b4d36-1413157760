import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { AuditEvent, ObjectType } from "@/contracts";

/**
 * Sidecar store for what the backend does not hold yet (INTEGRATION-GUIDE.md 8.8):
 * workspace choice, case sub-states and outcomes, owners and watchers, the
 * requirement sign-off layer, questions, notes, referrals, comments, alerts, plan.
 * Keyed by backend ids, persisted as JSON so it survives restarts. Every write
 * records an audit event here (synthetic, merged into the audit responses).
 */
export interface SidecarData {
  version: 1;
  /** userId -> chosen config and weight profile */
  workspaces: Record<string, { configId?: string; profileId?: string }>;
  /** requirement id -> sign-off layer on top of backend `approved` */
  signOff: Record<string, { status: "signed_off" | "planned" | "exported"; by: string; at: string }>;
  /** requirement id -> reason of the last send-back */
  sentBack: Record<string, string>;
  audit: AuditEvent[];
  /** Free slots for the sidecar features, filled as they are wired. */
  [k: string]: unknown;
}

const FILE = process.env.SIDECAR_FILE ?? join(process.cwd(), ".data", "sidecar.json");
const g = globalThis as unknown as { __rsSidecar?: SidecarData };

function empty(): SidecarData {
  return { version: 1, workspaces: {}, signOff: {}, sentBack: {}, audit: [] };
}

export function sidecar(): SidecarData {
  if (!g.__rsSidecar) {
    g.__rsSidecar = existsSync(FILE) ? { ...empty(), ...(JSON.parse(readFileSync(FILE, "utf8")) as SidecarData) } : empty();
  }
  return g.__rsSidecar;
}

export function saveSidecar() {
  mkdirSync(dirname(FILE), { recursive: true });
  writeFileSync(FILE, JSON.stringify(sidecar(), null, 1));
}

export function resetSidecar() {
  g.__rsSidecar = empty();
  saveSidecar();
}

export function sidecarAudit(e: { actorId: string; action: string; objectType: ObjectType; objectId: string; before?: unknown; after?: unknown; rationale?: string }) {
  const s = sidecar();
  const event: AuditEvent = {
    seq: -1 - s.audit.length, // negative: never collides with backend sequence numbers
    at: new Date().toISOString(),
    actorType: e.actorId.startsWith("agent:") ? "ai" : "human",
    ...e,
  };
  s.audit.push(event);
  saveSidecar();
  return event;
}

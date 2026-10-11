import type { Series, User } from "@/contracts";
import { users as mockUsers } from "@/mock/authored/people";

/**
 * The UI keeps its six named personas; each acts in the backend as one app_user
 * (backend/app/demo.py, DEMO_USERS) on one config (vehicle x market).
 * Katrin (head) uses the all-lines PM: the backend lets only `pm:` actors approve.
 * Her head-only actions (sign-off, send back, resolve) live in the sidecar.
 */
export interface Persona {
  backendUserId: string;
  /** Default config name, `<vehicle> / <market>` as in GET /configs. */
  config: string;
  vehicle: Series;
}

const U = (n: number) => `00000000-0000-4000-8000-00000000000${n}`;

export const PERSONAS: Record<string, Persona> = {
  "u-lena": { backendUserId: U(1), config: "G60 / USA", vehicle: "G60" },
  "u-wei": { backendUserId: U(7), config: "G60 / China", vehicle: "G60" },
  "u-jonas": { backendUserId: U(6), config: "G60 / Europe", vehicle: "G60" },
  "u-katrin": { backendUserId: U(6), config: "G60 / USA", vehicle: "G60" },
  "u-sofia": { backendUserId: U(3), config: "F70 / Europe", vehicle: "F70" },
  "u-daniel": { backendUserId: U(2), config: "G70 / USA", vehicle: "G70" },
};

export const personaUsers = (): User[] => mockUsers.filter((u) => PERSONAS[u.id]);

export function persona(userId: string): Persona | undefined {
  return PERSONAS[userId];
}

/** Backend actor (`pm:<uuid>`) -> persona id for display; the first persona wins for shared users. */
export function personaIdForActor(actor: string | null | undefined): string {
  if (!actor) return "system";
  const [kind, id] = actor.split(":");
  if (kind === "pm" || kind === "engineer" || kind === "admin") {
    const hit = Object.entries(PERSONAS).find(([, p]) => p.backendUserId === id);
    return hit ? hit[0] : actor;
  }
  return actor.startsWith("agent:") ? actor : `agent:${actor}`;
}

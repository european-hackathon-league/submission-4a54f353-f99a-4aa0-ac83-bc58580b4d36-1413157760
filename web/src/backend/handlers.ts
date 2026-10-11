import type { AgentRun, Agent, Notification, QuarantineItem, Signal, TaxonomyResponse, WeightProfile, Workspace, User } from "@/contracts";
import type { Handlers } from "@/mock/handlers";
import { ApiError } from "@/mock/store";
import { backend, cached, invalidateCache, type QueryValue } from "./client";
import {
  agentFromBackend,
  marketFromBackend,
  marketToBackend,
  powertrainFromBackend,
  sourceTypeFromBackend,
  trustFromBackend,
  weightsFromBackend,
  weightsToBackend,
} from "./map";
import { persona, personaUsers, type Persona } from "./personas";
import { resetSidecar, saveSidecar, sidecar, sidecarAudit } from "./sidecar";

/**
 * Backend-backed handlers (DATA_SOURCE=backend). Same signatures as src/mock/handlers.ts;
 * route.ts falls back to the mock for every endpoint not implemented here.
 * Endpoint map: INTEGRATION-GUIDE.md section 8 and the wiring plan.
 */

// ---------- backend row shapes (backend/API.md, /docs) ----------
interface BConfig {
  id: string;
  name: string;
  vehicle: string;
  market: string;
  profiles: { id: string; name: string; is_default: boolean }[];
}
interface BProfile {
  id: string;
  config_id: string;
  name: string;
  case_weights: Record<string, number>;
  brand_multipliers: Record<string, number>;
  cost_multipliers: Record<string, number>;
  is_default: boolean;
}
interface BSignal {
  id: string;
  record_id: string;
  case_id: string | null;
  kind: Signal["kind"];
  severity: Signal["severity"] | null;
  summary: string;
  quote: string;
  quote_en: string | null;
  market: string;
  vehicle: string | null;
  vehicle_role: Signal["vehicleRole"] | null;
  evidence_role: string | null;
  powertrain: string | null;
  reach: number | null;
  reach_unit: string | null;
  aspect_group: Signal["aspectGroup"];
  car_area: string[] | null;
  published_at: string | null;
  topic_id: string | null;
  topic: string | null;
  source_id: string | null;
  content_type: string | null;
  url: string | null;
  record_title: string | null;
}

// ---------- context ----------
function who(user: User): Persona & { userId: string } {
  const p = persona(user.id);
  if (!p) throw new ApiError(401, `No backend user for persona ${user.id}`);
  return { ...p, userId: user.id };
}

const get = <T>(user: User, path: string, query?: Record<string, QueryValue>) => backend<T>(who(user).backendUserId, "GET", path, { query });

async function write<T>(user: User, method: "POST" | "PUT" | "PATCH", path: string, body?: unknown) {
  const out = await backend<T>(who(user).backendUserId, method, path, { body });
  invalidateCache();
  return out;
}

const configs = (user: User) => cached("configs", 60_000, () => get<BConfig[]>(user, "/configs"));

/** The persona's active config and weight profile (sidecar choice, else the persona default). */
export async function activeConfig(user: User): Promise<{ config: BConfig; profileId: string }> {
  const p = who(user);
  const all = await configs(user);
  const choice = sidecar().workspaces[user.id] ?? {};
  const config = all.find((c) => c.id === choice.configId) ?? all.find((c) => c.name === p.config);
  if (!config) throw new ApiError(404, `Config ${p.config} not found in the backend`);
  const profile = config.profiles.find((x) => x.id === choice.profileId) ?? config.profiles.find((x) => x.is_default) ?? config.profiles[0];
  if (!profile) throw new ApiError(404, `Config ${config.name} has no weight profile`);
  return { config, profileId: profile.id };
}

function profileFromBackend(p: BProfile): WeightProfile {
  return {
    id: p.id,
    name: p.name,
    description: `Weight profile of config ${p.config_id}`,
    ownerId: null,
    isDefault: p.is_default,
    weights: weightsFromBackend(p.case_weights),
    costMultiplier: { S: p.cost_multipliers.S ?? 1, M: p.cost_multipliers.M ?? 0.85, L: p.cost_multipliers.L ?? 0.7 },
  };
}

async function profilesOf(user: User, configId: string): Promise<BProfile[]> {
  const cfg = await cached(`config:${configId}`, 30_000, () => get<{ profiles: BProfile[] }>(user, `/configs/${configId}`));
  return cfg.profiles;
}

function workspaceOf(user: User, config: BConfig, profileId: string): Workspace {
  const market = marketFromBackend(config.market);
  return {
    id: `ws-${user.id}`,
    userId: user.id,
    series: config.vehicle as Workspace["series"],
    region: market === "CN" ? "CN" : "RoW",
    projectType: "new_gen", // rejected option in CLAUDE.md; hidden on real data
    weightProfileId: profileId,
    sourceFilter: { excludeSocial: false, ownersOnly: false, minTrust: "E" },
    watchlist: { competitors: [], topics: [], domains: [], languages: [] },
  };
}

// ---------- mapping ----------
function agentOfRun(name: string): Agent {
  if (name.includes("competitor")) return "competitor";
  if (name.includes("trend")) return "trends";
  if (name.includes("innovation")) return "innovation_scout";
  if (name.startsWith("seed") || name.includes("bmw")) return "bmw_data";
  return "customer_public";
}

export function signalFromBackend(s: BSignal, series: Signal["series"]): Signal {
  return {
    id: s.id,
    kind: s.kind,
    severity: s.severity ?? "none",
    agent: agentFromBackend(s.evidence_role, s.source_id, s.kind),
    sourceType: sourceTypeFromBackend(s.source_id, s.content_type),
    sourceName: s.record_title ?? s.source_id ?? "source",
    trust: trustFromBackend(s.source_id, s.content_type),
    url: s.url ?? undefined,
    recordRef: s.record_id,
    publishedAt: s.published_at ?? "",
    fetchedAt: s.published_at ?? "",
    series: (s.vehicle as Signal["series"]) ?? series,
    vehicleRole: s.vehicle_role ?? "unknown",
    powertrain: powertrainFromBackend(s.powertrain),
    market: marketFromBackend(s.market),
    marketBasis: s.market && s.market !== "unknown" ? "stated" : "unknown",
    carArea: (s.car_area?.[0] ?? "quality_materials") as Signal["carArea"],
    topicId: s.topic_id,
    aspectGroup: s.aspect_group,
    featureHint: s.topic ?? "",
    summary: s.summary,
    quote: s.quote,
    quoteEn: s.quote_en ?? undefined,
    language: s.quote_en ? "other" : "en",
    audience: s.evidence_role === "bmw_customer" ? "owner" : s.content_type === "expert" ? "reviewer" : "public",
    reach: s.reach ?? undefined,
    reachUnit: s.reach_unit === "views" || s.reach_unit === "upvotes" || s.reach_unit === "likes" ? s.reach_unit : undefined,
    caseId: s.case_id,
    status: s.case_id ? "clustered" : "unclustered",
    synthetic: false,
  };
}

const NOTIFICATION_KIND: Record<string, Notification["kind"]> = {
  case_opened: "new_signals",
  new_evidence: "new_signals",
  market_conflict: "alert",
  run_failed: "alert",
  candidate_feature: "alert",
  new_competitor: "alert",
  requirement_generated: "new_signals",
};

// ---------- handlers ----------
export const backendHandlers: Partial<Handlers> = {
  // Identity
  me: async ({ user }) => {
    const { config, profileId } = await activeConfig(user);
    const profiles = await profilesOf(user, config.id);
    const profile = profiles.find((p) => p.id === profileId) ?? profiles[0];
    return { user, workspace: workspaceOf(user, config, profileId), weightProfile: profileFromBackend(profile) };
  },
  users: () => personaUsers(),
  updateWorkspace: async ({ user, body }) => {
    const { config, profileId } = await activeConfig(user);
    const s = sidecar();
    const before = { ...s.workspaces[user.id] };
    let next = { configId: config.id, profileId };
    if (body.series && body.series !== config.vehicle) {
      const market = config.market;
      const all = await configs(user);
      const other = all.find((c) => c.vehicle === body.series && c.market === market) ?? all.find((c) => c.vehicle === body.series);
      if (!other) throw new ApiError(404, `No config for ${body.series}`);
      next = { configId: other.id, profileId: (other.profiles.find((p) => p.is_default) ?? other.profiles[0]).id };
    }
    if (body.weightProfileId) next.profileId = body.weightProfileId;
    s.workspaces[user.id] = next;
    saveSidecar();
    sidecarAudit({ actorId: user.id, action: "workspace.updated", objectType: "workspace", objectId: `ws-${user.id}`, before, after: next });
    const fresh = await activeConfig(user);
    return workspaceOf(user, fresh.config, fresh.profileId);
  },

  // Signals and agents
  signals: async ({ user, query }) => {
    const { config } = await activeConfig(user);
    const series = (query.series ?? config.vehicle) as Signal["series"];
    const res = await get<{ total: number; items: BSignal[] }>(user, "/signals", {
      vehicle: query.caseId ? undefined : series,
      case_id: query.caseId,
      market: query.market ? marketToBackend(query.market) : undefined,
      kind: query.kind,
      limit: Math.min(query.limit ?? 500, 500),
    });
    let items = res.items.map((s) => signalFromBackend(s, series));
    if (query.agent) items = items.filter((s) => s.agent === query.agent);
    if (query.status) items = items.filter((s) => s.status === query.status);
    if (query.q) {
      const q = query.q.toLowerCase();
      items = items.filter((s) => s.summary.toLowerCase().includes(q) || s.quote.toLowerCase().includes(q));
    }
    return items;
  },
  clusters: () => [],
  agentRuns: async ({ user }) => {
    const { config } = await activeConfig(user);
    const runs = await get<
      { id: string; agent: string; mode: string; status: string; started_at: string; ended_at: string | null; signals_valid: number | null; signals_quarantined: number | null; llm_cost_usd: number | null; error: string | null }[]
    >(user, "/runs", { limit: 50 });
    return runs.map(
      (r): AgentRun => ({
        id: r.id,
        agent: agentOfRun(r.agent),
        series: config.vehicle as AgentRun["series"],
        startedAt: r.started_at,
        endedAt: r.ended_at ?? r.started_at,
        status: r.status === "failed" ? "failed" : r.status === "ok" || r.status === "done" || r.status === "success" ? "ok" : "partial",
        valid: r.signals_valid ?? 0,
        rejected: r.signals_quarantined ?? 0,
        newSignals: r.signals_valid ?? 0,
        llmCostUsd: Number(r.llm_cost_usd ?? 0),
        note: r.error ?? `${r.agent} (${r.mode})`,
      }),
    );
  },
  refresh: () => {
    throw new ApiError(501, "Fetch runs are started by the daily worker on the server, not from the UI. See the runs list for the latest results.");
  },
  quarantine: async ({ user }) => {
    const res = await get<{ items: { id: string; record_id: string; reject_reason: string; kind: string; summary: string | null; quote: string | null; agent: string | null; created_at: string }[] }>(
      user,
      "/quarantine",
      { limit: 200 },
    );
    return res.items.map(
      (q): QuarantineItem => ({
        id: q.id,
        agent: agentOfRun(q.agent ?? ""),
        sourceName: q.record_id,
        snippet: q.quote ?? q.summary ?? "",
        reason: q.reject_reason,
        at: q.created_at,
      }),
    );
  },
  taxonomy: async ({ user }) => {
    const t = await cached("taxonomy", 600_000, () =>
      get<{ car_areas: { code: string; name: string; description: string }[]; aspect_groups: { code: string; description: string }[]; topics: { topic_id: string; name: string; car_area: string; hint: string | null; taxonomy_version: string }[] }>(
        user,
        "/taxonomy",
      ),
    );
    const out: TaxonomyResponse = {
      version: t.topics[0]?.taxonomy_version ?? "backend",
      carAreas: t.car_areas.map((a) => ({ id: a.code as TaxonomyResponse["carAreas"][number]["id"], label: a.name, description: a.description })),
      topics: t.topics.map((x) => ({ id: x.topic_id, label: x.name, shortLabel: x.name.split(",")[0].trim(), carArea: x.car_area as TaxonomyResponse["carAreas"][number]["id"], hint: x.hint })),
      aspectGroups: t.aspect_groups.map((a) => ({ id: a.code as TaxonomyResponse["aspectGroups"][number]["id"], description: a.description })),
    };
    return out;
  },

  // Scoring profiles
  weightProfiles: async ({ user }) => {
    const { config } = await activeConfig(user);
    return (await profilesOf(user, config.id)).map(profileFromBackend);
  },
  saveWeightProfile: async ({ user, params, body }) => {
    const { config } = await activeConfig(user);
    const current = (await profilesOf(user, config.id)).find((p) => p.id === params.id);
    if (!current) throw new ApiError(404, "Weight profile not found in your config");
    const p = await write<BProfile>(user, "PATCH", `/profiles/${params.id}`, {
      name: body.name,
      case_weights: body.weights ? weightsToBackend(body.weights, current.case_weights) : undefined,
      cost_multipliers: body.costMultiplier,
      is_default: body.isDefault,
      rationale: "Saved in the weights panel",
    });
    return profileFromBackend(p);
  },
  createWeightProfile: async ({ user, body }) => {
    const { config, profileId } = await activeConfig(user);
    const base = (await profilesOf(user, config.id)).find((p) => p.id === profileId);
    const p = await write<BProfile>(user, "POST", `/configs/${config.id}/profiles`, {
      name: body.name,
      case_weights: weightsToBackend(body.weights, base?.case_weights ?? {}),
      cost_multipliers: body.costMultiplier,
      rationale: body.description || "Created in the weights panel",
    });
    return profileFromBackend(p);
  },

  // Inbox
  notifications: async ({ user }) => {
    const rows = await get<{ id: string; type: string; object_type: string; object_id: string; message: string; created_at: string; read: boolean }[]>(user, "/notifications", { limit: 100 });
    return rows.map(
      (n): Notification => ({
        id: n.id,
        userId: user.id,
        kind: NOTIFICATION_KIND[n.type] ?? "alert",
        title: n.type.replaceAll("_", " "),
        body: n.message,
        link: n.object_type === "case" ? `/cases/${n.object_id}` : n.object_type === "requirement" ? `/requirements/${n.object_id}` : "/radar",
        read: n.read,
        createdAt: n.created_at,
      }),
    );
  },
  readNotifications: async ({ user, body }) => {
    const ids = body.ids ?? (await get<{ id: string }[]>(user, "/notifications", { unread: true, limit: 500 })).map((n) => n.id);
    for (const id of ids) await write(user, "POST", `/notifications/${id}/read`);
    return { ok: true as const };
  },

  // Oversight: reset only clears the sidecar, never the backend
  reset: () => {
    resetSidecar();
    invalidateCache();
    return { ok: true as const };
  },
};

import { ApiError } from "@/mock/store";

/**
 * Server-side client for the FastAPI backend (backend/API.md). Only the adapter
 * handlers in src/backend use it; the browser keeps talking to /api.
 * Env: BACKEND_URL (default http://localhost:8000). Auth: X-User-Id (backend AUTH_MODE=dev).
 */
const BASE = process.env.BACKEND_URL ?? "http://localhost:8000";

export type QueryValue = string | number | boolean | undefined | null | (string | number)[];

export function qs(query: Record<string, QueryValue> = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    // FastAPI list parameters repeat the key: state=open&state=confirmed
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.append(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** FastAPI errors are {detail: string} or, for validation, {detail: [{loc, msg}]}. */
function detailMessage(json: unknown, status: number): string {
  const d = (json as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg}`).join("; ");
  return `Backend request failed (${status})`;
}

export async function backend<T>(
  backendUserId: string,
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  opts: { query?: Record<string, QueryValue>; body?: unknown } = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path + qs(opts.query), {
      method,
      headers: { "content-type": "application/json", "x-user-id": backendUserId },
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(502, `Backend not reachable at ${BASE}`);
  }
  const text = await res.text();
  const json = text ? (JSON.parse(text) as unknown) : null;
  if (!res.ok) throw new ApiError(res.status, detailMessage(json, res.status));
  return json as T;
}

/** Short-lived memo for reference reads (configs, taxonomy, boards) to keep N+1 calls cheap. */
const memo = new Map<string, { at: number; value: Promise<unknown> }>();
export function cached<T>(key: string, ttlMs: number, load: () => Promise<T>): Promise<T> {
  const hit = memo.get(key);
  if (hit && Date.now() - hit.at < ttlMs) return hit.value as Promise<T>;
  const value = load().catch((e) => {
    memo.delete(key);
    throw e;
  });
  memo.set(key, { at: Date.now(), value });
  return value;
}

/** Drop memoized reads after a write (all of them: simple and consistent, like the UI's invalidation). */
export function invalidateCache() {
  memo.clear();
}

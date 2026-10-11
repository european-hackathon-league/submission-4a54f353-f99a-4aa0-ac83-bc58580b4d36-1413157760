import type { NextRequest } from "next/server";
import { ZodError } from "zod";
import { endpoints, type EndpointName } from "@/contracts";
import { handlers } from "@/mock/handlers";
import { ApiError, db } from "@/mock/store";
import { backendHandlers, backendUsers } from "@/backend/handlers";

/**
 * One catch-all route that serves the REST contract in src/contracts/api.ts.
 * Default: the mock on the in-memory store (synthetic demo data). With
 * DATA_SOURCE=backend every endpoint is served by the adapter in src/backend from
 * the FastAPI backend (BACKEND_URL); the two data sources are never mixed.
 */
const USE_BACKEND = process.env.DATA_SOURCE === "backend";
/** Who a request without a user is (the login page lists the users before one is picked). */
const DEFAULT_BACKEND_USER = process.env.BACKEND_DEFAULT_USER ?? "00000000-0000-4000-8000-000000000001";
const isUuid = (v: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);

const routes = (Object.keys(endpoints) as EndpointName[]).map((name) => {
  const def = endpoints[name];
  const keys: string[] = [];
  const pattern = new RegExp(
    "^" +
      def.path.replace(/:[a-zA-Z]+/g, (m) => {
        keys.push(m.slice(1));
        return "([^/]+)";
      }) +
      "$",
  );
  return { name, def, keys, pattern };
});

function match(method: string, path: string) {
  for (const r of routes) {
    if (r.def.method !== method) continue;
    const m = path.match(r.pattern);
    if (m) return { ...r, params: Object.fromEntries(r.keys.map((k, i) => [k, decodeURIComponent(m[i + 1])])) };
  }
  return null;
}

async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const route = match(req.method, "/" + path.join("/"));
  if (!route) return Response.json({ error: `No endpoint ${req.method} /${path.join("/")}` }, { status: 404 });

  try {
    let userId = req.headers.get("x-user-id") ?? req.cookies.get("rs_user")?.value ?? "u-lena";
    // A cookie left over from the mock (u-lena) is not a backend user: fall back to the default one.
    if (USE_BACKEND && !isUuid(userId)) userId = DEFAULT_BACKEND_USER;
    const user = (USE_BACKEND ? await backendUsers(userId) : db().users).find((u) => u.id === userId);
    if (!user) throw new ApiError(401, `Unknown user ${userId}`);

    const def = route.def as { query?: { parse(v: unknown): unknown }; body?: { parse(v: unknown): unknown }; response: { safeParse(v: unknown): { success: boolean; error?: ZodError } } };
    const query = def.query ? def.query.parse(Object.fromEntries(req.nextUrl.searchParams)) : {};
    let body: unknown = undefined;
    if (def.body) {
      const text = await req.text();
      body = def.body.parse(text ? JSON.parse(text) : {});
    }

    const handler = (USE_BACKEND ? backendHandlers[route.name] : handlers[route.name]) as (c: unknown) => unknown;
    const result = await handler({ params: route.params, query, body, user });

    if (process.env.NODE_ENV !== "production") {
      const check = def.response.safeParse(result);
      if (!check.success) console.error(`[${USE_BACKEND ? "backend" : "mock"}] ${route.name} response breaks the contract:`, check.error?.issues.slice(0, 5));
    }
    return Response.json(result);
  } catch (error) {
    if (error instanceof ZodError) return Response.json({ error: "Invalid request", issues: error.issues }, { status: 400 });
    if (error instanceof ApiError) return Response.json({ error: error.message }, { status: error.status });
    console.error("[mock] unhandled", error);
    return Response.json({ error: error instanceof Error ? error.message : "Server error" }, { status: 500 });
  }
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };

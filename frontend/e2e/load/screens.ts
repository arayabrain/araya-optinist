import { ClientRequest } from "http"

import { RecordRef } from "./config"
import {
  errorName,
  header,
  HttpResult,
  jsonBody,
  send,
  userAgent,
} from "./http"
import { RequestRecord, Route, round3, VuRecord } from "./summary"

// One virtual user (VU) opens one screen: that screen's requests in the
// browser's order, sequential where the page waits and all at once where the
// browser sends them together. Free-route requests carry the step's AWSALB
// pin, so every VU reaches the one free instance under test.

// What the ALB sends to the public service whatever the headers say; the rest
// of a signed-in user's requests carry a Bearer token and go to the free tier
export const PUBLIC_PREFIXES = [
  "/auth/",
  "/users/me",
  "/static/",
  "/health",
  "/log-report/",
]

export function routeOf(path: string): Route {
  return PUBLIC_PREFIXES.some((p) => path.startsWith(p)) ? "public" : "free"
}

// A stable label: the endpoint and the output file's name. A numeric tail is
// a database id, so the label keeps the endpoint alone.
export function itemName(path: string): string {
  const parts = path.split("?")[0].split("/")
  const endpoint = parts.length > 3 ? parts[3] : parts.join("/")
  const last = parts[parts.length - 1]
  return /^\d+$/.test(last) ? endpoint : `${endpoint}:${last}`
}

export type Session = {
  accessToken: string
  refreshToken: string
  exToken: string | null
}

const ACCEPT_JSON = { Accept: "application/json" }

export function authHeaders(accessToken: string, exToken: string | null) {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
    ...ACCEPT_JSON,
  }
  if (exToken) headers.ExToken = exToken
  return headers
}

// ---------------------------------------------------------------------------
// The step a VU runs in
// ---------------------------------------------------------------------------

export type StepContext = {
  base: URL
  pin: string
  t0: number
  deadlineMs: number
  requests: RequestRecord[]
  vuRecords: VuRecord[]
  abortReason: string | null
  // Past the step's deadline
  cancelled: boolean
  // true once the users are released together; false if the step stopped
  // before its release
  released: Promise<boolean>
}

export class VirtualUser {
  readonly agent = userAgent()
  readonly inflight = new Set<ClientRequest>()
  stopped = false

  constructor(readonly index: number) {}

  // Ends the user's open requests, as a closing browser tab would
  stop(): void {
    this.stopped = true
    for (const req of this.inflight) req.destroy(new Error("stopped"))
    this.agent.destroy()
  }
}

class Abort extends Error {}

function seconds(ctx: StepContext, at: number): number {
  return round3((at - ctx.t0) / 1000)
}

function elapsed(since: number): number {
  return round3((performance.now() - since) / 1000)
}

type CallOptions = {
  headers?: Record<string, string>
  json?: unknown
  ttfb?: boolean
}

// One request, recorded whatever happens to it: every failure is a data point
async function call(
  ctx: StepContext,
  vu: VirtualUser,
  name: string,
  method: string,
  path: string,
  route: Route,
  opts: CallOptions = {},
): Promise<HttpResult | null> {
  if (ctx.abortReason || ctx.cancelled) throw new Abort()
  const headers = { ...opts.headers }
  if (route === "free") headers.Cookie = `AWSALB=${ctx.pin}`
  const t = performance.now()
  const rec: RequestRecord = {
    vu: vu.index,
    name,
    route,
    start: seconds(ctx, t),
    status: null,
    dur: 0,
  }
  let res: HttpResult | null = null
  try {
    res = await send(vu.agent, ctx.base, method, path, {
      headers,
      json: opts.json,
      ttfb: opts.ttfb,
      timeoutMs: ctx.deadlineMs,
      inflight: vu.inflight,
    })
    rec.status = res.status
    rec.dur = elapsed(t)
    rec.bytes = res.bytes
    rec.served_by = header(res, "x-served-by-instance")
  } catch (e) {
    rec.dur = elapsed(t)
    rec.error = !vu.stopped
      ? errorName(e)
      : ctx.cancelled
        ? "deadline"
        : "stopped"
  }
  ctx.requests.push(rec)
  if (ctx.abortReason || ctx.cancelled) throw new Abort()
  return res
}

async function runVu(
  ctx: StepContext,
  vu: VirtualUser,
  body: (v: VuRecord, start: number) => Promise<void>,
  account?: number,
): Promise<void> {
  if (!(await ctx.released)) {
    vu.stop()
    return
  }
  const start = performance.now()
  const v: VuRecord = { vu: vu.index }
  if (account !== undefined) v.account = account
  try {
    await body(v, start)
  } catch (e) {
    v.aborted = true
    if (!(e instanceof Abort)) v.error = String(e)
  } finally {
    vu.stop()
  }
  ctx.vuRecords.push(v)
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

// The top page in a new tab with a session. It shows after the first
// /users/me; the storage refresh, the second /users/me and the limit warning
// go out together after it.
export function dashboardUser(
  ctx: StepContext,
  vu: VirtualUser,
  session: Session,
  account: number,
): Promise<void> {
  return runVu(
    ctx,
    vu,
    async (v, start) => {
      const r = await call(
        ctx,
        vu,
        "auth_refresh",
        "POST",
        "/auth/refresh",
        "public",
        {
          json: { refresh_token: session.refreshToken },
        },
      )
      let token = session.accessToken
      if (r?.status === 200) {
        token = jsonBody<{ access_token: string }>(r).access_token
      }
      const h = authHeaders(token, session.exToken)
      await call(ctx, vu, "is_standalone", "GET", "/is_standalone", "public", {
        headers: ACCEPT_JSON,
      })
      await call(ctx, vu, "users_me", "GET", "/users/me", "public", {
        headers: h,
      })
      v.shown = elapsed(start)
      await Promise.all([
        call(
          ctx,
          vu,
          "refresh_storage",
          "POST",
          "/workspaces/refresh-storage",
          "free",
          {
            headers: h,
          },
        ),
        call(ctx, vu, "users_me_2", "GET", "/users/me", "public", {
          headers: h,
        }),
        call(
          ctx,
          vu,
          "limit_warning",
          "GET",
          "/storage-limit-alerts/limit-warning",
          "free",
          { headers: h },
        ),
      ])
      v.done = elapsed(start)
    },
    account,
  )
}

// The top page's two /users/me alone, tokens refreshed before the release: a
// burst on the signed-in user lookup without the token refresh in front of it
export function usersMeUser(
  ctx: StepContext,
  vu: VirtualUser,
  headers: Record<string, string>,
): Promise<void> {
  return runVu(ctx, vu, async (v, start) => {
    await call(ctx, vu, "users_me", "GET", "/users/me", "public", { headers })
    await call(ctx, vu, "users_me_2", "GET", "/users/me", "public", { headers })
    v.done = elapsed(start)
  })
}

export type DialogSpec = { reproduce: string; items: string[] }

// The owner opens the record's output dialog: reproduce, then every item the
// browser fetched for it, at once
export function dialogUser(
  ctx: StepContext,
  vu: VirtualUser,
  headers: Record<string, string>,
  spec: DialogSpec,
): Promise<void> {
  return runVu(ctx, vu, async (v, start) => {
    await call(
      ctx,
      vu,
      "reproduce",
      "GET",
      spec.reproduce,
      routeOf(spec.reproduce),
      {
        headers,
      },
    )
    await Promise.all(
      spec.items.map((p) =>
        call(ctx, vu, itemName(p), "GET", p, routeOf(p), {
          ttfb: true,
          headers,
        }),
      ),
    )
    v.done = elapsed(start)
  })
}

export type HeaderedPath = { path: string; headers: Record<string, string> }
export type PublicSpec = {
  list: string
  thumbnails: HeaderedPath[]
  reproduce: string
  items: HeaderedPath[]
}

// An anonymous visitor: /public with its thumbnails, then the record's output
// dialog. Every request goes to the public service.
export function publicUser(
  ctx: StepContext,
  vu: VirtualUser,
  spec: PublicSpec,
): Promise<void> {
  return runVu(ctx, vu, async (v, start) => {
    await call(
      ctx,
      vu,
      "auth_refresh_anon",
      "POST",
      "/auth/refresh",
      "public",
      { json: {} },
    )
    await call(ctx, vu, "is_standalone", "GET", "/is_standalone", "public", {
      headers: ACCEPT_JSON,
    })
    await call(ctx, vu, "public_list", "GET", spec.list, "public", {
      headers: ACCEPT_JSON,
    })
    await Promise.all(
      spec.thumbnails.map((t) =>
        call(ctx, vu, itemName(t.path), "GET", t.path, "public", {
          ttfb: true,
          headers: t.headers,
        }),
      ),
    )
    v.shown = elapsed(start)
    const dialogStart = performance.now()
    await call(ctx, vu, "public_reproduce", "GET", spec.reproduce, "public", {
      headers: ACCEPT_JSON,
    })
    await Promise.all(
      spec.items.map((t) =>
        call(ctx, vu, itemName(t.path), "GET", t.path, "public", {
          ttfb: true,
          headers: t.headers,
        }),
      ),
    )
    v.dialog = elapsed(dialogStart)
    v.done = elapsed(start)
  })
}

// ---------------------------------------------------------------------------
// Request sets from the browser capture. The frontend changes, so the replay
// is rebuilt from a fresh capture on every run, and a capture whose shape the
// replay does not know fails the run instead of being replayed wrongly.
// ---------------------------------------------------------------------------

export type CaptureRow = {
  phase: string
  method: string
  path: string
  type: string
  status?: number | null
  request_headers?: Record<string, string>
  failed?: string
}

export class CaptureChanged extends Error {}

function apiRows(rows: CaptureRow[], phase?: string): CaptureRow[] {
  return rows.filter(
    (r) =>
      (phase === undefined || r.phase === phase) &&
      (r.type === "xhr" || r.type === "fetch") &&
      r.path.startsWith("/") &&
      !r.path.startsWith("/log-report/"),
  )
}

const DASHBOARD_REQUESTS: Record<string, number> = {
  "POST /auth/refresh": 1,
  "GET /is_standalone": 1,
  "GET /users/me": 2,
  "POST /workspaces/refresh-storage": 1,
  "GET /storage-limit-alerts/limit-warning": 1,
}

function tally(keys: string[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const k of keys) out[k] = (out[k] ?? 0) + 1
  return out
}

function describeTally(t: Record<string, number>): string {
  return Object.entries(t)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, n]) => (n > 1 ? `${k} x${n}` : k))
    .join(", ")
}

// The top page's order is fixed in dashboardUser; the capture only has to
// show the browser still sends exactly that set
export function checkDashboardCapture(rows: CaptureRow[]): void {
  const sent = tally(
    apiRows(rows, "dashboard_new_tab").map(
      (r) => `${r.method} ${r.path.split("?")[0]}`,
    ),
  )
  if (describeTally(sent) !== describeTally(DASHBOARD_REQUESTS)) {
    throw new CaptureChanged(
      `the top page now sends ${describeTally(sent)}; ` +
        `the replay sends ${describeTally(DASHBOARD_REQUESTS)}`,
    )
  }
}

export function dialogSpec(rows: CaptureRow[], record: RecordRef): DialogSpec {
  const dialog = apiRows(rows, "dialog")
  const reproduce = dialog.filter((r) =>
    r.path.includes("/workflow/reproduce/"),
  )
  const items = dialog.filter((r) => !r.path.includes("/workflow/reproduce/"))
  const own = `/${record.workspaceId}/${record.uid}`
  if (
    reproduce.length !== 1 ||
    !reproduce[0].path.endsWith(own) ||
    !items.length
  ) {
    throw new CaptureChanged(
      `the output dialog sent ${reproduce.length} reproduce request(s) and ` +
        `${items.length} item(s); the replay needs one reproduce of the record ` +
        "and at least one item",
    )
  }
  return { reproduce: reproduce[0].path, items: items.map((r) => r.path) }
}

function publicHeaders(r: CaptureRow): Record<string, string> {
  const headers: Record<string, string> = { ...ACCEPT_JSON }
  // The ALB sends the item routes to the public service on this header
  if (r.request_headers?.dataview_public_request) {
    headers.DATAVIEW_PUBLIC_REQUEST = "true"
  }
  return headers
}

// The recorded visit is phases public_list and dialog; the warm-up visit and
// the uid filter's own requests stay out
export function publicSpec(rows: CaptureRow[], record: RecordRef): PublicSpec {
  const answered = apiRows(rows).filter(
    (r) => r.status && (r.phase === "public_list" || r.phase === "dialog"),
  )
  const lists = answered.filter(
    (r) =>
      r.phase === "public_list" && r.path.startsWith("/api/public/dataview?"),
  )
  const seen = new Set<string>()
  const thumbnails: HeaderedPath[] = []
  for (const r of answered) {
    if (
      r.path.includes("/api/visualizations/thumbnail/") &&
      !seen.has(r.path)
    ) {
      seen.add(r.path)
      thumbnails.push({ path: r.path, headers: publicHeaders(r) })
    }
  }
  const own = `${record.workspaceId}/${record.uid}`
  const reproduce = answered.filter((r) =>
    r.path.startsWith(`/api/public/dataview/workflow/reproduce/${own}`),
  )
  const items = answered
    .filter(
      (r) =>
        r.phase === "dialog" &&
        r.path.startsWith("/api/visualizations/") &&
        !r.path.includes("/thumbnail/") &&
        r.path.includes(`/${own}/`),
    )
    .map((r) => ({ path: r.path, headers: publicHeaders(r) }))
  if (
    lists.length !== 1 ||
    reproduce.length !== 1 ||
    !thumbnails.length ||
    !items.length
  ) {
    throw new CaptureChanged(
      `the public page sent ${lists.length} list, ${reproduce.length} reproduce, ` +
        `${thumbnails.length} thumbnail and ${items.length} item request(s); ` +
        "the replay needs one list, one reproduce, and some of each other",
    )
  }
  return {
    list: lists[0].path,
    thumbnails,
    reproduce: reproduce[0].path,
    items,
  }
}

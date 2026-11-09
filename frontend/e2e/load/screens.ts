import { ClientRequest } from "http"

import { authHeaders as bearerHeaders } from "../helpers"

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

// A node id is the algorithm's name and a random 10-character tail
const NODE_ID_TAIL = /_[a-z0-9]{10}$/

// A stable label that matches across records: the endpoint, then everything
// after the record (node, subfolders, file) with every node's random tail
// dropped, then the query without its ids, so the same file name from two
// nodes or two folders stays two items. A numeric tail is a database id, so
// the label keeps the endpoint alone.
// Paths: /api/visualizations/<endpoint>/<ws>/<uid>[/<node>/...]/<file>[?query]
export function itemName(path: string): string {
  const [route, query = ""] = path.split("?")
  const parts = route.split("/")
  const endpoint = parts.length > 3 ? parts[3] : parts.join("/")
  const last = parts[parts.length - 1]
  if (/^\d+$/.test(last)) return endpoint
  const rest = parts.slice(6).map((s) => s.replace(NODE_ID_TAIL, ""))
  const params = [...new URLSearchParams(query).entries()]
    .filter(([key]) => key !== "id" && !key.endsWith("_id"))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${value}`)
  return (
    `${endpoint}:${rest.length ? rest.join("/") : last}` +
    (params.length ? `?${params.join("&")}` : "")
  )
}

export type Session = {
  accessToken: string
  refreshToken: string
  exToken: string | null
}

const ACCEPT_JSON = { Accept: "application/json" }

// helpers.ts's Bearer and ExToken, asking for JSON as the frontend does
export function authHeaders(accessToken: string, exToken: string | null) {
  return { ...bearerHeaders(accessToken, exToken), ...ACCEPT_JSON }
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

// Since a moment, to the last headers among the answers; to now when none
// answered
function lastHeaders(results: (HttpResult | null)[], since: number): number {
  const at = results
    .filter((r): r is HttpResult => r !== null)
    .map((r) => r.headersAt)
  return at.length ? round3((Math.max(...at) - since) / 1000) : elapsed(since)
}

type CallOptions = {
  headers?: Record<string, string>
  json?: unknown
  // An item or a thumbnail: timed to the headers (the server's time) and to
  // the last byte, its body read through and dropped
  item?: boolean
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
      discardBody: opts.item,
      timeoutMs: ctx.deadlineMs,
      inflight: vu.inflight,
    })
    rec.status = res.status
    if (opts.item) {
      rec.dur = round3((res.headersAt - t) / 1000)
      rec.full = round3((res.endAt - t) / 1000)
    } else {
      rec.dur = round3((res.endAt - t) / 1000)
    }
    rec.bytes = res.bytes
    rec.served_by = header(res, "x-served-by-instance")
    const encoding = header(res, "content-encoding")
    if (encoding) rec.encoding = encoding
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

// The Dashboard in a new tab with a session. It shows after the first
// /users/me; the storage refresh, the second /users/me, the limit warning and
// the header's announcement go out together after it. The announcement carries
// no Bearer token, so the ALB's default rule sends it to the public service.
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
      // A browser whose refresh fails goes to the login page; this user ends
      // there, unfinished
      if (r?.status !== 200) return
      const token = jsonBody<{ access_token: string }>(r).access_token
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
        call(ctx, vu, "announcement", "GET", "/announcement", "public"),
      ])
      v.done = elapsed(start)
    },
    account,
  )
}

// The Dashboard's two /users/me alone, tokens refreshed before the release: a
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

// The owner opens the record's OutputsView from Dataview: reproduce, then
// every item the browser fetched for it, at once
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
    const items = await Promise.all(
      spec.items.map((p) =>
        call(ctx, vu, itemName(p), "GET", p, routeOf(p), {
          item: true,
          headers,
        }),
      ),
    )
    v.done = lastHeaders(items, start)
    v.done_body = elapsed(start)
  })
}

export type HeaderedPath = { path: string; headers: Record<string, string> }
export type PublicSpec = {
  list: string
  thumbnails: HeaderedPath[]
  reproduce: string
  items: HeaderedPath[]
}

// An anonymous visitor: PublicDataview with its thumbnails, then the record's
// OutputsView. Every request goes to the public service.
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
    const thumbnails = await Promise.all(
      spec.thumbnails.map((t) =>
        call(ctx, vu, itemName(t.path), "GET", t.path, "public", {
          item: true,
          headers: t.headers,
        }),
      ),
    )
    v.shown = lastHeaders(thumbnails, start)
    v.shown_body = elapsed(start)
    // The visitor opens the record once the list has drawn
    const dialogStart = performance.now()
    await call(ctx, vu, "public_reproduce", "GET", spec.reproduce, "public", {
      headers: ACCEPT_JSON,
    })
    const items = await Promise.all(
      spec.items.map((t) =>
        call(ctx, vu, itemName(t.path), "GET", t.path, "public", {
          item: true,
          headers: t.headers,
        }),
      ),
    )
    v.dialog = lastHeaders(items, dialogStart)
    v.dialog_body = elapsed(dialogStart)
    v.done = lastHeaders(items, start)
    v.done_body = elapsed(start)
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
  "GET /announcement": 1,
}

// The PublicDataview visit's requests besides the list, thumbnails, reproduce
// and items
const PUBLIC_FIXED_REQUESTS: Record<string, number> = {
  "POST /auth/refresh": 1,
  "GET /is_standalone": 1,
}

function requestKey(r: CaptureRow): string {
  return `${r.method} ${r.path.split("?")[0]}`
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

// A recorded request that failed or answered an error would be replayed as a
// smaller screen, or as an error the browser did not get through: the run
// stops instead. The anonymous visitor's refresh is refused by design.
function refuseFailedRows(rows: CaptureRow[], phases: string[]): void {
  const failed = rows.filter(
    (r) =>
      phases.includes(r.phase) &&
      (r.failed ||
        !r.status ||
        (r.status >= 400 &&
          !(requestKey(r) === "POST /auth/refresh" && r.status === 401))),
  )
  if (failed.length) {
    throw new CaptureChanged(
      `the recording has ${failed.length} failed request(s): ` +
        describeTally(
          tally(failed.map((r) => `${requestKey(r)} ${r.status ?? r.failed}`)),
        ),
    )
  }
}

// The Dashboard's order is fixed in dashboardUser; the capture only has to
// show the browser still sends exactly that set
export function checkDashboardCapture(rows: CaptureRow[]): void {
  refuseFailedRows(apiRows(rows), ["dashboard_new_tab"])
  const sent = tally(apiRows(rows, "dashboard_new_tab").map(requestKey))
  if (describeTally(sent) !== describeTally(DASHBOARD_REQUESTS)) {
    throw new CaptureChanged(
      `the Dashboard now sends ${describeTally(sent)}; ` +
        `the replay sends ${describeTally(DASHBOARD_REQUESTS)}`,
    )
  }
}

// What OutputsView from Dataview sends besides reproduce: its items, and the
// list of outputs the owner hid
const DIALOG_ITEM_PREFIXES = [
  "/api/visualizations/",
  "/api/dataview/hidden_outputs/",
]

export function dialogSpec(rows: CaptureRow[], record: RecordRef): DialogSpec {
  refuseFailedRows(apiRows(rows), ["dialog"])
  const dialog = apiRows(rows, "dialog")
  const reproduce = dialog.filter((r) =>
    r.path.includes("/workflow/reproduce/"),
  )
  const items = dialog.filter((r) => !r.path.includes("/workflow/reproduce/"))
  // The replay sends GETs of known kinds; anything else is a new request set
  const unknown = items.filter(
    (r) =>
      r.method !== "GET" ||
      !DIALOG_ITEM_PREFIXES.some((p) => r.path.startsWith(p)),
  )
  if (unknown.length) {
    throw new CaptureChanged(
      `OutputsView from Dataview now also sends ${describeTally(
        tally(unknown.map(requestKey)),
      )}; the replay sends reproduce, items and hidden outputs as GETs`,
    )
  }
  const own = `/${record.workspaceId}/${record.uid}`
  if (
    reproduce.length !== 1 ||
    !reproduce[0].path.endsWith(own) ||
    !items.length
  ) {
    throw new CaptureChanged(
      `OutputsView from Dataview sent ${reproduce.length} reproduce request(s) and ` +
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
  refuseFailedRows(apiRows(rows), ["public_list", "dialog"])
  const answered = apiRows(rows).filter(
    (r) => r.phase === "public_list" || r.phase === "dialog",
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
  const itemRows = answered.filter(
    (r) =>
      r.phase === "dialog" &&
      r.path.startsWith("/api/visualizations/") &&
      !r.path.includes("/thumbnail/") &&
      r.path.includes(`/${own}/`),
  )
  const items = itemRows.map((r) => ({
    path: r.path,
    headers: publicHeaders(r),
  }))
  const rest = tally(
    answered
      .filter(
        (r) =>
          !lists.includes(r) &&
          !reproduce.includes(r) &&
          !itemRows.includes(r) &&
          !r.path.includes("/api/visualizations/thumbnail/"),
      )
      .map(requestKey),
  )
  if (describeTally(rest) !== describeTally(PUBLIC_FIXED_REQUESTS)) {
    throw new CaptureChanged(
      "besides the list, thumbnails, reproduce and items, PublicDataview " +
        `now sends ${describeTally(rest) || "nothing"}; ` +
        `the replay sends ${describeTally(PUBLIC_FIXED_REQUESTS)}`,
    )
  }
  if (
    lists.length !== 1 ||
    reproduce.length !== 1 ||
    !thumbnails.length ||
    !items.length
  ) {
    throw new CaptureChanged(
      `PublicDataview sent ${lists.length} list, ${reproduce.length} reproduce, ` +
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

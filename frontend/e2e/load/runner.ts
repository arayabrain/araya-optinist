import * as fs from "fs"
import * as http from "http"
import * as path from "path"

import { Account, RecordRef } from "./config"
import {
  errorName,
  header,
  HttpResult,
  jsonBody,
  send,
  setCookie,
} from "./http"
import { Plan, PlanStep, stopThresholds } from "./plan"
import {
  authHeaders,
  dashboardUser,
  DialogSpec,
  dialogUser,
  publicUser,
  PublicSpec,
  Session,
  StepContext,
  usersMeUser,
  VirtualUser,
} from "./screens"
import {
  ProbeRecord,
  ProbeStat,
  RequestRecord,
  round3,
  Screen,
  StepSummary,
  summarize,
  Target,
  VuRecord,
} from "./summary"

// Probes before the release, so a step starts from a quiet service, and
// after the last user, so a stall the burst left behind still stops it
export const PROBE_BASELINE_S = 10
export const PROBE_TAIL_S = 10
export const PROBE_TIMEOUT_S = 10
// Between steps the probes go on at a lower rate: they show whether the
// services came back before the next step
export const WATCH_INTERVAL_S = 5
// Five per cent of at least 20 answered requests
const MIN_ANSWERED_FOR_5XX = 20
const MAX_5XX_SHARE = 0.05

const SETUP_TIMEOUT_MS = 60_000
// Firebase limits sign-ins per address and per project
const SIGN_IN_SPACING_MS = 3_000

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

const setupAgent = new http.Agent({ keepAlive: false })

function setupCall(
  base: URL,
  method: string,
  urlPath: string,
  opts: {
    headers?: Record<string, string>
    json?: unknown
    timeoutMs?: number
  } = {},
): Promise<HttpResult> {
  return send(setupAgent, base, method, urlPath, {
    timeoutMs: SETUP_TIMEOUT_MS,
    ...opts,
  })
}

type TokenBody = {
  access_token: string
  refresh_token: string
  ex_token?: string | null
}

// One sign-in per account per run; the tokens stay in memory
export async function signIn(
  base: URL,
  accounts: Account[],
): Promise<Session[]> {
  const sessions: Session[] = []
  for (const [i, account] of accounts.entries()) {
    if (i) await sleep(SIGN_IN_SPACING_MS)
    const res = await setupCall(base, "POST", "/auth/login", {
      json: { email: account.email, password: account.password },
    })
    if (res.status !== 200) {
      // Retrying would spend more of Firebase's sign-in budget
      throw new Error(
        `account ${i + 1} of the accounts file: sign-in answered ${res.status}`,
      )
    }
    const body = jsonBody<TokenBody>(res)
    sessions.push({
      accessToken: body.access_token,
      refreshToken: body.refresh_token,
      exToken: body.ex_token ?? null,
    })
  }
  return sessions
}

// ID tokens last an hour and a run outlasts one, so each step refreshes the
// ones it sends before its release
export async function refreshed(base: URL, s: Session): Promise<Session> {
  const res = await setupCall(base, "POST", "/auth/refresh", {
    json: { refresh_token: s.refreshToken },
  })
  if (res.status !== 200)
    throw new Error(`token refresh answered ${res.status}`)
  return {
    ...s,
    accessToken: jsonBody<{ access_token: string }>(res).access_token,
  }
}

export async function planName(base: URL, s: Session): Promise<string | null> {
  const res = await setupCall(base, "GET", "/users/me", {
    headers: authHeaders(s.accessToken, s.exToken),
  })
  if (res.status !== 200) throw new Error(`/users/me answered ${res.status}`)
  return (
    jsonBody<{ subscription_plan_name?: string | null }>(res)
      .subscription_plan_name ?? null
  )
}

export type RecordState = { id: number; publishStatus: number }

// The record's primary key and publish state, from its owner's listing. The
// uid alone is not unique: records are keyed by workspace and uid.
export async function findRecord(
  base: URL,
  owner: Session,
  ref: RecordRef,
): Promise<RecordState | null> {
  const query = `workspace_id=${ref.workspaceId}&uid=${encodeURIComponent(ref.uid)}&offset=0&limit=50`
  const res = await setupCall(base, "GET", `/api/dataview?${query}`, {
    headers: authHeaders(owner.accessToken, owner.exToken),
  })
  if (res.status !== 200)
    throw new Error(`the owner's Dataview listing answered ${res.status}`)
  const { items } = jsonBody<{
    items: { id: number; uid?: string; publish_status: number }[]
  }>(res)
  const item = items.find((i) => i.uid === ref.uid)
  return item ? { id: item.id, publishStatus: item.publish_status } : null
}

export const PUBLISHED = 1

// Publishing syncs and validates against S3 within the request
const PUBLISH_TIMEOUT_MS = 120_000

export async function setPublished(
  base: URL,
  owner: Session,
  id: number,
  on: boolean,
): Promise<void> {
  const res = await setupCall(
    base,
    "POST",
    `/api/dataview/publish/${id}/${on ? "on" : "off"}`,
    {
      headers: authHeaders(owner.accessToken, owner.exToken),
      timeoutMs: PUBLISH_TIMEOUT_MS,
    },
  )
  if (res.status !== 200)
    throw new Error(`publish ${on ? "on" : "off"} answered ${res.status}`)
}

export type Pin = { cookie: string; hash: string }

// The free instance a Bearer request reached, and the AWSALB cookie that
// keeps every later request on it
export async function takePin(base: URL, s: Session): Promise<Pin> {
  const res = await setupCall(base, "GET", "/is_standalone", {
    headers: authHeaders(s.accessToken, s.exToken),
  })
  const cookie = setCookie(res, "AWSALB")
  const hash = header(res, "x-served-by-instance")
  if (res.status !== 200 || !cookie || !hash) {
    throw new Error(
      `no pin: /is_standalone answered ${res.status}, AWSALB ${cookie ? "set" : "missing"}, ` +
        `instance hash ${hash ? "set" : "missing"}`,
    )
  }
  return { cookie, hash }
}

// ---------------------------------------------------------------------------
// Probes
// ---------------------------------------------------------------------------

const PROBE_PATH: Record<Target, string> = {
  public: "/health",
  free: "/is_standalone",
}

type Prober = {
  agent: http.Agent
  base: URL
  t0: number
  // Bearer and the pin: without a Bearer header the ALB answers
  // /is_standalone from the public service
  freeHeaders: Record<string, string>
}

function prober(base: URL, s: Session, pin: Pin, t0: number): Prober {
  return {
    agent: new http.Agent({ keepAlive: true, maxSockets: 2 }),
    base,
    t0,
    freeHeaders: {
      ...authHeaders(s.accessToken, s.exToken),
      Cookie: `AWSALB=${pin.cookie}`,
    },
  }
}

// Times out at the target's stop threshold, so a stall stops the step when
// it crosses the threshold rather than when the probe returns
async function probeOnce(
  p: Prober,
  target: Target,
  limitS: number,
): Promise<ProbeRecord> {
  const t = performance.now()
  const rec: ProbeRecord = {
    target,
    t: round3((t - p.t0) / 1000),
    status: null,
    dur: 0,
  }
  try {
    const res = await send(p.agent, p.base, "GET", PROBE_PATH[target], {
      headers: target === "free" ? p.freeHeaders : {},
      timeoutMs: Math.min(PROBE_TIMEOUT_S, limitS) * 1000,
    })
    rec.status = res.status
    rec.dur = round3((performance.now() - t) / 1000)
    rec.served_by = header(res, "x-served-by-instance")
  } catch (e) {
    rec.dur = round3((performance.now() - t) / 1000)
    rec.error = errorName(e)
  }
  return rec
}

// Probe failures in a row per target; returns the reason to stop, if any
function streak(
  fails: Record<Target, number>,
  rec: ProbeRecord,
  limits: Record<Target, number>,
): string | null {
  fails[rec.target] = rec.status === 200 ? 0 : fails[rec.target] + 1
  return rec.dur >= limits[rec.target] || fails[rec.target] >= 2
    ? `probe ${rec.target}: ${JSON.stringify(rec)}`
    : null
}

export function fiveXxReason(requests: RequestRecord[]): string | null {
  const answered = requests.filter((r) => r.status !== null)
  const five = answered.filter((r) => (r.status as number) >= 500)
  return answered.length >= MIN_ANSWERED_FOR_5XX &&
    five.length / answered.length > MAX_5XX_SHARE
    ? `5xx ${five.length}/${answered.length}`
    : null
}

async function probeLoop(
  p: Prober,
  ctx: StepContext,
  probes: ProbeRecord[],
  limits: Record<Target, number>,
  running: () => boolean,
): Promise<void> {
  const fails: Record<Target, number> = { public: 0, free: 0 }
  while (running()) {
    for (const target of ["public", "free"] as Target[]) {
      const rec = await probeOnce(p, target, limits[target])
      probes.push(rec)
      ctx.abortReason = ctx.abortReason ?? streak(fails, rec, limits)
    }
    ctx.abortReason = ctx.abortReason ?? fiveXxReason(ctx.requests)
    await sleep(1000)
  }
}

// ---------------------------------------------------------------------------
// One step
// ---------------------------------------------------------------------------

export type StepInput = {
  base: URL
  plan: Plan
  screen: Screen
  step: PlanStep
  // Index-aligned with the accounts file
  sessions: Session[]
  owner: number
  dialog?: DialogSpec
  public?: PublicSpec
  // The instance the run pinned first; a different one ends the run
  expectedHash: string | null
}

export type StepResult = {
  summary: StepSummary
  requests: RequestRecord[]
  probes: ProbeRecord[]
  vus: VuRecord[]
  pin: Pin
  probeSession: Session
}

export async function runStep(input: StepInput): Promise<StepResult> {
  const { base, plan, screen, step } = input
  const t0 = performance.now()
  const limits = stopThresholds(plan, input.step)
  const probeSession = await refreshed(base, input.sessions[0])
  const pin = await takePin(base, probeSession)
  if (input.expectedHash && pin.hash !== input.expectedHash) {
    throw new Error(
      "the pinned free instance is not the one this run started on",
    )
  }

  let release: (go: boolean) => void = () => undefined
  const ctx: StepContext = {
    base,
    pin: pin.cookie,
    t0,
    deadlineMs: plan.step_deadline_s * 1000,
    requests: [],
    vuRecords: [],
    abortReason: null,
    cancelled: false,
    released: new Promise((resolve) => (release = resolve)),
  }
  const users = Array.from({ length: step.users }, (_, i) => new VirtualUser(i))
  const n = input.sessions.length
  let tasks: Promise<void>[]
  if (screen === "dashboard") {
    tasks = users.map((vu) =>
      dashboardUser(ctx, vu, input.sessions[vu.index % n], vu.index % n),
    )
  } else if (screen === "usersme") {
    const headers: Record<string, string>[] = []
    for (const s of input.sessions) {
      const fresh = await refreshed(base, s)
      headers.push(authHeaders(fresh.accessToken, fresh.exToken))
    }
    tasks = users.map((vu) => usersMeUser(ctx, vu, headers[vu.index % n]))
  } else if (screen === "dialog") {
    const owner = await refreshed(base, input.sessions[input.owner])
    const headers = authHeaders(owner.accessToken, owner.exToken)
    tasks = users.map((vu) =>
      dialogUser(ctx, vu, headers, input.dialog as DialogSpec),
    )
  } else {
    tasks = users.map((vu) => publicUser(ctx, vu, input.public as PublicSpec))
  }

  const probes: ProbeRecord[] = []
  let probing = true
  const p = prober(base, probeSession, pin, t0)
  const probeTask = probeLoop(p, ctx, probes, limits, () => probing)
  await sleep(PROBE_BASELINE_S * 1000)
  let releaseAt: number | null = null
  if (ctx.abortReason) {
    release(false)
    await Promise.all(tasks)
  } else {
    release(true)
    releaseAt = round3((performance.now() - t0) / 1000)
    const all = Promise.all(tasks)
    let timer: NodeJS.Timeout | undefined
    let poll: NodeJS.Timeout | undefined
    const ended = await Promise.race([
      all.then(() => "done" as const),
      new Promise<"deadline">((resolve) => {
        timer = setTimeout(() => resolve("deadline"), ctx.deadlineMs)
      }),
      // A stop rule ends every user at once: their open and queued requests
      // would keep loading the service the rule found struggling
      new Promise<"stopped">((resolve) => {
        poll = setInterval(() => {
          if (ctx.abortReason) resolve("stopped")
        }, 100)
      }),
    ])
    clearTimeout(timer)
    clearInterval(poll)
    if (ended !== "done") {
      ctx.cancelled = ended === "deadline"
      for (const vu of users) vu.stop()
      await all
    }
    await sleep(PROBE_TAIL_S * 1000)
  }
  probing = false
  await probeTask
  p.agent.destroy()

  const summary = summarize({
    screen,
    vus: step.users,
    abortReason: ctx.abortReason,
    freeHash: pin.hash,
    requests: ctx.requests,
    probes,
    vuRecords: ctx.vuRecords,
    releaseAt,
  })
  return {
    summary,
    requests: ctx.requests,
    probes,
    vus: ctx.vuRecords,
    pin,
    probeSession,
  }
}

// ---------------------------------------------------------------------------
// Between steps
// ---------------------------------------------------------------------------

export type WatchResult = {
  seconds: number
  probes: ProbeRecord[]
  stats: Record<Target, ProbeStat>
  // Set when a service failed or reached its threshold twice in a row
  unhealthy: string | null
}

// Bad probes in a row per target while no load of ours runs. A lone slow or
// failed probe between fast ones is the load generator's own link (the ALB's
// response times stay flat through it); a service in trouble misses twice.
export function watchStreak(
  bad: Record<Target, number>,
  rec: ProbeRecord,
  limits: Record<Target, number>,
): string | null {
  const isBad = rec.status !== 200 || rec.dur >= limits[rec.target]
  bad[rec.target] = isBad ? bad[rec.target] + 1 : 0
  return bad[rec.target] >= 2
    ? `probe ${rec.target}: ${JSON.stringify(rec)}`
    : null
}

// The cool-down after a step, and the settle after the last one: no load,
// one probe pair every few seconds
export async function watch(
  base: URL,
  s: Session,
  pin: Pin,
  seconds: number,
  limits: Record<Target, number>,
): Promise<WatchResult> {
  const t0 = performance.now()
  const p = prober(base, s, pin, t0)
  const probes: ProbeRecord[] = []
  const bad: Record<Target, number> = { public: 0, free: 0 }
  let unhealthy: string | null = null
  while (performance.now() - t0 < seconds * 1000) {
    for (const target of ["public", "free"] as Target[]) {
      const rec = await probeOnce(p, target, limits[target])
      probes.push(rec)
      unhealthy = unhealthy ?? watchStreak(bad, rec, limits)
    }
    const left = seconds * 1000 - (performance.now() - t0)
    await sleep(Math.max(0, Math.min(WATCH_INTERVAL_S * 1000, left)))
  }
  p.agent.destroy()
  const stats = {} as Record<Target, ProbeStat>
  for (const target of ["public", "free"] as Target[]) {
    const mine = probes.filter((r) => r.target === target)
    stats[target] = {
      count: mine.length,
      failed: mine.filter((r) => r.status !== 200).length,
      p50: null,
      max: mine.length ? Math.max(...mine.map((r) => r.dur)) : null,
    }
  }
  return { seconds, probes, stats, unhealthy }
}

// ---------------------------------------------------------------------------
// Files: summary.json and the jsonl rows in the reference tool's layout
// ---------------------------------------------------------------------------

function writeJsonl(file: string, rows: object[]): void {
  fs.writeFileSync(file, rows.map((r) => JSON.stringify(r) + "\n").join(""))
}

export function writeStep(dir: string, result: StepResult): void {
  // A step's folder is never reused: two runs into one folder would mix
  fs.mkdirSync(dir)
  writeJsonl(path.join(dir, "requests.jsonl"), result.requests)
  writeJsonl(path.join(dir, "probes.jsonl"), result.probes)
  writeJsonl(path.join(dir, "vus.jsonl"), result.vus)
  fs.writeFileSync(
    path.join(dir, "summary.json"),
    JSON.stringify(result.summary, null, 1),
  )
}

// "after" in a step's folder for its cool-down, "settle" in the run's folder
export function writeWatch(
  dir: string,
  result: WatchResult,
  name: "after" | "settle",
): void {
  writeJsonl(path.join(dir, `probes-${name}.jsonl`), result.probes)
  const { probes: _probes, ...rest } = result
  fs.writeFileSync(
    path.join(dir, `${name}.json`),
    JSON.stringify(rest, null, 1),
  )
}

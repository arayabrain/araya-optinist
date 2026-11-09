// What one load step records, and the figures read from it. summary.json keeps
// the schema of the measurement tool the lane was ported from, so the steps
// measured before the lane existed compare with the lane's as they are.

export const SCREENS = ["dashboard", "usersme", "dialog", "public"] as const
export type Screen = (typeof SCREENS)[number]
export type Target = "public" | "free"
export type Route = Target

export type RequestRecord = {
  vu: number
  name: string
  route: Route
  start: number
  status: number | null
  // To the headers for an item or a thumbnail (the server's time), to the
  // last byte for the rest
  dur: number
  // To the last byte, for an item or a thumbnail
  full?: number
  bytes?: number
  // The answer's Content-Encoding, when it had one
  encoding?: string
  served_by?: string | null
  error?: string
}

export type ProbeRecord = {
  target: Target
  t: number
  status: number | null
  dur: number
  served_by?: string | null
  error?: string
}

// shown, dialog and done end at the last item's headers; the *_body ones at
// its last byte
export type VuRecord = {
  vu: number
  account?: number
  shown?: number
  dialog?: number
  done?: number
  shown_body?: number
  dialog_body?: number
  done_body?: number
  aborted?: boolean
  error?: string
}

export type Stat3 = {
  p50: number | null
  p95: number | null
  max: number | null
}

export type RequestStat = {
  count: number
  ok: number
  non_200: string[]
  p50: number | null
  p95: number | null
  max: number | null
  // As they crossed the wire
  bytes_total: number
  // Answers that came encoded; absent in steps whose client asked for none
  encoded?: number
  // To the last byte; absent where bodies were not read
  full_max?: number | null
}

export type ProbeStat = {
  count: number
  failed: number
  p50: number | null
  max: number | null
}

export type StepSummary = {
  screen: Screen
  vus: number
  abort_reason: string | null
  free_instance_hash: string | null
  free_served_by: string[]
  vu_done: number
  shown: Stat3
  done: Stat3
  // Absent in the steps measured before the PublicDataview screen existed
  dialog?: Stat3
  // Absent in the steps whose items were timed to the headers only
  shown_body?: Stat3
  dialog_body?: Stat3
  done_body?: Stat3
  requests: Record<string, RequestStat>
  probes: Record<Target, ProbeStat>
  release_at: number | null
}

// Python's round(): ties go to the even neighbour. The percentile index must
// round the same way, or a 10-sample p50 moves to the other middle value.
export function roundHalfEven(x: number): number {
  const floor = Math.floor(x)
  const rest = x - floor
  if (rest > 0.5) return floor + 1
  if (rest < 0.5) return floor
  return floor % 2 === 0 ? floor : floor + 1
}

// The sorted value at index p/100 x (n - 1), rounded half to even, as the
// reference tool computes it
export function pct(values: number[], p: number): number | null {
  if (!values.length) return null
  const sorted = [...values].sort((a, b) => a - b)
  const index = roundHalfEven((p / 100) * (sorted.length - 1))
  return sorted[Math.min(sorted.length - 1, index)]
}

export function round3(x: number): number {
  return Math.round(x * 1000) / 1000
}

// Half up on the decimal digits as written: toFixed rounds 0.565 down,
// because the double just under it is what it sees
export function fixed(x: number, digits: number): string {
  const scale = 10 ** digits
  return (Math.round(x * scale + 1e-9) / scale).toFixed(digits)
}

function stat3(values: number[]): Stat3 {
  return {
    p50: pct(values, 50),
    p95: pct(values, 95),
    max: values.length ? Math.max(...values) : null,
  }
}

export function summarize(step: {
  screen: Screen
  vus: number
  abortReason: string | null
  freeHash: string | null
  requests: RequestRecord[]
  probes: ProbeRecord[]
  vuRecords: VuRecord[]
  releaseAt: number | null
}): StepSummary {
  const byName = new Map<string, RequestRecord[]>()
  for (const r of step.requests) {
    byName.set(r.name, [...(byName.get(r.name) ?? []), r])
  }
  const requests: Record<string, RequestStat> = {}
  for (const [name, rs] of byName) {
    const durs = rs.filter((r) => r.status === 200).map((r) => r.dur)
    requests[name] = {
      count: rs.length,
      ok: durs.length,
      non_200: [
        ...new Set(
          rs
            .filter((r) => r.status !== 200)
            .map((r) => String(r.status ?? r.error)),
        ),
      ].sort(),
      p50: pct(durs, 50),
      p95: pct(durs, 95),
      max: durs.length ? Math.max(...durs) : null,
      bytes_total: rs.reduce((sum, r) => sum + (r.bytes ?? 0), 0),
      encoded: rs.filter((r) => r.encoding).length,
    }
    const fulls = rs
      .filter((r) => r.status === 200 && typeof r.full === "number")
      .map((r) => r.full as number)
    if (fulls.length) requests[name].full_max = Math.max(...fulls)
  }
  const served = [
    ...new Set(
      step.requests
        .filter((r) => r.route === "free" && r.served_by)
        .map((r) => r.served_by as string),
    ),
  ].sort()
  const probes = {} as Record<Target, ProbeStat>
  for (const target of ["public", "free"] as Target[]) {
    const mine = step.probes.filter((p) => p.target === target)
    const durs = mine.map((p) => p.dur)
    probes[target] = {
      count: durs.length,
      failed: mine.filter((p) => p.status !== 200).length,
      p50: pct(durs, 50),
      max: durs.length ? Math.max(...durs) : null,
    }
  }
  const of = (
    key:
      | "shown"
      | "done"
      | "dialog"
      | "shown_body"
      | "dialog_body"
      | "done_body",
  ) =>
    stat3(
      step.vuRecords
        .map((v) => v[key])
        .filter((x): x is number => typeof x === "number"),
    )
  return {
    screen: step.screen,
    vus: step.vus,
    abort_reason: step.abortReason,
    free_instance_hash: step.freeHash,
    free_served_by: served,
    vu_done: step.vuRecords.filter((v) => typeof v.done === "number").length,
    shown: of("shown"),
    done: of("done"),
    dialog: of("dialog"),
    shown_body: of("shown_body"),
    dialog_body: of("dialog_body"),
    done_body: of("done_body"),
    requests,
    probes,
    release_at: step.releaseAt,
  }
}

// ---------------------------------------------------------------------------
// Figures: what the plan's conditions and the report read from a step. They
// carry measured values only - no instance hash, host or record id - so the
// committed baseline is a list of them.
// ---------------------------------------------------------------------------

// A probe at its threshold (public, free) is a slow service; two failed
// probes in a row (public_down, free_down) one that stopped answering
export type StoppedBy =
  | "none"
  | "public"
  | "free"
  | "public_down"
  | "free_down"
  | "5xx"
  | "deadline"
  | "other"

export type StepFigures = {
  screen: Screen
  vus: number
  stopped_by: StoppedBy
  vu_done: number
  shown_max: number | null
  done_max: number | null
  dialog_max: number | null
  // To the last byte; absent where bodies were not read
  shown_body_max?: number | null
  dialog_body_max?: number | null
  done_body_max?: number | null
  public_probe_max: number | null
  free_probe_max: number | null
  requests: Record<string, RequestStat>
  // Filled from the run's post-checks; null where nobody read it
  cpu_max_pct?: Partial<Record<Target, number | null>>
  alb_response_max_s?: Partial<Record<Target, number | null>>
  // The stop thresholds the step ran with, where known
  probe_stop_s?: Partial<Record<Target, number>>
}

export function stoppedBy(abortReason: string | null): StoppedBy {
  if (!abortReason) return "none"
  const down = abortReason.match(/^probe (public|free) down\b/)
  if (down) return `${down[1] as Target}_down`
  const probe = abortReason.match(/^probe (public|free)\b/)
  if (probe) return probe[1] as Target
  if (abortReason.startsWith("5xx")) return "5xx"
  if (abortReason.startsWith("deadline")) return "deadline"
  return "other"
}

export function figuresOf(s: StepSummary): StepFigures {
  const figures: StepFigures = {
    screen: s.screen,
    vus: s.vus,
    stopped_by: stoppedBy(s.abort_reason),
    vu_done: s.vu_done,
    shown_max: s.shown.max,
    done_max: s.done.max,
    dialog_max: s.dialog?.max ?? null,
    public_probe_max: s.probes.public.max,
    free_probe_max: s.probes.free.max,
    requests: s.requests,
  }
  if (s.done_body) {
    figures.shown_body_max = s.shown_body?.max ?? null
    figures.dialog_body_max = s.dialog_body?.max ?? null
    figures.done_body_max = s.done_body.max
  }
  return figures
}

// Why a step stopped, in words. The stop reason itself holds the probe's
// record, instance hash included; this keeps the figures only. Reads both
// this lane's JSON and the reference tool's Python dict.
export function describeStop(s: StepSummary): string {
  const reason = s.abort_reason
  if (!reason) return "no stop"
  const by = stoppedBy(reason)
  if (by === "5xx" || by === "deadline") return reason
  if (by === "other") return "stopped"
  const num = (key: string) => {
    const m = reason.match(new RegExp(`["']${key}["']:\\s*([\\d.]+)`))
    return m ? Number(m[1]) : null
  }
  const dur = num("dur")
  const t = num("t")
  const when =
    t === null
      ? ""
      : s.release_at === null || t < s.release_at
        ? ", before the release"
        : `, ${fixed(t - s.release_at, 1)} s after the release`
  const status = num("status")
  const secs = dur === null ? "?" : fixed(dur, 2)
  if (by === "public_down" || by === "free_down") {
    const target = by.replace("_down", "")
    return `${target} stopped answering: two probes in a row failed${when}`
  }
  // A probe times out at its threshold, so a stall reads as a timeout
  if (status === null && /Timeout/.test(reason))
    return `${by} probe ≥ ${secs} s${when}`
  if (status === null) return `${by} probe failed${when}`
  if (status !== 200) return `${by} probe answered ${status}${when}`
  return `${by} probe ${secs} s${when}`
}

// The anonymous visitor's /auth/refresh is refused by design
const EXPECTED_STATUS: Record<string, number> = { auth_refresh_anon: 401 }

// The requests that answered other than a browser's would, with the answers
export function unexpectedAnswers(
  requests: Record<string, RequestStat>,
): string[] {
  const out: string[] = []
  for (const [name, r] of Object.entries(requests)) {
    const want = EXPECTED_STATUS[name] ?? 200
    const bad =
      want === 200 ? [...r.non_200] : r.non_200.filter((x) => x !== `${want}`)
    if (want !== 200 && r.ok) bad.push("200")
    if (bad.length) out.push(`${name}: ${bad.join(", ")}`)
  }
  return out
}

// A step passes when it did not stop, every virtual user finished, every
// request answered as a browser's would, and every free-route request reached
// the pinned instance.
export function failuresOf(s: StepSummary): string[] {
  const failures: string[] = []
  if (s.abort_reason) failures.push(`stopped: ${describeStop(s)}`)
  if (s.vu_done !== s.vus) failures.push(`${s.vu_done} / ${s.vus} users done`)
  failures.push(...unexpectedAnswers(s.requests))
  const strays = s.free_served_by.filter((h) => h !== s.free_instance_hash)
  if (strays.length) {
    failures.push(`${strays.length} other free instance(s) answered`)
  }
  return failures
}

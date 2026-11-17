import * as fs from "fs"
import * as path from "path"

import { BROWSER_SOCKETS_PER_HOST } from "./http"
import {
  figuresOf,
  fixed,
  RequestStat,
  Screen,
  StepFigures,
  StepSummary,
  StoppedBy,
  Target,
  unexpectedAnswers,
} from "./summary"

// The before / after report. It holds measured values only - no hostname,
// account, record id or instance hash - so it can be posted as it is.

export type Baseline = {
  measured_on: string
  note: string
  steps: StepFigures[]
}

export type LogHit = {
  target: Target
  kind: string
  route?: string
  timestamp: number
}

export type StepOutcome = {
  key: string
  summary: StepSummary
  figures: StepFigures
  failures: string[]
  stop: string
  // The cool-down (or the settle) that followed the step
  after?: {
    public_max: number | null
    free_max: number | null
    failed: number
    unhealthy: string | null
  }
  logs?: LogHit[]
  replaced?: string[]
}

export type RunReport = {
  plan: string
  startedAt: string
  endedAt: string
  baselineLabel: string
  overrides: string[]
  endedEarly: string | null
  notRun: { screen: Screen; users: number; reason: string }[]
  steps: StepOutcome[]
  post?: {
    targets: Record<Target, string[]>
    activities: {
      group: string
      start: string
      description: string
      cause: string
    }[]
    alb5xx: Record<Target | "alb", number>
    logTruncated: string[]
  }
  // Whether the watch after the last step ran, and why it found a service
  // unsettled, or null; absent in runs saved before the report held them
  settleRan?: boolean
  settleUnhealthy?: string | null
}

// ---------------------------------------------------------------------------
// Reading saved steps
// ---------------------------------------------------------------------------

// The steps under a results folder, one per screen and user count. Where a
// count has several folders (dialog-n1, dialog-n1-cold), the plain
// <screen>-n<users> one is the step; a lone suffixed one (dashboard-n1-dry)
// stands for its count.
export function readSteps(
  dir: string,
): { key: string; summary: StepSummary }[] {
  const found = fs
    .readdirSync(dir)
    .filter((name) => fs.existsSync(path.join(dir, name, "summary.json")))
    .map((name) => ({
      key: name,
      summary: JSON.parse(
        fs.readFileSync(path.join(dir, name, "summary.json"), "utf-8"),
      ),
    })) as { key: string; summary: StepSummary }[]
  const byStep = new Map<string, { key: string; summary: StepSummary }[]>()
  for (const f of found) {
    const id = `${f.summary.screen}-n${f.summary.vus}`
    byStep.set(id, [...(byStep.get(id) ?? []), f])
  }
  const chosen: { key: string; summary: StepSummary }[] = []
  for (const [id, candidates] of byStep) {
    const plain = candidates.find((c) => c.key === id)
    if (plain) chosen.push(plain)
    else if (candidates.length === 1) chosen.push(candidates[0])
    else
      throw new Error(
        `${dir}: ${candidates.map((c) => c.key).join(", ")} all claim ${id}`,
      )
  }
  return chosen
}

export function baselineFrom(
  dir: string,
  measuredOn: string,
  note: string,
): Baseline {
  return {
    measured_on: measuredOn,
    note,
    steps: readSteps(dir).map((s) => figuresOf(s.summary)),
  }
}

// A committed baseline file, or another run's results folder
export function loadBaseline(where: string): Baseline {
  if (fs.statSync(where).isDirectory()) {
    return baselineFrom(where, "", "the saved steps of another run")
  }
  return JSON.parse(fs.readFileSync(where, "utf-8")) as Baseline
}

// ---------------------------------------------------------------------------
// Cells
// ---------------------------------------------------------------------------

export function fmtS(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—"
  const digits = x >= 10 ? 1 : 2
  const shown = fixed(x, digits)
  // Rounded up to a whole number, a time under a threshold (5 s, 10 s) would
  // read as reaching it
  if (Number(shown) > x && Number.isInteger(Number(shown))) {
    const scale = 10 ** digits
    return `${(Math.floor(x * scale) / scale).toFixed(digits)} s`
  }
  return `${shown} s`
}

function fmtPct(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `${x} %`
}

function fmtMB(bytes: number | null): string {
  return bytes === null ? "—" : `${fixed(bytes / 1e6, 2)} MB`
}

function pair(
  before: StepFigures | undefined,
  after: StepFigures | undefined,
  pick: (f: StepFigures) => string,
  hasBaseline: boolean,
): string {
  const a = after ? pick(after) : "not run"
  if (!hasBaseline) return a
  return `${before ? pick(before) : "—"} → ${a}`
}

const STOP_WORDS: Record<StoppedBy, string> = {
  none: "no",
  public: "public probe",
  free: "free probe",
  public_down: "public not answering",
  free_down: "free not answering",
  "5xx": "5xx",
  deadline: "deadline",
  other: "yes",
}

function p50max(r: RequestStat | undefined): string {
  return r ? `${fmtS(r.p50)} / ${fmtS(r.max)}` : "—"
}

function thresholdNote(b: StepFigures, a: StepFigures): string {
  const notes: string[] = []
  for (const target of ["public", "free"] as Target[]) {
    const x = b.probe_stop_s?.[target]
    const y = a.probe_stop_s?.[target]
    if (x !== undefined && y !== undefined && x !== y)
      notes.push(`${target} threshold ${x} → ${y} s`)
  }
  return notes.length ? `; ${notes.join(", ")}` : ""
}

// Why a step that did not stop still cannot stand for the screen: users who
// never finished, or answers a browser would not get, make it look fast
function incomplete(f: StepFigures): string | null {
  if (f.vu_done < f.vus) return `${f.vu_done} / ${f.vus} users done`
  if (unexpectedAnswers(f.requests).length) return "error answers"
  return null
}

// Every row says which way it went: a share where both steps ran through,
// in words where one of them stopped or did not complete
export function improvement(
  b: StepFigures | undefined,
  a: StepFigures | undefined,
  headline: (f: StepFigures) => number | null,
): string {
  if (!a) return "not run"
  if (!b) return "no before"
  const note = thresholdNote(b, a)
  const stoppedBefore = b.stopped_by !== "none"
  const stoppedAfter = a.stopped_by !== "none"
  if (stoppedBefore && !stoppedAfter) return `stopped → no stop${note}`
  if (!stoppedBefore && stoppedAfter)
    return `no stop → stopped (${STOP_WORDS[a.stopped_by]})${note}`
  if (stoppedBefore && stoppedAfter) return `stopped both times${note}`
  const gaps = [
    [incomplete(b), "before"],
    [incomplete(a), "after"],
  ]
    .filter(([why]) => why)
    .map(([why, side]) => `${side}: ${why}`)
  if (gaps.length) return `not compared (${gaps.join("; ")})${note}`
  const x = headline(b)
  const y = headline(a)
  if (x === null || y === null || x === 0) return `not compared${note}`
  const change = (y - x) / x
  if (Math.abs(change) < 0.1) return `unchanged${note}`
  const share = Math.round(Math.abs(change) * 100)
  return `${share} % ${change < 0 ? "shorter" : "longer"}${note}`
}

function itemMax(f: StepFigures, exclude: string[]): number | null {
  const maxes = Object.entries(f.requests)
    .filter(([name]) => !exclude.includes(name))
    .map(([, r]) => r.max)
    .filter((x): x is number => x !== null)
  return maxes.length ? Math.max(...maxes) : null
}

function bytesPerUser(f: StepFigures | undefined): number | null {
  if (!f || !f.vus) return null
  const total = Object.values(f.requests).reduce(
    (sum, r) => sum + r.bytes_total,
    0,
  )
  return total / f.vus
}

// Requests that do not belong to the record: PublicDataview's list and its
// thumbnails follow whatever else is published
const NOT_THE_RECORD = ["auth_refresh_anon", "is_standalone", "public_list"]

function encoded(f: StepFigures, name: string): boolean {
  return (f.requests[name].encoded ?? 0) > 0
}

// The record's items both steps sent and that answered every time in both:
// a timed-out item would otherwise read as a smaller record
function recordItems(b: StepFigures, a: StepFigures): string[] {
  const answered = (f: StepFigures, n: string) =>
    f.requests[n].ok === f.requests[n].count
  return Object.keys(a.requests).filter(
    (n) =>
      n in b.requests &&
      !NOT_THE_RECORD.includes(n) &&
      !n.startsWith("thumbnail:") &&
      answered(a, n) &&
      answered(b, n),
  )
}

// Bytes per user, before and after, over the record's items both steps sent
// the same way, compressed in both or in neither; null when there are none
function recordBytes(
  b: StepFigures | undefined,
  a: StepFigures | undefined,
): [number, number] | null {
  if (!b?.vus || !a?.vus) return null
  const shared = recordItems(b, a).filter(
    (n) => encoded(a, n) === encoded(b, n),
  )
  if (!shared.length) return null
  const per = (f: StepFigures) =>
    shared.reduce((sum, n) => sum + f.requests[n].bytes_total, 0) / f.vus
  return [per(b), per(a)]
}

// ---------------------------------------------------------------------------
// Tables per screen
// ---------------------------------------------------------------------------

type Column = { title: string; cell: (f: StepFigures) => string }

const SCREEN_TABLES: Record<
  Screen,
  {
    title: string
    usersTitle: string
    columns: Column[]
    headline: (f: StepFigures) => number | null
  }
> = {
  dashboard: {
    title: "Dashboard after sign-in",
    usersTitle: "Users at once",
    headline: (f) => f.shown_max,
    columns: [
      { title: "Stopped", cell: (f) => STOP_WORDS[f.stopped_by] },
      { title: "Shown, last user", cell: (f) => fmtS(f.shown_max) },
      { title: "Done, last user", cell: (f) => fmtS(f.done_max) },
      {
        title: "Reached `/users/me`",
        cell: (f) => `${f.requests.users_me?.ok ?? 0}`,
      },
      {
        title: "`/auth/refresh` p50 / max",
        cell: (f) => p50max(f.requests.auth_refresh),
      },
      {
        title: "`refresh-storage` p50 / max",
        cell: (f) => p50max(f.requests.refresh_storage),
      },
      {
        title: "Probe max public / free",
        cell: (f) => `${fmtS(f.public_probe_max)} / ${fmtS(f.free_probe_max)}`,
      },
      {
        title: "CPU max free / public",
        cell: (f) =>
          `${fmtPct(f.cpu_max_pct?.free)} / ${fmtPct(f.cpu_max_pct?.public)}`,
      },
    ],
  },
  usersme: {
    title: "`/users/me` burst",
    usersTitle: "Users at once",
    headline: (f) => f.done_max,
    columns: [
      { title: "Stopped", cell: (f) => STOP_WORDS[f.stopped_by] },
      { title: "Done, last user", cell: (f) => fmtS(f.done_max) },
      {
        title: "Answered 200",
        cell: (f) => {
          const rs = [f.requests.users_me, f.requests.users_me_2].filter(
            Boolean,
          ) as RequestStat[]
          return `${rs.reduce((s, r) => s + r.ok, 0)} / ${rs.reduce((s, r) => s + r.count, 0)}`
        },
      },
      {
        title: "1st `/users/me` p50 / max",
        cell: (f) => p50max(f.requests.users_me),
      },
      {
        title: "2nd `/users/me` p50 / max",
        cell: (f) => p50max(f.requests.users_me_2),
      },
      { title: "Public probe max", cell: (f) => fmtS(f.public_probe_max) },
      { title: "Public CPU max", cell: (f) => fmtPct(f.cpu_max_pct?.public) },
    ],
  },
  dialog: {
    title: "OutputsView from Dataview, owner",
    usersTitle: "Users at once",
    headline: (f) => f.done_max,
    columns: [
      { title: "Stopped", cell: (f) => STOP_WORDS[f.stopped_by] },
      { title: "Users done", cell: (f) => `${f.vu_done} / ${f.vus}` },
      {
        title: "Every item answered, last user",
        cell: (f) => fmtS(f.done_max),
      },
      {
        title: "Every item read, last user",
        cell: (f) => fmtS(f.done_body_max),
      },
      { title: "Slowest item", cell: (f) => fmtS(itemMax(f, ["reproduce"])) },
      { title: "Free probe max", cell: (f) => fmtS(f.free_probe_max) },
      { title: "Free CPU max", cell: (f) => fmtPct(f.cpu_max_pct?.free) },
    ],
  },
  public: {
    title: "PublicDataview without sign-in",
    usersTitle: "Visitors at once",
    headline: (f) => f.done_max,
    columns: [
      { title: "Stopped", cell: (f) => STOP_WORDS[f.stopped_by] },
      { title: "List answered, last", cell: (f) => fmtS(f.shown_max) },
      {
        title: "OutputsView answered, last",
        cell: (f) => fmtS(f.dialog_max),
      },
      { title: "Whole visit answered, last", cell: (f) => fmtS(f.done_max) },
      { title: "Whole visit read, last", cell: (f) => fmtS(f.done_body_max) },
      { title: "Public probe max", cell: (f) => fmtS(f.public_probe_max) },
      { title: "Public CPU max", cell: (f) => fmtPct(f.cpu_max_pct?.public) },
    ],
  },
}

function table(head: string[], rows: string[][]): string[] {
  return [
    `| ${head.join(" | ")} |`,
    `|${head.map(() => "---").join("|")}|`,
    ...rows.map((r) => `| ${r.join(" | ")} |`),
  ]
}

function screenSection(
  screen: Screen,
  before: StepFigures[],
  after: StepFigures[],
  hasBaseline: boolean,
): string[] {
  const spec = SCREEN_TABLES[screen]
  const b = before.filter((f) => f.screen === screen)
  const a = after.filter((f) => f.screen === screen)
  if (!b.length && !a.length) return []
  const users = [...new Set([...b, ...a].map((f) => f.vus))].sort(
    (x, y) => x - y,
  )
  const rows = users.map((n) => {
    const bf = b.find((f) => f.vus === n)
    const af = a.find((f) => f.vus === n)
    return [
      `${n}`,
      ...spec.columns.map((c) => pair(bf, af, c.cell, hasBaseline)),
      hasBaseline ? improvement(bf, af, spec.headline) : "—",
    ]
  })
  const lines = [
    `## ${spec.title}`,
    "",
    ...table(
      [spec.usersTitle, ...spec.columns.map((c) => c.title), "Improvement"],
      rows,
    ),
  ]
  if (screen === "dialog") lines.push("", ...perItem(b, a, hasBaseline))
  return [...lines, ""]
}

// Server time per item at one user, and the bytes one open moves
function perItem(
  before: StepFigures[],
  after: StepFigures[],
  hasBaseline: boolean,
): string[] {
  const b = before.find((f) => f.vus === 1)
  const a = after.find((f) => f.vus === 1)
  if (!a && !b) return []
  const names = [
    ...new Set([
      ...Object.keys(b?.requests ?? {}),
      ...Object.keys(a?.requests ?? {}),
    ]),
  ].sort((x, y) =>
    x === "reproduce" ? -1 : y === "reproduce" ? 1 : x.localeCompare(y),
  )
  const rows = names.map((name) => {
    const x = b?.requests[name]?.p50 ?? null
    const y = a?.requests[name]?.p50 ?? null
    let change = "—"
    if (hasBaseline) {
      if (x === null) change = "new in this run"
      else if (y === null) change = "not sent in this run"
      else {
        const c = (y - x) / x
        change =
          Math.abs(c) < 0.1
            ? "unchanged"
            : `${Math.round(Math.abs(c) * 100)} % ${c < 0 ? "shorter" : "longer"}`
      }
    }
    return [
      `\`${name}\``,
      hasBaseline ? `${fmtS(x)} → ${fmtS(y)}` : fmtS(y),
      change,
      fmtS(a?.requests[name]?.full_max),
    ]
  })
  return [
    "Per item, 1 user (server time to the headers; read: to the last byte, slowest):",
    "",
    ...table(["Item", "Time", "Improvement", "Read"], rows),
    "",
    `Bytes per open: ${hasBaseline ? `${fmtMB(bytesPerUser(b))} → ` : ""}${fmtMB(bytesPerUser(a))}.`,
  ]
}

// ---------------------------------------------------------------------------
// Differences the comparison has to be read with
// ---------------------------------------------------------------------------

export function differences(
  before: StepFigures[],
  after: StepFigures[],
): string[] {
  const notes = [
    `Each user opens at most ${BROWSER_SOCKETS_PER_HOST} connections to the host, as a ` +
      "browser does on this HTTP/1.1 listener. The tool behind the 2026-11-02 and " +
      "2026-11-05 figures sent each user's parallel requests all at once (15 thumbnails " +
      "per PublicDataview visitor), so list and item times there are on the strict side.",
  ]
  for (const screen of ["dialog", "public"] as Screen[]) {
    const b = before.find((f) => f.screen === screen && f.vus === 1)
    const a = after.find((f) => f.screen === screen && f.vus === 1)
    const bytes = recordBytes(b, a)
    const recoded =
      b?.vus && a?.vus
        ? recordItems(b, a).filter((n) => encoded(a, n) !== encoded(b, n))
        : []
    if (recoded.length) {
      notes.push(
        `${SCREEN_TABLES[screen].title}: ${recoded.length} of the record's items came ` +
          "compressed in only one of the runs, so its bytes per open " +
          `(${fmtMB(bytesPerUser(b))} → ${fmtMB(bytesPerUser(a))}) compare the transfer; ` +
          (bytes
            ? "the fixture check reads the items sent the same way in both."
            : "no item was sent the same way in both, so the fixture is not checked by bytes."),
      )
    }
    if (
      bytes &&
      bytes[0] > 0 &&
      Math.abs(bytes[1] - bytes[0]) / bytes[0] > 0.01
    ) {
      notes.push(
        `${SCREEN_TABLES[screen].title}: the record's items that answered in both runs move ` +
          `${fmtMB(bytes[0])} → ${fmtMB(bytes[1])} per open at 1 user. Either the fixture ` +
          "differs or the server now answers the same items with other bytes; read its " +
          "times with that in mind.",
      )
    }
  }
  const bodies = (list: StepFigures[]) =>
    list.some((f) => f.done_body_max !== undefined)
  if (bodies(after) && !bodies(before)) {
    notes.push(
      'Items and thumbnails: the "answered" columns add up each phase\'s slowest ' +
        "server answer, timed from when its request got a connection, as the " +
        "reference tool (which had no connection limit) timed them; they give the " +
        'Improvement. The "read" columns are the user\'s wait to the last byte, ' +
        "with the waits for one of its 6 connections and the load generator's own " +
        "link, and have no before.",
    )
  }
  for (const screen of [
    "dashboard",
    "usersme",
    "dialog",
    "public",
  ] as Screen[]) {
    const names = (list: StepFigures[]) =>
      new Set(
        list
          .filter((f) => f.screen === screen)
          .flatMap((f) => Object.keys(f.requests)),
      )
    const b = names(before)
    const a = names(after)
    if (!b.size || !a.size) continue
    const added = [...a].filter((n) => !b.has(n))
    const gone = [...b].filter((n) => !a.has(n))
    if (added.length || gone.length) {
      notes.push(
        `${SCREEN_TABLES[screen].title}: the browser's request set changed` +
          (added.length
            ? `; now also ${added.map((n) => `\`${n}\``).join(", ")}`
            : "") +
          (gone.length
            ? `; no longer ${gone.map((n) => `\`${n}\``).join(", ")}`
            : "") +
          ".",
      )
    }
  }
  const thresholds = after
    .map((a) => {
      const b = before.find((f) => f.screen === a.screen && f.vus === a.vus)
      return b ? ([a, thresholdNote(b, a)] as const) : null
    })
    .filter((x): x is readonly [StepFigures, string] => !!x && !!x[1])
  for (const [a, note] of thresholds) {
    notes.push(
      `Stop thresholds, ${SCREEN_TABLES[a.screen].title} at ${a.vus}: ${note.slice(2)}.`,
    )
  }
  return notes
}

// ---------------------------------------------------------------------------
// Logs and robustness
// ---------------------------------------------------------------------------

export type Window = { key: string; from: number; to: number }

// Each hit to the step whose window (the step and its cool-down) holds it
export function bucket(
  hits: LogHit[],
  windows: Window[],
): Record<string, LogHit[]> {
  const out: Record<string, LogHit[]> = {}
  for (const w of windows) out[w.key] = []
  for (const h of hits) {
    const w = windows.find((x) => h.timestamp >= x.from && h.timestamp < x.to)
    if (w) out[w.key].push(h)
  }
  return out
}

// "—" where the logs were not read, so an unread step never shows as clean
function count(
  hits: LogHit[] | undefined,
  kind: string,
  route?: (r: string) => boolean,
): string {
  if (!hits) return "—"
  return `${hits.filter((h) => h.kind === kind && (!route || route(h.route ?? ""))).length}`
}

function fiveXx(hits: LogHit[] | undefined): string {
  return hits ? `${hits.filter((h) => /^5\d\d$/.test(h.kind)).length}` : "—"
}

function replacedCell(s: StepOutcome): string {
  if (!s.replaced) return "—"
  return s.replaced.length ? `yes: ${s.replaced.join("; ")}` : "no"
}

function robustness(steps: StepOutcome[]): string[] {
  const onDashboard = steps.filter(
    (s) => s.figures.screen === "dashboard" || s.figures.screen === "usersme",
  )
  if (!onDashboard.length) return []
  const rows = onDashboard.map((s) => [
    `${SCREEN_TABLES[s.figures.screen].title}, ${s.figures.vus}`,
    count(s.logs, "QueuePool"),
    count(s.logs, "401"),
    fiveXx(s.logs),
    replacedCell(s),
    s.after
      ? `probe max ${fmtS(s.after.public_max)} / ${fmtS(s.after.free_max)}, ${s.after.failed} failed`
      : "—",
  ])
  return [
    "## Dashboard robustness",
    "",
    "Server log lines in each step's window (the step and its cool-down), from every client.",
    "",
    ...table(
      [
        "Step",
        "DB pool timeouts (`QueuePool`)",
        "401 answers",
        "5xx answers",
        "Instance replaced",
        "After the step (public / free)",
      ],
      rows,
    ),
    "",
  ]
}

function logTable(steps: StepOutcome[]): string[] {
  const rows = steps.map((s) => [
    s.key,
    count(s.logs, "QueuePool"),
    count(s.logs, "Traceback"),
    count(s.logs, "Deadlock"),
    `${count(s.logs, "401")} (${count(s.logs, "401", (r) => r === "POST /auth/refresh")} on \`/auth/refresh\`)`,
    fiveXx(s.logs),
    `${fmtS(s.figures.alb_response_max_s?.free)} / ${fmtS(s.figures.alb_response_max_s?.public)}`,
  ])
  return table(
    [
      "Step",
      "`QueuePool`",
      "`Traceback`",
      "`Deadlock`",
      "401",
      "5xx",
      "ALB slowest answer free / public",
    ],
    rows,
  )
}

// ---------------------------------------------------------------------------
// The report
// ---------------------------------------------------------------------------

// Instance ids and ARNs reach the report in AWS's own words (scaling
// activities, refusal reasons); the report keeps what happened, not which
const REDACTIONS: [RegExp, string][] = [
  [/\barn:aws:[^\s;),]+/g, "<arn>"],
  [/\bi-[0-9a-f]{8,17}\b/g, "<instance>"],
]

export function redact(text: string): string {
  return REDACTIONS.reduce((out, [re, by]) => out.replace(re, by), text)
}

export function renderReport(
  baseline: Baseline | null,
  run: RunReport,
): string {
  const before = baseline?.steps ?? []
  const after = run.steps.map((s) => s.figures)
  const has = !!baseline
  const lines = [
    `# Web UI load run: plan \`${run.plan}\``,
    "",
    `- Ran ${run.startedAt} → ${run.endedAt} (UTC), development`,
    `- Baseline: ${
      has
        ? `${run.baselineLabel}${baseline?.measured_on ? ` (measured ${baseline.measured_on})` : ""}`
        : "none"
    }`,
    `- Overrides: ${run.overrides.length ? run.overrides.join("; ") : "none"}`,
  ]
  if (run.endedEarly) lines.push(`- **Ended early**: ${run.endedEarly}`)
  lines.push(
    "",
    "Cells read before → after. Times are for the last user of a step.",
    "",
  )
  if (has)
    lines.push(
      "## Read with",
      "",
      ...differences(before, after).map((d) => `- ${d}`),
      "",
    )
  for (const screen of [
    "dashboard",
    "usersme",
    "dialog",
    "public",
  ] as Screen[]) {
    lines.push(...screenSection(screen, before, after, has))
  }
  lines.push(...robustness(run.steps))
  const failed = run.steps.filter((s) => s.failures.length)
  lines.push("## Step results", "")
  for (const s of run.steps) {
    lines.push(
      `- \`${s.key}\`: ${s.failures.length ? `fails: ${s.failures.join("; ")}` : "passes"}`,
    )
  }
  if (!failed.length && run.steps.length)
    lines.push("", "Every step that ran passed.")
  if (run.notRun.length) {
    lines.push("", "Not run:", "")
    for (const n of run.notRun)
      lines.push(`- ${SCREEN_TABLES[n.screen].title}, ${n.users}: ${n.reason}`)
  }
  lines.push("")
  if (run.post) {
    const p = run.post
    lines.push("## Post-checks", "")
    lines.push(
      `- Targets at the end: free ${p.targets.free.join(", ") || "none"}; ` +
        `public ${p.targets.public.join(", ") || "none"}`,
    )
    if (run.settleRan === false) {
      lines.push("- The watch after the last step: not run")
    } else if (run.settleUnhealthy !== undefined) {
      lines.push(
        `- The watch after the last step: ${
          run.settleUnhealthy
            ? `a service did not settle (${run.settleUnhealthy.replace(/:.*$/, "")})`
            : "both services answered"
        }`,
      )
    }
    lines.push(
      `- Scaling activity since the start: ${
        p.activities.length
          ? p.activities
              .map((a) => `${a.start} ${a.description} (${a.cause})`)
              .join("; ")
          : "none"
      }`,
    )
    lines.push(
      `- 5xx over the run, every client: free targets ${p.alb5xx.free}, ` +
        `public targets ${p.alb5xx.public}, the ALB itself ${p.alb5xx.alb}`,
    )
    if (p.logTruncated.length)
      lines.push(`- Log reads cut at their limit: ${p.logTruncated.join(", ")}`)
    lines.push(
      "",
      "Server log lines per step window, and the ALB's slowest answer per " +
        "target over the step and the minute after it (a probe stop the ALB " +
        "did not see came from the load generator's side):",
      "",
      ...logTable(run.steps),
      "",
    )
  }
  return redact(lines.join("\n"))
}

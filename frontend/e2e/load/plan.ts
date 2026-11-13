import * as fs from "fs"
import * as path from "path"

import { nextScheduledStop } from "../helpers"

import { SCREENS, Screen, StepFigures, Target } from "./summary"

// A plan is the committed escalation policy: the steps per screen, how far a
// screen may go by default (its cap), and what each step needs from the one
// before it. Decisions taken mid-run on 2026-11-05 live here as data, so the
// next run takes the same ones.

export type PlanStep = {
  users: number
  // Stop thresholds for this step, over the plan's
  probe_stop_s?: Partial<Record<Target, number>>
  // Conditions on the previous step of the same screen, all of which must hold
  when?: string[]
  // Runs only with LOAD_GO_AHEAD=1, whatever the cap
  requires_go_ahead?: boolean
}

export type ScreenPlan = { screen: Screen; cap: number; steps: PlanStep[] }

export type Plan = {
  name: string
  step_deadline_s: number
  cooldown_s: number
  // Watched after the last step before the post-checks: a public instance
  // has been replaced 2 and 6 minutes after the load that broke it
  settle_s: number
  // No step may still be running at this time of day (UTC): development's
  // scheduler stops the environment at 13:00 UTC on weekdays
  cutoff_utc: string
  probe_stop_s: Record<Target, number>
  screens: ScreenPlan[]
  // Set on loading: the file of a plan from outside load/plans/
  local?: string
}

// Safety floors from the two manual runs. The public target group's health
// check times out at 5 s and three misses replace the instance, so a public
// threshold above 5 s cannot stop a step in time; the free group's check
// (30 s, five misses) leaves room for 10 s. A shorter cool-down lets the free
// tier's cpu-high alarm (2 x 2 min over 60 %) add an instance.
export const MAX_PROBE_STOP_S: Record<Target, number> = { public: 5, free: 10 }
export const MIN_COOLDOWN_S = 240
export const MIN_SETTLE_S = 600
export const MAX_STEP_DEADLINE_S = 120
// #846 asks for 100 users at once; no cap or go-ahead goes past it
export const MAX_USERS_PER_STEP = 100
// The scheduler stops development at 13:00 UTC on weekdays. The cut-off must
// leave the last step, the settle and the post-checks before it.
const SCHEDULED_STOP_UTC_S = 13 * 3600
const POST_CHECKS_S = 300

const E2E_DIR = path.resolve(__dirname, "..")
const PLAN_DIR = path.join(__dirname, "plans")
// The plans in the repository; any other file, in load/plans/ or not, is local
const COMMITTED_PLANS = ["default", "parity"]

// A path resolves from e2e/, as LOAD_ACCOUNTS_FILE and LOAD_RESULTS_DIR do
export function planPath(nameOrPath: string): string {
  return /[/\\]|\.json$/.test(nameOrPath)
    ? path.resolve(E2E_DIR, nameOrPath)
    : path.join(PLAN_DIR, `${nameOrPath}.json`)
}

// A plan from outside load/plans/ is local: its steps are its own, but its
// caps are held to the default plan's, so only LOAD_MAX_USERS_<SCREEN> with
// LOAD_GO_AHEAD raises them, and the report names the file
export function loadPlan(nameOrPath: string): Plan {
  const file = planPath(nameOrPath)
  if (!fs.existsSync(file)) throw new Error(`no plan at ${file}`)
  const plan = JSON.parse(fs.readFileSync(file, "utf-8")) as Plan
  const problems = planProblems(plan)
  if (problems.length) {
    throw new Error(`plan ${file}:\n  ${problems.join("\n  ")}`)
  }
  plan.local =
    path.dirname(file) === PLAN_DIR &&
    COMMITTED_PLANS.includes(path.basename(file, ".json"))
      ? undefined
      : file
  if (plan.local) {
    const committed = loadPlan("default")
    for (const s of plan.screens) {
      const cap = committed.screens.find((c) => c.screen === s.screen)?.cap
      s.cap = Math.min(s.cap, cap ?? 0)
    }
  }
  return plan
}

export function planProblems(plan: Plan): string[] {
  const problems: string[] = []
  if (
    !(plan.step_deadline_s > 0 && plan.step_deadline_s <= MAX_STEP_DEADLINE_S)
  ) {
    problems.push(`step_deadline_s must be 1..${MAX_STEP_DEADLINE_S}`)
  }
  if (!(plan.cooldown_s >= MIN_COOLDOWN_S)) {
    problems.push(`cooldown_s must be at least ${MIN_COOLDOWN_S}`)
  }
  if (!(plan.settle_s >= MIN_SETTLE_S)) {
    problems.push(`settle_s must be at least ${MIN_SETTLE_S}`)
  }
  // The name goes into the results folder's
  if (!/^[a-z0-9][a-z0-9-]*$/.test(plan.name ?? "")) {
    problems.push("name must be lower-case letters, digits and dashes")
  }
  const cutoff = (plan.cutoff_utc ?? "").match(/^([01]\d|2[0-3]):([0-5]\d)$/)
  if (!cutoff) {
    problems.push("cutoff_utc must be HH:MM")
  } else {
    const at = Number(cutoff[1]) * 3600 + Number(cutoff[2]) * 60
    if (
      at + plan.step_deadline_s + plan.settle_s + POST_CHECKS_S >
      SCHEDULED_STOP_UTC_S
    ) {
      problems.push(
        `cutoff_utc ${plan.cutoff_utc} leaves no room for the last step, the ` +
          "settle and the post-checks before the 13:00 UTC scheduled stop",
      )
    }
  }
  const thresholds = [
    ["plan", plan.probe_stop_s] as const,
    ...plan.screens.flatMap((s) =>
      s.steps.map(
        (st) => [`${s.screen} ${st.users}`, st.probe_stop_s] as const,
      ),
    ),
  ]
  for (const [where, stops] of thresholds) {
    for (const target of ["public", "free"] as Target[]) {
      const value = stops?.[target]
      if (
        value !== undefined &&
        !(value > 0 && value <= MAX_PROBE_STOP_S[target])
      ) {
        problems.push(
          `${where}: ${target} threshold must be above 0 and at most ` +
            `${MAX_PROBE_STOP_S[target]} s`,
        )
      }
    }
  }
  if (!plan.probe_stop_s?.public || !plan.probe_stop_s?.free) {
    problems.push("probe_stop_s needs public and free")
  }
  // A plan that sends no load would pass without measuring anything
  if (!plan.screens?.length) problems.push("the plan has no screen")
  const seen = new Set<string>()
  for (const s of plan.screens ?? []) {
    if (!SCREENS.includes(s.screen)) problems.push(`unknown screen ${s.screen}`)
    if (seen.has(s.screen)) problems.push(`${s.screen} listed twice`)
    seen.add(s.screen)
    if (!(s.cap > 0)) problems.push(`${s.screen}: cap must be positive`)
    if (!s.steps?.length) problems.push(`${s.screen}: no step`)
    ;(s.steps ?? []).forEach((st, i) => {
      if (!(Number.isInteger(st.users) && st.users > 0)) {
        problems.push(
          `${s.screen}: step ${i + 1} users must be a positive integer`,
        )
      }
      if (st.users > MAX_USERS_PER_STEP) {
        problems.push(
          `${s.screen}: ${st.users} users is above the ${MAX_USERS_PER_STEP} the lane allows`,
        )
      }
      if (i > 0 && st.users <= s.steps[i - 1].users) {
        problems.push(
          `${s.screen}: steps must rise (${st.users} after ${s.steps[i - 1].users})`,
        )
      }
      if (i === 0 && st.when?.length) {
        problems.push(
          `${s.screen}: the first step has no previous step to test`,
        )
      }
      for (const cond of st.when ?? []) {
        try {
          parseCondition(cond)
        } catch (e) {
          problems.push(`${s.screen} ${st.users}: ${(e as Error).message}`)
        }
      }
    })
  }
  return problems
}

export function stopThresholds(
  plan: Plan,
  step: PlanStep,
): Record<Target, number> {
  return { ...plan.probe_stop_s, ...step.probe_stop_s }
}

// ---------------------------------------------------------------------------
// Conditions, parsed rather than evaluated:
//   previous.<number metric> <|<=|>|>=|==|!= <number>
//   previous.stopped_by ==|!= <reason>,  previous.stopped_by in <reason>,...
// ---------------------------------------------------------------------------

const NUMERIC = [
  "public_probe_max",
  "free_probe_max",
  "done_max",
  "vu_done",
] as const
type NumericMetric = (typeof NUMERIC)[number]
type Op = "<" | "<=" | ">" | ">=" | "==" | "!="
const STOP_REASONS = [
  "none",
  "public",
  "free",
  "public_down",
  "free_down",
  "5xx",
  "deadline",
  "other",
]

export type Condition =
  | { metric: NumericMetric; op: Op; value: number; text: string }
  | { metric: "stopped_by"; op: "in" | "not in"; value: string[]; text: string }

export function parseCondition(text: string): Condition {
  const m = text.match(/^previous\.(\w+)\s+(<=|>=|==|!=|<|>|in)\s+(\S+)$/)
  if (!m)
    throw new Error(`condition "${text}" is not previous.<metric> <op> <value>`)
  const [, metric, op, raw] = m
  if (metric === "stopped_by") {
    if (!["==", "!=", "in"].includes(op)) {
      throw new Error(`stopped_by takes ==, != or in, not ${op}`)
    }
    const value = raw.split(",")
    const unknown = value.filter((v) => !STOP_REASONS.includes(v))
    if (unknown.length) {
      throw new Error(
        `stopped_by is one of ${STOP_REASONS.join(", ")}, not ${unknown}`,
      )
    }
    return { metric, op: op === "!=" ? "not in" : "in", value, text }
  }
  if (!(NUMERIC as readonly string[]).includes(metric)) {
    throw new Error(`unknown metric ${metric}`)
  }
  if (op === "in") throw new Error(`${metric} does not take in`)
  const value = Number(raw)
  if (!Number.isFinite(value)) throw new Error(`${raw} is not a number`)
  return { metric: metric as NumericMetric, op: op as Op, value, text }
}

function compare(a: number, op: Op, b: number): boolean {
  switch (op) {
    case "<":
      return a < b
    case "<=":
      return a <= b
    case ">":
      return a > b
    case ">=":
      return a >= b
    case "==":
      return a === b
    case "!=":
      return a !== b
  }
}

export function conditionHolds(
  cond: Condition,
  previous: StepFigures,
): boolean {
  if (cond.metric === "stopped_by") {
    return cond.value.includes(previous.stopped_by) === (cond.op === "in")
  }
  const actual = previous[cond.metric]
  // A figure nobody measured cannot satisfy a condition on it
  if (actual === null || actual === undefined) return false
  return compare(actual, cond.op, cond.value)
}

// ---------------------------------------------------------------------------
// Which steps run
// ---------------------------------------------------------------------------

export type StepGates = {
  // LOAD_MAX_USERS_<SCREEN>, for this run only
  capOverride?: number
  goAhead: boolean
}

export type Decision =
  | { run: true }
  | { run: false; reason: string; endsScreen: boolean }

// previous is the last step of this screen that ran, or null for the first.
export function decideStep(
  screen: ScreenPlan,
  step: PlanStep,
  previous: StepFigures | null,
  gates: StepGates,
): Decision {
  const cap = gates.capOverride ?? screen.cap
  if (step.users > cap) {
    return {
      run: false,
      reason:
        `above the cap of ${cap}` + (gates.capOverride ? " (override)" : ""),
      endsScreen: true,
    }
  }
  const needsGoAhead = step.requires_go_ahead || step.users > screen.cap
  if (needsGoAhead && !gates.goAhead) {
    return { run: false, reason: "needs LOAD_GO_AHEAD=1", endsScreen: true }
  }
  if (!previous) return { run: true }
  const conditions = (step.when ?? []).map(parseCondition)
  // A stopped step ends its screen unless the next step names the stop it
  // accepts
  if (!conditions.some((c) => c.metric === "stopped_by")) {
    conditions.unshift(parseCondition("previous.stopped_by == none"))
  }
  const failed = conditions.filter((c) => !conditionHolds(c, previous))
  if (failed.length) {
    return {
      run: false,
      reason: `previous step: not ${failed.map((c) => c.text).join(", not ")}`,
      endsScreen: true,
    }
  }
  return { run: true }
}

// The step a screen is held to: its largest one this run's cap and go-ahead
// allow. Conditions do not lower it - a screen that stopped short of it fails.
export function targetStep(
  screen: ScreenPlan,
  gates: StepGates,
): PlanStep | null {
  const allowed = screen.steps.filter((step) => {
    const d = decideStep(screen, step, null, gates)
    return d.run
  })
  return allowed.length ? allowed[allowed.length - 1] : null
}

// The moment no step may still be running: the plan's cut-off on the day of
// the next scheduled stop, or the announced window's end, whichever comes
// first. A cut-off already passed stays passed (a run started at 12:50 UTC
// gets none), so no step runs into the stop.
export function cutoffAt(plan: Plan, now: Date, windowEnd: Date): Date {
  const [hh, mm] = plan.cutoff_utc.split(":").map(Number)
  const daily = nextScheduledStop(now)
  daily.setUTCHours(hh, mm, 0, 0)
  return daily < windowEnd ? daily : windowEnd
}

// Whether a step taking this many seconds from now ends before the cut-off
export function fitsBefore(end: Date, now: number, seconds: number): boolean {
  return now + seconds * 1000 <= end.getTime()
}

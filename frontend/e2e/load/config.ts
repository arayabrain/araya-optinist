import * as fs from "fs"
import * as path from "path"

import { SCREENS, Screen } from "./summary"

// Everything environment-specific comes from e2e/.env (or the shell), so the
// repository holds no hostname, account or record id. The accounts file holds
// passwords: it is read, never printed, and nothing derived from it is written.

export type Account = { email: string; password: string }
export type RecordRef = { workspaceId: number; uid: string }

export type LoadConfig = {
  base: URL
  accounts: Account[]
  dialogRecord: RecordRef
  dialogOwner: Account
  publicRecord: RecordRef
  publish: boolean
  plan: string
  screens: Screen[] | null
  maxUsers: Partial<Record<Screen, number>>
  goAhead: boolean
  windowEnd: Date
  windowAnnounced: boolean
  imageDigest: string
  resultsDir: string
}

// The Free Manager targets ceil(active users in 5 min / 5) instances, so six
// distinct users scale the free tier out and the pinned instance stops being
// the only one serving.
export const MAX_DISTINCT_ACCOUNTS = 5

const E2E_DIR = path.resolve(__dirname, "..")

type Env = Record<string, string | undefined>

// Why this environment must not be loaded at all, or "" when it may.
// Separate from the configuration so a run that was not asked for skips
// rather than fails.
export function laneSkipReason(env: Env): string {
  if (!env.RUN_LOAD_AWS) {
    return "loads the shared development environment; opt in with RUN_LOAD_AWS=1"
  }
  return ""
}

// Refusals that are a mistake in an explicit request, so they fail the run
export function targetProblems(env: Env): string[] {
  const problems: string[] = []
  const base = env.BASE_URL || ""
  // The port matters: without :8080 the ALB redirects, and the redirect
  // drops the Authorization header
  if (!base.includes("development-optinist") || !/:8080\/?$/.test(base)) {
    problems.push("BASE_URL must be development's ALB origin, ending in :8080")
  }
  if (env.HEALTH_ENV && env.HEALTH_ENV !== "development") {
    problems.push(
      `HEALTH_ENV=${env.HEALTH_ENV}: this lane only reads development`,
    )
  }
  return problems
}

export function parseRecordRef(value: string, name: string): RecordRef {
  const m = value.match(/^(\d+)\/([A-Za-z0-9_-]+)$/)
  if (!m) throw new Error(`${name} must be <workspace id>/<uid>`)
  return { workspaceId: Number(m[1]), uid: m[2] }
}

// email<TAB>password per line. Errors name the line, never its content.
export function parseAccounts(text: string): Account[] {
  const accounts: Account[] = []
  text.split(/\r?\n/).forEach((line, i) => {
    if (!line.trim()) return
    const fields = line.split("\t")
    if (fields.length !== 2 || !fields[0].trim() || !fields[1]) {
      throw new Error(`accounts file line ${i + 1} is not email<TAB>password`)
    }
    accounts.push({ email: fields[0].trim(), password: fields[1] })
  })
  return accounts
}

function flag(env: Env, name: string): boolean {
  return !!env[name] && env[name] !== "0"
}

export function readConfig(env: Env): LoadConfig {
  const problems: string[] = []
  const need = (name: string): string => {
    const value = env[name]
    if (!value) problems.push(`${name} is not set`)
    return value ?? ""
  }

  let accounts: Account[] = []
  const accountsFile = need("LOAD_ACCOUNTS_FILE")
  if (accountsFile) {
    const file = path.resolve(E2E_DIR, accountsFile)
    if (!fs.existsSync(file)) {
      problems.push(`LOAD_ACCOUNTS_FILE: no file at ${file}`)
    } else {
      try {
        accounts = parseAccounts(fs.readFileSync(file, "utf-8"))
      } catch (e) {
        problems.push(`LOAD_ACCOUNTS_FILE: ${(e as Error).message}`)
      }
    }
  }
  problems.push(...accountProblems(accounts, env))

  const ref = (name: string, fallback?: string): RecordRef => {
    const value = env[name] || fallback
    if (!value) {
      problems.push(`${name} is not set`)
      return { workspaceId: 0, uid: "" }
    }
    try {
      return parseRecordRef(value, name)
    } catch (e) {
      problems.push((e as Error).message)
      return { workspaceId: 0, uid: "" }
    }
  }
  const dialogRecord = ref("LOAD_DIALOG_RECORD")
  const publicRecord = ref("LOAD_PUBLIC_RECORD", env.LOAD_DIALOG_RECORD)

  const ownerEmail = need("LOAD_DIALOG_OWNER").toLowerCase()
  const dialogOwner = accounts.find((a) => a.email.toLowerCase() === ownerEmail)
  if (ownerEmail && accounts.length && !dialogOwner) {
    problems.push(
      "LOAD_DIALOG_OWNER is not one of the accounts file's accounts",
    )
  }

  let screens: Screen[] | null = null
  if (env.LOAD_SCREENS) {
    const asked = env.LOAD_SCREENS.split(",").map((s) => s.trim())
    const unknown = asked.filter(
      (s) => !(SCREENS as readonly string[]).includes(s),
    )
    if (unknown.length)
      problems.push(`LOAD_SCREENS: unknown ${unknown.join(", ")}`)
    screens = asked as Screen[]
  }

  const maxUsers: Partial<Record<Screen, number>> = {}
  for (const screen of SCREENS) {
    const name = `LOAD_MAX_USERS_${screen.toUpperCase()}`
    if (!env[name]) continue
    const value = Number(env[name])
    if (!Number.isInteger(value) || value <= 0) {
      problems.push(`${name} must be a positive integer`)
    } else {
      maxUsers[screen] = value
    }
  }

  const windowEndText = need("LOAD_WINDOW_END")
  const windowEnd = new Date(windowEndText)
  if (windowEndText && Number.isNaN(windowEnd.getTime())) {
    problems.push("LOAD_WINDOW_END must be an ISO-8601 time with its offset")
  } else if (windowEndText && !/([+-]\d\d:?\d\d|Z)$/.test(windowEndText)) {
    // A bare local time reads differently on a runner in another zone
    problems.push(
      "LOAD_WINDOW_END needs its UTC offset, e.g. 2026-11-10T17:00+09:00",
    )
  } else if (windowEndText && windowEnd.getTime() <= Date.now()) {
    problems.push("LOAD_WINDOW_END has already passed")
  }

  const imageDigest = need("LOAD_IMAGE_DIGEST")
  if (imageDigest && !/^sha256:[0-9a-f]{64}$/.test(imageDigest)) {
    problems.push("LOAD_IMAGE_DIGEST must be sha256:<64 hex>")
  }

  if (problems.length) {
    throw new Error(`load lane configuration:\n  ${problems.join("\n  ")}`)
  }
  return {
    base: new URL(env.BASE_URL as string),
    accounts,
    dialogRecord,
    dialogOwner: dialogOwner as Account,
    publicRecord,
    publish: flag(env, "LOAD_PUBLISH"),
    plan: env.LOAD_PLAN || "default",
    screens,
    maxUsers,
    goAhead: flag(env, "LOAD_GO_AHEAD"),
    windowEnd,
    windowAnnounced: flag(env, "LOAD_WINDOW_ANNOUNCED"),
    imageDigest,
    resultsDir: path.resolve(E2E_DIR, env.LOAD_RESULTS_DIR || "load-results"),
  }
}

export function accountProblems(accounts: Account[], env: Env): string[] {
  if (!accounts.length)
    return env.LOAD_ACCOUNTS_FILE ? ["the accounts file is empty"] : []
  const problems: string[] = []
  const emails = new Set(accounts.map((a) => a.email.toLowerCase()))
  if (emails.size !== accounts.length)
    problems.push("the accounts file repeats an account")
  // globalSetup signs the suite's own free user in too
  if (env.TEST_USER_EMAIL) emails.add(env.TEST_USER_EMAIL.toLowerCase())
  if (emails.size > MAX_DISTINCT_ACCOUNTS) {
    problems.push(
      `${emails.size} distinct accounts (the accounts file plus TEST_USER_EMAIL); ` +
        `more than ${MAX_DISTINCT_ACCOUNTS} scales the free tier out`,
    )
  }
  return problems
}

// The accounts active in the last 30 minutes that are neither the suite's
// nor this run's: somebody's work in progress.
export function othersActive(active: string[], accounts: Account[]): string[] {
  const ours = new Set(accounts.map((a) => a.email.toLowerCase()))
  return active.filter((email) => !ours.has(email.toLowerCase()))
}

import { execSync } from "child_process"
import * as fs from "fs"
import * as path from "path"

import {
  APIRequestContext,
  chromium,
  expect,
  Page,
  request,
  test,
} from "@playwright/test"

// ---------------------------------------------------------------------------
// Local docker stack. Specs that need state no API exposes (a plan, a role, a
// verified email) drive the containers directly, so they run against the local
// stack only and skip elsewhere.
// ---------------------------------------------------------------------------

export const REPO_ROOT = path.resolve(__dirname, "../..")
const COMPOSE = "docker compose -f docker-compose.dev.multiuser.yml"
const DOCKER_EXEC_TIMEOUT_MS = 30_000

export function runSql(sql: string): string {
  return execSync(
    `${COMPOSE} exec -T db sh -c ` +
      `'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -N "$MYSQL_DATABASE"'`,
    {
      cwd: REPO_ROOT,
      input: sql,
      stdio: ["pipe", "pipe", "pipe"],
      // execSync blocks the event loop, so a hung exec cannot be cut short by
      // the test timeout: without this the run stalls to the global timeout
      timeout: DOCKER_EXEC_TIMEOUT_MS,
    },
  )
    .toString()
    .trim()
}

export function runInBackend(cmd: string, input?: string): string {
  return execSync(`${COMPOSE} exec -T studio-dev-be ${cmd}`, {
    cwd: REPO_ROOT,
    stdio: ["pipe", "pipe", "pipe"],
    input,
    timeout: DOCKER_EXEC_TIMEOUT_MS,
  })
    .toString()
    .trim()
}

// Registration leaves the address unverified, and an unverified account cannot
// log in. Dev Firebase has no inbox to click through.
export function verifyEmail(email: string) {
  runInBackend(
    "poetry run python -",
    `
import firebase_admin
from firebase_admin import auth, credentials
cred = credentials.Certificate("studio/config/auth/firebase_private.json")
firebase_admin.initialize_app(cred)
auth.update_user(auth.get_user_by_email("${email}").uid, email_verified=True)
`,
  )
}

export function deleteFirebaseUser(email: string) {
  runInBackend(
    "poetry run python -",
    `
import firebase_admin
from firebase_admin import auth, credentials
cred = credentials.Certificate("studio/config/auth/firebase_private.json")
firebase_admin.initialize_app(cred)
try:
    auth.delete_user(auth.get_user_by_email("${email}").uid)
except auth.UserNotFoundError:
    pass
`,
  )
}

// A run that dies before its afterAll orphans the Firebase user even though the
// DB row is gone, so sweep by the throwaway shape `<prefix>_<Date.now()>@test.com`
// rather than by what the DB still knows; fixed accounts carry no timestamp.
export function sweepE2eFirebaseUsers(): string {
  return runInBackend(
    "poetry run python -",
    `
import re
import firebase_admin
from firebase_admin import auth, credentials
cred = credentials.Certificate("studio/config/auth/firebase_private.json")
firebase_admin.initialize_app(cred)
stale = [
    u.uid
    for u in auth.list_users().iterate_all()
    if re.fullmatch(r"e2e_[a-z_]+_\\d{13}@test\\.com", u.email or "")
]
for i in range(0, len(stale), 1000):
    auth.delete_users(stale[i : i + 1000])
print(len(stale))
`,
  )
}

export function activeUserRows(email: string): number {
  return Number(
    runSql(
      `SELECT COUNT(*) FROM users WHERE email = '${sqlLiteral(email)}'
         AND active = 1;`,
    ),
  )
}

// Register + verify, idempotently. Throws with both statuses when the account
// still cannot log in, because a silent failure here surfaces much later as an
// unrelated assertion. Local stack only: the duplicate-row check below reads
// the docker DB directly, so callers must gate on localStackSkipReason().
export async function ensureRegisteredUser(
  email: string,
  password: string,
  name: string,
  roleId = 20,
): Promise<void> {
  const api = await request.newContext({ baseURL: apiUrl() })
  const register = () =>
    api.post("/api/register", {
      data: { name, role_id: roleId, email, password },
    })
  try {
    const creds = { email, password }
    const existing = await api.post("/auth/login", { data: creds })
    if (existing.ok()) return
    // A row means the account is real and the configured password is simply
    // wrong. Registering anyway adds a SECOND active row at this address,
    // which nothing in the schema prevents and which then breaks every lookup
    // expecting a single id - including this spec's own `beforeAll`.
    if (activeUserRows(email) > 0) {
      throw new Error(
        `${email} already has an active row but its password does not match: ` +
          `${existing.status()} ${await existing.text()}`,
      )
    }
    // A 5xx is the dev backend restarting under --reload, not a verdict on the
    // account: deleting a real Firebase user on one is unrecoverable.
    if (existing.status() >= 500) {
      throw new Error(
        `login for ${email} failed transiently, not deleting it: ` +
          `${existing.status()} ${await existing.text()}`,
      )
    }
    // No row, so whatever Firebase still holds is an orphan from a reset stack
    // or a deletion that stopped after its Firebase step. Left in place it
    // makes registration answer EMAIL_EXISTS forever.
    deleteFirebaseUser(email)
    const registered = await register()
    verifyEmail(email)
    const loggedIn = await api.post("/auth/login", { data: creds })
    if (!loggedIn.ok()) {
      throw new Error(
        `bootstrap failed for ${email}: register ${registered.status()} ` +
          `${await registered.text()}; login ${loggedIn.status()} ` +
          `${await loggedIn.text()}`,
      )
    }
  } finally {
    await api.dispose()
  }
}

// The confirm-by-typing dialog used for deletions
export function confirmDialog(page: Page) {
  return page.locator('[role="dialog"]')
}

// Hold a request open until the test releases it, so an assertion on an
// in-flight state never races a wall clock. No Promise.withResolvers on node 20
export function routeGate() {
  let release = () => {}
  const held = new Promise<void>((resolve) => (release = resolve))
  return { held, release: () => release() }
}

export function isLocalBaseUrl(): boolean {
  return /localhost|127\.0\.0\.1/.test(
    process.env.BASE_URL || "http://localhost:3000",
  )
}

// Returns the reason a docker-driven spec cannot run here, or "" if it can.
export function localStackSkipReason(): string {
  if (!isLocalBaseUrl()) {
    return "spec mutates the local docker DB; BASE_URL is not local"
  }
  try {
    runSql("SELECT 1;")
  } catch {
    return "local docker db container not reachable"
  }
  return ""
}

export function sqlLiteral(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/'/g, "''")
}

// Storage state saved after a single UI login; authed specs reuse it so each run
// needs only a handful of Firebase logins (rate limits)
const AUTH_DIR = path.join(__dirname, ".auth")
export const FREE_STORAGE_STATE = path.join(AUTH_DIR, "free.json")
export const ADMIN_STORAGE_STATE = path.join(AUTH_DIR, "admin.json")

export function freeStorageState(): string | undefined {
  return fs.existsSync(FREE_STORAGE_STATE) ? FREE_STORAGE_STATE : undefined
}

// Resolved per test rather than at module load, because the admin account does
// not exist until the spec's own beforeAll has registered and promoted it
export function adminStorageState(): string | undefined {
  return fs.existsSync(ADMIN_STORAGE_STATE) ? ADMIN_STORAGE_STATE : undefined
}

// One UI login, saved for reuse. Retries because a cold CRA dev server can take
// longer than the login timeout to compile the login route.
export async function saveStorageState(
  statePath: string,
  email: string,
  password: string,
  baseURL = process.env.BASE_URL || "http://localhost:3000",
) {
  fs.mkdirSync(path.dirname(statePath), { recursive: true })
  const browser = await chromium.launch()
  try {
    // storageState must be cleared explicitly: a page opened inside a spec's
    // `test.use()` scope inherits that scope's state, so this one would start
    // already signed in as the previous run, be redirected off /login before
    // the form is filled, and save that stale token back over the file.
    const page = await browser.newPage({ baseURL, storageState: undefined })
    for (let attempt = 1; ; attempt++) {
      await page.goto("/login")
      await page.locator('[data-testid="email"]').fill(email)
      await page.locator('[data-testid="password"]').fill(password)
      await page.locator('[data-testid="button-submit"]').click()
      try {
        await page.waitForURL(/\/dashboard/, { timeout: 60_000 })
        break
      } catch (e) {
        if (attempt >= 3) throw e
      }
    }
    await page.context().storageState({ path: statePath })
  } finally {
    await browser.close()
  }
}

// For specs running with the saved storage state: land on the dashboard
// without going through the login form
export async function gotoDashboard(page: Page) {
  await page.goto("/dashboard")
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 30_000 })
  await dismissStorageWarning(page)
}

// Credentials come from env vars or e2e/.env (gitignored) — never commit them.
export const FREE_USER = {
  email: process.env.TEST_USER_EMAIL || "",
  password: process.env.TEST_USER_PASSWORD || "",
}
export const PREMIUM_USER = {
  email: process.env.TEST_PREMIUM_EMAIL || "",
  password: process.env.TEST_PREMIUM_PASSWORD || "",
}
export const UNVERIFIED_USER = {
  email: process.env.TEST_UNVERIFIED_EMAIL || "",
  password: process.env.TEST_UNVERIFIED_PASSWORD || "",
}

export function skipWithoutCreds(
  user: { email: string; password: string } = FREE_USER,
  name = "TEST_USER_EMAIL/TEST_USER_PASSWORD",
) {
  test.skip(!user.email || !user.password, `${name} not set`)
}

export async function login(
  page: Page,
  email = FREE_USER.email,
  password = FREE_USER.password,
  dismissWarning = true,
) {
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.goto("/login")
    await expect(page.locator('[data-testid="button-submit"]')).toBeVisible({
      timeout: 30_000,
    })
    await page.locator('[data-testid="email"]').fill(email)
    await page.locator('[data-testid="password"]').fill(password)
    await page.locator('[data-testid="button-submit"]').click()
    try {
      await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })
      if (dismissWarning) await dismissStorageWarning(page)
      return
    } catch {
      // Login timed out — retry
    }
  }
  throw new Error(`Login failed after 3 attempts for ${email}`)
}

export async function logout(page: Page) {
  await page.locator('[aria-label="open profile menu"]').click()
  await page.locator('li:has-text("Sign Out")').click()
  // A "Jobs Running" dialog may appear if jobs are active
  const signOutAnyway = page.locator('button:has-text("Sign Out Anyway")')
  try {
    await expect(signOutAnyway).toBeVisible({ timeout: 2_000 })
    await signOutAnyway.click()
  } catch {
    // No running jobs — continue
  }
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 })
}

export async function dismissStorageWarning(page: Page) {
  const handleLater = page.locator('button:has-text("Handle later")')
  try {
    await expect(handleLater).toBeVisible({ timeout: 3_000 })
    await handleLater.click()
  } catch {
    // No storage warning — continue
  }
}

export async function goToWorkspaces(page: Page) {
  await page.goto("/workspaces")
  await expect(page.locator("text=Workspaces").first()).toBeVisible({
    timeout: 15_000,
  })
}

// Shared workspace for data-dependent specs (03/05/06/07): sample data is
// imported once per run; global-setup deletes all e2e-* workspaces at start
export const DATA_WS = "e2e-data"

export function apiUrl(): string {
  const baseURL = process.env.BASE_URL || "http://localhost:3000"
  return process.env.API_URL || baseURL.replace(/:\d+$/, ":8000")
}

// A backend on USE_FIREBASE_TOKEN=False validates ExToken, not the Bearer
// token, so both have to be sent to cover either auth mode
export function authHeaders(token: string, exToken?: string | null) {
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
  if (exToken) headers.ExToken = exToken
  return headers
}

// A request context authenticated by API login, for work that has no page:
// global-setup (before any browser exists) and the cleanup group
export async function apiLogin(
  email = FREE_USER.email,
  password = FREE_USER.password,
): Promise<{ api: APIRequestContext; headers: Record<string, string> }> {
  const api = await request.newContext({ baseURL: apiUrl() })
  const res = await api.post("/auth/login", { data: { email, password } })
  if (!res.ok()) {
    // Read the body before dispose(), which tears the response down with it
    const body = await res.text()
    await api.dispose()
    throw new Error(
      `TEST_USER_EMAIL/TEST_USER_PASSWORD rejected by ${apiUrl()}/auth/login ` +
        `(${res.status()}): ${body}`,
    )
  }
  const { access_token, ex_token } = await res.json()
  return { api, headers: authHeaders(access_token, ex_token) }
}

// Deletes the account's e2e-* workspaces and returns the names removed.
// DELETE /workspace/{id} also drops the workspace's input/output data, in S3
// too when remote storage is on.
export async function deleteE2eWorkspaces(
  api: APIRequestContext,
  headers: Record<string, string>,
): Promise<string[]> {
  const list = await api.get("/workspaces?offset=0&limit=100", { headers })
  if (!list.ok()) {
    throw new Error(`GET /workspaces ${list.status()}: ${await list.text()}`)
  }
  const { items } = await list.json()
  const deleted: string[] = []
  for (const ws of items as { id: number; name: string }[]) {
    if (!/^e2e-/.test(ws.name)) continue
    const res = await api.delete(`/workspace/${ws.id}`, { headers })
    if (res.ok()) deleted.push(ws.name)
  }
  return deleted
}

// The app keeps its tokens in localStorage, so page.request needs them
// passed explicitly; the page must already be on the app origin
export async function apiHeaders(page: Page) {
  const { token, exToken } = await page.evaluate(() => ({
    token: localStorage.getItem("access_token"),
    exToken: localStorage.getItem("ExToken"),
  }))
  if (!token) {
    throw new Error(
      "No access_token in localStorage — is the storage state stale? Delete e2e/.auth and rerun.",
    )
  }
  return authHeaders(token, exToken)
}

// Find-or-create a workspace via the API — avoids the virtualized grid
// (row ordering, render races) for tests that aren't about the grid itself
export async function ensureWorkspaceId(
  page: Page,
  name: string,
): Promise<number> {
  const headers = await apiHeaders(page)
  const list = await page.request.get(
    `${apiUrl()}/workspaces?offset=0&limit=100`,
    { headers },
  )
  if (!list.ok()) {
    throw new Error(`GET /workspaces ${list.status()}: ${await list.text()}`)
  }
  const { items } = await list.json()
  const found = items.find((w: { name: string }) => w.name === name)
  if (found) return found.id
  const created = await page.request.post(`${apiUrl()}/workspace`, {
    headers,
    data: { name },
  })
  if (!created.ok()) {
    throw new Error(
      `POST /workspace ${created.status()}: ${await created.text()}`,
    )
  }
  return (await created.json()).id
}

export async function openWorkspace(page: Page, name: string): Promise<number> {
  const id = await ensureWorkspaceId(page, name)
  await page.goto(`/workspaces/${id}`)
  await expect(
    page.locator('button[role="tab"]:has-text("Workflow")'),
  ).toBeVisible({ timeout: 15_000 })
  return id
}

export async function importSampleData(page: Page, workspaceName: string) {
  // The import silently no-ops until GET /workspace/{id} populates the store,
  // and the Record tab renders nothing that proves it has. Probe on Workflow,
  // then switch to Record, where the menu entry is enabled.
  await page.locator('button[role="tab"]:has-text("Workflow")').click()
  await expect(page.locator(`text=${workspaceName}`).first()).toBeVisible({
    timeout: 15_000,
  })
  await page.locator('button[role="tab"]:has-text("Record")').click()
  await page.locator('[data-testid="MenuBookIcon"]').click()
  await page.getByText("Import sample data").click()
  const dialog = page.locator('[role="dialog"]:has-text("Import sample data?")')
  await expect(dialog).toBeVisible()
  await dialog.getByRole("button", { name: "OK" }).click()
  await expect(page.locator("text=Sample data import success")).toBeVisible({
    timeout: 120_000,
  })
}

// Go to the Record tab; import sample data first if no records exist yet
export async function ensureTutorialRecords(page: Page, workspaceName: string) {
  await page.locator('button[role="tab"]:has-text("Record")').click()
  const hasRecords = await page
    .locator('[data-testid="reproduce-button"]')
    .first()
    .waitFor({ timeout: 10_000 })
    .then(() => true)
    .catch(() => false)
  if (!hasRecords) {
    await importSampleData(page, workspaceName)
    await expect(
      page.locator('[data-testid="reproduce-button"]').first(),
    ).toBeVisible({ timeout: 30_000 })
  }
}

export async function reproduceTutorial(page: Page, tutorialName: string) {
  await page.locator('button[role="tab"]:has-text("Record")').click()
  const row = page.locator(`tr:has-text("${tutorialName}")`).first()
  await expect(row).toBeVisible({ timeout: 15_000 })
  // Disabled while a previous run is still settling — wait, then reload
  // once (a fresh fetch clears stale running state)
  const reproduceButton = row.locator('[data-testid="reproduce-button"]')
  const enabled = await expect(reproduceButton)
    .toBeEnabled({ timeout: 60_000 })
    .then(() => true)
    .catch(() => false)
  if (!enabled) {
    await page.reload()
    await page.locator('button[role="tab"]:has-text("Record")').click()
    await expect(row).toBeVisible({ timeout: 15_000 })
    await expect(reproduceButton).toBeEnabled({ timeout: 60_000 })
  }
  await reproduceButton.click()
  const dialog = page.locator('[role="dialog"]:has-text("Reproduce workflow?")')
  await expect(dialog).toBeVisible({ timeout: 10_000 })
  await dialog.locator('[data-testid="reproduce-confirm-button"]').click()
  await expect(page.locator("text=Successfully reproduced.")).toBeVisible({
    timeout: 60_000,
  })
  // Reproduce loads the workflow into the flowchart tab
  await expect(
    page.locator('button[role="tab"]:has-text("Workflow")'),
  ).toHaveAttribute("aria-selected", "true", { timeout: 30_000 })
}

// The run split button: after a reproduce it defaults to by-uid "RUN"
// (immediate, same uid, reuses existing outputs); "RUN ALL" starts a fresh
// run under a new uid via the name dialog. Select the mode explicitly so a
// test exercises the path it claims to.
async function startRun(page: Page, mode: "RUN" | "RUN ALL") {
  await page.locator('button:has([data-testid="ArrowDropDownIcon"])').click()
  await page.locator(`li:text-is("${mode}")`).click()
  // Anchor on the run POST actually going out (the click's storage
  // pre-check makes the dispatch async, and navigating away too early
  // silently cancels it)
  const runStarted = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      /\/run\//.test(r.url()) &&
      !r.url().includes("/run/result"),
  )
  await page.locator(`button:text-is("${mode}")`).click()
  if (mode === "RUN ALL") {
    const dialog = page.locator(
      '[role="dialog"]:has-text("Name and run workflow")',
    )
    await expect(dialog).toBeVisible({ timeout: 15_000 })
    // Must not contain "Tutorial" — record-row locators match by substring
    await dialog.locator("input").fill("e2e-runall")
    await dialog.getByRole("button", { name: "Run", exact: true }).click()
  }
  // Both /run/{ws} and /run/{ws}/{uid} answer the run's uid as a bare string
  const response = await runStarted
  return {
    workspaceId: response.url().match(/\/run\/(\d+)/)?.[1] ?? "",
    uid: String(await response.json()),
  }
}

// experiment.yaml's own success flag, which finalization writes: "running"
// until the pipeline settles, then "success" or "error".
async function recordedRunStatus(
  page: Page,
  workspaceId: string,
  uid: string,
): Promise<string> {
  const headers = await apiHeaders(page)
  const res = await page.request.get(`${apiUrl()}/experiments/${workspaceId}`, {
    headers,
  })
  if (!res.ok()) return `GET /experiments/${workspaceId} -> ${res.status()}`
  const experiments = (await res.json()) as Record<string, { success?: string }>
  return experiments?.[uid]?.success ?? "absent"
}

// Reproduce a tutorial and run it to completion. Loading an already-finished
// experiment fires a phantom "Workflow finished" snackbar, so wait it out
// before waiting for the real one. "RUN ALL" = fresh uid, full pipeline
// compute (5-10 min, @slow); "RUN" = by-uid rerun, which snakemake treats
// as already complete for the imported tutorials (seconds).
// A real snakemake run. The ceiling is environment-dependent: the docker stack
// CI uses clears a tutorial well inside the default, a deployed environment can
// take far longer, so RUN_TIMEOUT_MS raises it without a code edit.
export const RUN_TIMEOUT_MS = Number(process.env.RUN_TIMEOUT_MS ?? 840_000)
// After the snackbar, how long the recorded result may take to settle
const RECORD_TIMEOUT_MS = 300_000
// The surrounding reproduce/start steps need headroom above the run itself
export const RUN_TEST_TIMEOUT_MS = RUN_TIMEOUT_MS + RECORD_TIMEOUT_MS + 60_000

export async function runTutorial(
  page: Page,
  tutorialName: string,
  mode: "RUN" | "RUN ALL" = "RUN ALL",
) {
  await reproduceTutorial(page, tutorialName)
  const { workspaceId, uid } = await startRun(page, mode)
  const finished = page.locator("text=Workflow finished")
  const aborted = page.locator("text=Workflow aborted")
  await expect(finished).toBeHidden({ timeout: 30_000 })
  // Race the two terminal snackbars: waiting on success alone burns the whole
  // ceiling on a run that already died, and reports it as a plain timeout
  await expect(finished.or(aborted).first()).toBeVisible({
    timeout: RUN_TIMEOUT_MS,
  })
  await expect(
    aborted,
    `${tutorialName} aborted instead of finishing`,
  ).toHaveCount(0)

  // The snackbar alone is not proof. Where the workflow PID file is not visible
  // to the API process, unrun nodes are reported errored, no node is left
  // pending, and the frontend calls that FINISHED seconds into the run.
  let recorded = ""
  await expect
    .poll(
      async () => (recorded = await recordedRunStatus(page, workspaceId, uid)),
      {
        timeout: RECORD_TIMEOUT_MS,
        message: `${tutorialName} (${uid}) never settled in experiment.yaml`,
      },
    )
    .toMatch(/^(success|error)$/)
  expect(recorded, `${tutorialName} recorded "${recorded}", not success`).toBe(
    "success",
  )
}

// Mints the success record + thumbnails the dataview needs.
//
// This is NOT a no-op: `git ls-files sample_data/tutorial/output` is 12 files,
// all of them experiment/snakemake/workflow YAML. No node output directories, no
// JSON, no NWB ship at all, and global setup deletes the e2e-* workspace each
// run, so snakemake recomputes from scratch. Budget a real run (see RUN_TIMEOUT_MS
// inner wait in runTutorial), not the ~15s a rerun-by-uid would take.
export async function ensureCompletedTutorialRun(
  page: Page,
  wsName: string,
  tutorialName = "Tutorial1",
) {
  await ensureTutorialRecords(page, wsName)
  await runTutorial(page, tutorialName, "RUN")
}

// Publishing requires a cloud bucket on the account (the backend 400s
// without one). Local-stack users have none, so set a placeholder attribute
// - the S3-existence check is skipped in local storage mode, and all S3
// size lookups swallow errors. On deployed envs (no docker) this silently
// no-ops; users there have real buckets.
export function ensurePublishableAccount() {
  const email = process.env.TEST_USER_EMAIL
  if (!email) return
  try {
    runSql(
      `UPDATE users SET attributes = JSON_SET(
         COALESCE(attributes, JSON_OBJECT()),
         '$.remote_bucket_name', 'e2e-local-placeholder')
       WHERE email = '${sqlLiteral(email)}';`,
    )
  } catch {
    // Not a local stack
  }
}

// Shared routing contract fixture: sourcing the mock bodies from it keeps them
// on the real response shape and guarantees no body carries a user_id.
const PREMIUM_CONTRACT = JSON.parse(
  fs.readFileSync(
    path.join(
      __dirname,
      "..",
      "src",
      "utils",
      "routing",
      "__fixtures__",
      "premium_routing",
      "premium_contract.json",
    ),
    "utf-8",
  ),
)

// Route-mock an active premium assignment (status + heartbeat + beacon token)
// for assignment-dependent UI where the real AWS flow is unreachable. No
// X-Routing-ID header: the local backend runs non-standalone and rejects a
// request carrying a routing_id it did not mint, so seeding a fake one here
// would 403 every later real request. Seeding is covered at the jest
// interceptor level instead.
//
// `shared` mocks the shared-resource fallback instead of a dedicated instance.
// That is a different frontend branch: with no dedicated instance the app keeps
// its "being prepared" notice up rather than announcing success, and it records
// the fallback in the routing service.
// `scaling` mocks the instance-still-starting state: no assignment yet, and the
// assign call answers the retryable scaling shape the app polls on.
export async function mockPremiumAssignment(
  page: Page,
  {
    shared = false,
    scaling = false,
  }: { shared?: boolean; scaling?: boolean } = {},
) {
  const status = PREMIUM_CONTRACT.premium_status
  await page.route("**/users/me/premium/status", (route) =>
    route.fulfill({
      json: {
        ...status,
        assignment: scaling
          ? null
          : {
              ...status.assignment,
              assigned_at: new Date().toISOString(),
              is_shared: shared,
            },
      },
    }),
  )
  if (scaling) {
    // The retry branch emits exactly these fields (response_model_exclude_unset
    // strips the rest), so the mock must not carry instance fields with it
    await page.route("**/users/me/premium/assign", (route) =>
      route.fulfill({
        json: {
          message: "Premium instance is being prepared",
          assigned: false,
          scaling_in_progress: true,
          retry_after: 30,
        },
      }),
    )
  }
  await page.route("**/users/me/premium/heartbeat", (route) =>
    route.fulfill({ json: PREMIUM_CONTRACT.premium_heartbeat }),
  )
  await page.route("**/users/me/premium/beacon-token", (route) =>
    route.fulfill({ json: { token: "e2e" } }),
  )
}

// Server-side filter on the Workspace column, which is only offered where
// DataviewRecords renders without a workspaceId: /dataview and /public
export async function filterWorkspace(page: Page, value: string) {
  const header = page.locator(
    '.MuiDataGrid-columnHeader[data-field="workspace_name"]',
  )
  await header.hover()
  await header.locator(".MuiDataGrid-menuIcon button").click()
  await page.getByRole("menuitem", { name: /^filter$/i }).click()
  await page.locator(".MuiDataGrid-filterForm input").last().fill(value)
  await page.keyboard.press("Escape")
}

export async function createWorkspace(page: Page, name: string) {
  await goToWorkspaces(page)
  await page.locator('button:has-text("New")').first().click()
  const dialog = page.locator('[role="dialog"]')
  await expect(dialog).toBeVisible()
  await dialog.locator('[placeholder="Workspace Name"]').fill(name)
  await dialog.locator('button:has-text("Ok")').click()
  await expect(dialog).toBeHidden({ timeout: 15_000 })
  await expect(page.locator(`text=${name}`).first()).toBeVisible({
    timeout: 30_000,
  })
}

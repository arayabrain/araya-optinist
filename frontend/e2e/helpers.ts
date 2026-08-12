import * as fs from "fs"
import * as path from "path"

import {
  APIRequestContext,
  expect,
  Page,
  request,
  test,
} from "@playwright/test"

// Storage state saved by global-setup after a single UI login; authed specs
// reuse it so each run needs only a handful of Firebase logins (rate limits)
export const FREE_STORAGE_STATE = path.join(__dirname, ".auth", "free.json")

export function freeStorageState(): string | undefined {
  return fs.existsSync(FREE_STORAGE_STATE) ? FREE_STORAGE_STATE : undefined
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

// Navigate to workspaces and wait for at least one data row.
// Returns false if the table stayed empty (tests should then skip).
export async function goToWorkspacesWithData(page: Page): Promise<boolean> {
  await goToWorkspaces(page)
  const workflowButton = page.locator('button:has-text("Workflow")').first()
  try {
    await expect(workflowButton).toBeVisible({ timeout: 30_000 })
    return true
  } catch {
    return false
  }
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
    await api.dispose()
    throw new Error(
      `TEST_USER_EMAIL/TEST_USER_PASSWORD rejected by ${apiUrl()}/auth/login ` +
        `(${res.status()}): ${await res.text()}`,
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
async function apiHeaders(page: Page) {
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
  // The import menu silently no-ops until GET /workspace/{id} populates the
  // store; the workspace name, rendered only by the Workflow tab, signals
  // it's ready
  await page.locator('button[role="tab"]:has-text("Workflow")').click()
  await expect(page.locator(`text=${workspaceName}`).first()).toBeVisible({
    timeout: 15_000,
  })
  // The menu item is disabled off the Record tab
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
  await runStarted
}

// Reproduce a tutorial and run it to completion. Loading an already-finished
// experiment fires a phantom "Workflow finished" snackbar, so wait it out
// before waiting for the real one. "RUN ALL" = fresh uid, full pipeline
// compute (5-10 min, @slow); "RUN" = by-uid rerun, which snakemake treats
// as already complete for the imported tutorials (seconds).
export async function runTutorial(
  page: Page,
  tutorialName: string,
  mode: "RUN" | "RUN ALL" = "RUN ALL",
) {
  await reproduceTutorial(page, tutorialName)
  await startRun(page, mode)
  await expect(page.locator("text=Workflow finished")).toBeHidden({
    timeout: 30_000,
  })
  await expect(page.locator("text=Workflow finished")).toBeVisible({
    timeout: 840_000,
  })
}

// Mints the success record + thumbnails the dataview needs.
//
// This is NOT a no-op: `git ls-files sample_data/tutorial/output` is 12 files,
// all of them experiment/snakemake/workflow YAML. No node output directories, no
// JSON, no NWB ship at all, and global setup deletes the e2e-* workspace each
// run, so snakemake recomputes from scratch. Budget a real run (see the 840s
// inner wait in runTutorial), not the ~15s a rerun-by-uid would take.
export async function ensureCompletedTutorialRun(
  page: Page,
  wsName: string,
  tutorialName = "Tutorial1",
) {
  await ensureTutorialRecords(page, wsName)
  await runTutorial(page, tutorialName, "RUN")
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

// Route-mock an active dedicated premium assignment (status + heartbeat +
// beacon token) for assignment-dependent UI where the real AWS flow is
// unreachable. No X-Routing-ID header: the local backend runs non-standalone
// and rejects a request carrying a routing_id it did not mint, so seeding a
// fake one here would 403 every later real request. Seeding is covered at the
// jest interceptor level instead.
export async function mockPremiumAssignment(page: Page) {
  const status = PREMIUM_CONTRACT.premium_status
  await page.route("**/users/me/premium/status", (route) =>
    route.fulfill({
      json: {
        ...status,
        assignment: {
          ...status.assignment,
          assigned_at: new Date().toISOString(),
        },
      },
    }),
  )
  await page.route("**/users/me/premium/heartbeat", (route) =>
    route.fulfill({ json: PREMIUM_CONTRACT.premium_heartbeat }),
  )
  await page.route("**/users/me/premium/beacon-token", (route) =>
    route.fulfill({ json: { token: "e2e" } }),
  )
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

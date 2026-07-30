import { execSync } from "child_process"
import * as fs from "fs"
import * as path from "path"

import { test, expect, Page, request } from "@playwright/test"

import {
  apiUrl,
  ensureTutorialRecords,
  login,
  mockPremiumAssignment,
  openWorkspace,
  reproduceTutorial,
} from "./helpers"

// Full subscription/storage warning lifecycle on the LOCAL stack only:
// free baseline → upgrade → over-quota warning modal (110%) → usage-high
// indicator (95%) → storage reload reset → expired premium (grace) →
// overdue → downgraded free over quota → run block/warn at quota →
// expiration captions → cancel-subscription dialog → cancelled banner →
// inactivity warning (fake clock + mocked premium assignment).
//
// Plan and expiry are driven directly in the docker DB (the same knobs the
// README documents for manual account bootstrap) because there is no Stripe
// locally. Storage usage, however, must be REAL: on every login the app
// recalculates usage from the workspace folders and overwrites the DB value
// (Layout's per-session refresh), so a faked usage number never survives to
// the warning check. Instead a sparse ballast file (zero disk cost; folder
// sizes sum st_size) sits in the user's workspace and each test dials
// storage_quota_bytes to put the measured real usage at the percentage
// under test.
// Each test writes its full DB scenario up front, so the serial group
// survives retries. Skips (never fails) when creds are missing, BASE_URL is
// not local, or the docker containers are unreachable.

const USER = {
  email: process.env.TEST_LIFECYCLE_EMAIL || "",
  password: process.env.TEST_LIFECYCLE_PASSWORD || "",
}

const GB = 1073741824
const FREE_QUOTA = 5 * GB
const PREMIUM_QUOTA = 200 * GB
// Well under the 5GiB free quota (even with sample data imported on top) so
// the ballast only "exceeds" when a test shrinks the quota; also under the
// hardcoded free limit that grace/overdue states compare against, keeping
// those popups subscription-only
const BALLAST = 3 * GB

// Measured in beforeAll via the same backend recalculation the app runs on
// login. Quotas are dialed from this rather than from BALLAST because the
// workspace accumulates other real data (sample import for LC-09/10), and
// the login-time refresh overwrites any faked usage with reality.
let realUsage = 0
const overQuota = () => Math.floor(realUsage / 1.1) // usage ≈ 110%
const nearQuota = () => Math.floor(realUsage / 0.95) // usage ≈ 95%

const REPO_ROOT = path.resolve(__dirname, "../..")
const COMPOSE = "docker compose -f docker-compose.dev.multiuser.yml"

function runSql(sql: string): string {
  return execSync(
    `${COMPOSE} exec -T db sh -c ` +
      `'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -N "$MYSQL_DATABASE"'`,
    { cwd: REPO_ROOT, input: sql, stdio: ["pipe", "pipe", "pipe"] },
  )
    .toString()
    .trim()
}

function runInBackend(cmd: string, input?: string) {
  execSync(`${COMPOSE} exec -T studio-dev-be ${cmd}`, {
    cwd: REPO_ROOT,
    stdio: ["pipe", "pipe", "pipe"],
    input,
  })
}

const userId = `(SELECT id FROM users WHERE email = '${USER.email.replace(/'/g, "''")}')`

// Primes the cached value so the warning check agrees with the ballast even
// before the login-time refresh has run (20-minute freshness window)
function setStorage(usageBytes: number, quotaBytes: number) {
  runSql(
    `UPDATE user_storage_usage SET storage_usage_bytes = ${usageBytes},
       storage_quota_bytes = ${quotaBytes}, last_updated = UTC_TIMESTAMP()
       WHERE user_id = ${userId};`,
  )
}

// expiresIn examples: "INTERVAL 1 MONTH", "INTERVAL -1 DAY"
function setPlan(planId: number, expiresIn: string, scheduledDowngrade = 0) {
  runSql(
    `UPDATE subscription_users SET plan_id = ${planId},
       expiration = DATE_ADD(UTC_TIMESTAMP(), ${expiresIn}),
       scheduled_downgrade = ${scheduledDowngrade}
       WHERE user_id = ${userId};`,
  )
}

let workspaceId = 0
const ballastPath = () =>
  `/app/studio_data/input/${workspaceId}/e2e-ballast.bin`

function ensureBallast() {
  runInBackend(
    `sh -c "mkdir -p /app/studio_data/input/${workspaceId} && ` +
      `dd if=/dev/zero of=${ballastPath()} bs=1 count=0 seek=${BALLAST} 2>/dev/null"`,
  )
}

function removeBallast() {
  runInBackend(`rm -f ${ballastPath()}`)
}

function verifyEmail(email: string) {
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

// Register + verify the dedicated lifecycle user if it doesn't exist yet,
// and find-or-create its ballast workspace. Idempotent: an "already
// registered" response falls through to re-verify and log in, so a
// half-created user from an aborted run self-heals.
async function ensureUserAndWorkspace() {
  const api = await request.newContext({ baseURL: apiUrl() })
  try {
    const creds = { email: USER.email, password: USER.password }
    let loginRes = await api.post("/auth/login", { data: creds })
    if (!loginRes.ok()) {
      await api.post("/api/register", {
        data: { name: "E2E Lifecycle", role_id: 20, ...creds },
      })
      verifyEmail(USER.email)
      loginRes = await api.post("/auth/login", { data: creds })
      if (!loginRes.ok()) {
        throw new Error(
          `Lifecycle user bootstrap failed (login ${loginRes.status()}): ` +
            `${await loginRes.text()}`,
        )
      }
    }
    const { access_token } = await loginRes.json()
    const headers = { Authorization: `Bearer ${access_token}` }
    const list = await api.get("/workspaces?offset=0&limit=100", { headers })
    const { items } = await list.json()
    const found = items.find(
      (w: { name: string }) => w.name === "e2e-lifecycle",
    )
    if (found) {
      workspaceId = found.id
    } else {
      const created = await api.post("/workspace", {
        headers,
        data: { name: "e2e-lifecycle" },
      })
      workspaceId = (await created.json()).id
    }

    ensureBallast()
    // Recalculate real usage (ballast + whatever else the workspace holds)
    // exactly the way the app does on login, then read the result back
    await api.post("/workspaces/refresh-storage", { headers })
    realUsage = Number(
      runSql(
        `SELECT storage_usage_bytes FROM user_storage_usage
           WHERE user_id = ${userId};`,
      ),
    )
    if (!realUsage) {
      throw new Error("storage measurement returned 0 — ballast missing?")
    }
  } finally {
    await api.dispose()
  }
}

// login() with dismissWarning=false so the warning modals under test are
// still open when the assertions run
async function loginKeepWarnings(page: Page) {
  await login(page, USER.email, USER.password, false)
}

const dialog = (page: Page) => page.locator('[role="dialog"]')

test.describe.serial("Subscription/storage warning lifecycle", () => {
  let skipReason = ""

  test.beforeAll(async () => {
    if (!USER.email || !USER.password) {
      skipReason = "TEST_LIFECYCLE_EMAIL/TEST_LIFECYCLE_PASSWORD not set"
      return
    }
    const base = process.env.BASE_URL || "http://localhost:3000"
    if (!/localhost|127\.0\.0\.1/.test(base)) {
      skipReason =
        "lifecycle spec mutates the local docker DB; BASE_URL is not local"
      return
    }
    try {
      runSql("SELECT 1;")
    } catch {
      skipReason = "local docker db container not reachable"
      return
    }
    // The local-testing default in studio/config/.env disables every storage
    // lookup backend-side (deployed envs run with it false), so no warning
    // under test can ever fire while it's on
    const backendEnv = path.join(REPO_ROOT, "studio", "config", ".env")
    if (
      fs.existsSync(backendEnv) &&
      /^SKIP_STORAGE_CHECKS=true/m.test(fs.readFileSync(backendEnv, "utf-8"))
    ) {
      skipReason =
        "SKIP_STORAGE_CHECKS=true in studio/config/.env disables storage warnings"
      return
    }
    await ensureUserAndWorkspace()
    // A purchase row is what marks the user as having once paid; without it
    // the backend reports an expired premium as a plain free user and the
    // expired-state UI (Expired-on caption, Manage button) never renders.
    // Any real formerly-premium user has one.
    runSql(
      `INSERT INTO subscription_user_purchases (plan_id, user_id)
         SELECT 2, ${userId} WHERE NOT EXISTS
         (SELECT 1 FROM subscription_user_purchases
            WHERE user_id = ${userId});`,
    )
  })

  test.beforeEach(() => {
    test.skip(!!skipReason, skipReason)
  })

  test.afterAll(() => {
    if (skipReason) return
    // Leave the user as a clean free account for the next run
    removeBallast()
    setPlan(1, "INTERVAL 0 DAY")
    setStorage(0, FREE_QUOTA)
  })

  test("LC-01 - Free baseline: no warning, account shows Free", async ({
    page,
  }) => {
    setPlan(1, "INTERVAL 0 DAY")
    setStorage(realUsage, FREE_QUOTA)
    await loginKeepWarnings(page)

    await expect(dialog(page)).toBeHidden()
    await page.goto("/account")
    await expect(page.locator("text=Free").first()).toBeVisible({
      timeout: 15_000,
    })
    await expect(page.locator('button:has-text("Upgrade")')).toBeVisible()
  })

  test("LC-02 - Upgrade to premium: account shows Premium, no warning", async ({
    page,
  }) => {
    setPlan(2, "INTERVAL 1 MONTH")
    setStorage(realUsage, PREMIUM_QUOTA)
    await loginKeepWarnings(page)

    await expect(dialog(page)).toBeHidden()
    await page.goto("/account")
    await expect(page.locator("text=Premium").first()).toBeVisible({
      timeout: 15_000,
    })
    await expect(page.locator('button:has-text("Manage")')).toBeVisible()
  })

  test("LC-03 - Premium at 110% quota: Storage Limit Exceeded modal on login", async ({
    page,
  }) => {
    ensureBallast()
    setPlan(2, "INTERVAL 1 MONTH")
    setStorage(realUsage, overQuota())
    await loginKeepWarnings(page)

    const modal = dialog(page)
    await expect(modal.getByText("Storage Limit Exceeded")).toBeVisible({
      timeout: 30_000,
    })
    await expect(
      modal.getByRole("button", { name: "Manage Files" }),
    ).toBeVisible()
    // Active premium can't upgrade its way out of an over-quota state
    await expect(modal.getByRole("button", { name: "Upgrade" })).toBeHidden()
    // Handle later dismisses and stays on the dashboard
    await modal.getByRole("button", { name: "Handle later" }).click()
    await expect(modal).toBeHidden()
    await expect(page).toHaveURL(/\/dashboard/)

    await page.goto("/workspaces")
    await expect(page.getByText("Storage usage is over quota")).toBeVisible({
      timeout: 30_000,
    })
  })

  test("LC-04 - Premium at 95% quota: no modal, usage-high indicator only", async ({
    page,
  }) => {
    ensureBallast()
    setPlan(2, "INTERVAL 1 MONTH")
    setStorage(realUsage, nearQuota())
    await loginKeepWarnings(page)

    await page.goto("/workspaces")
    await expect(page.getByText("Storage usage is high")).toBeVisible({
      timeout: 30_000,
    })
    await expect(dialog(page)).toBeHidden()
  })

  test("LC-05 - Storage reload picks up freed space and clears the warning", async ({
    page,
  }) => {
    ensureBallast()
    setPlan(2, "INTERVAL 1 MONTH")
    setStorage(realUsage, nearQuota())
    await loginKeepWarnings(page)

    await page.goto("/workspaces")
    await expect(page.getByText("Storage usage is high")).toBeVisible({
      timeout: 30_000,
    })
    // Free the space for real, then reload — the recalculation should clear
    // the warning state
    removeBallast()
    await page.locator('button:has-text("Reload")').click()
    await expect(page.locator("text=/Storage refreshed/i").first()).toBeVisible(
      { timeout: 60_000 },
    )
    await expect(page.getByText("Storage usage is high")).toBeHidden()
  })

  test("LC-06 - Expired premium (grace period): expiry warning on login", async ({
    page,
  }) => {
    setPlan(2, "INTERVAL -1 DAY")
    setStorage(0, PREMIUM_QUOTA)
    await loginKeepWarnings(page)

    const modal = dialog(page)
    await expect(
      modal.getByText("Premium Subscription Expired", { exact: true }),
    ).toBeVisible({ timeout: 30_000 })
    await expect(modal.getByRole("button", { name: "Upgrade" })).toBeVisible()
    await modal.getByRole("button", { name: "Handle later" }).click()
    await expect(modal).toBeHidden()

    // An expired premium account offers both recovery paths
    await page.goto("/account")
    await expect(page.locator("text=/\\(Expired on/").first()).toBeVisible({
      timeout: 15_000,
    })
    await expect(page.locator('button:has-text("Upgrade")')).toBeVisible()
    await expect(page.locator('button:has-text("Manage")')).toBeVisible()
  })

  test("LC-07 - Long-expired premium: overdue modal requires acknowledgment", async ({
    page,
  }) => {
    // Past expiry + 30-day grace + 30-day warning window → OVERDUE
    setPlan(2, "INTERVAL -70 DAY")
    setStorage(0, PREMIUM_QUOTA)
    await loginKeepWarnings(page)

    const modal = dialog(page)
    await expect(modal.getByText("Urgent: Data Deletion Imminent")).toBeVisible(
      { timeout: 30_000 },
    )
    await expect(modal.getByText("Data Cleanup Overdue")).toBeVisible()
    const remindLater = modal.getByRole("button", {
      name: "I understand, remind me later",
    })
    await expect(remindLater).toBeDisabled()
    await modal.getByRole("checkbox").check()
    await expect(remindLater).toBeEnabled()
    await remindLater.click()
    await expect(modal).toBeHidden()
  })

  test("LC-08 - Downgraded free user over quota: warning offers upgrade", async ({
    page,
  }) => {
    ensureBallast()
    setPlan(1, "INTERVAL 0 DAY")
    setStorage(realUsage, overQuota())
    await loginKeepWarnings(page)

    const modal = dialog(page)
    await expect(modal.getByText("Storage Limit Exceeded")).toBeVisible({
      timeout: 30_000,
    })
    // Unlike LC-03, a free user gets the upgrade path and a deletion window
    await expect(modal.getByRole("button", { name: "Upgrade" })).toBeVisible()
    await expect(modal.getByText("Time Remaining")).toBeVisible()
    // Manage Files dismisses and lands on the workspace list
    await modal.getByRole("button", { name: "Manage Files" }).click()
    await expect(modal).toBeHidden()
    await expect(page).toHaveURL(/\/workspaces/, { timeout: 15_000 })
  })

  // LC-09/10 need a runnable workflow: sample data is imported into the
  // lifecycle workspace on first need (records persist across runs), then
  // Tutorial1 is reproduced. Quota is only shrunk AFTER the import — uploads
  // are themselves rejected over quota.
  async function loadRunnableWorkflow(page: Page) {
    await openWorkspace(page, "e2e-lifecycle")
    await ensureTutorialRecords(page, "e2e-lifecycle")
    await reproduceTutorial(page, "Tutorial1")
  }

  const runAllButton = (page: Page) =>
    page.locator('button:has-text("RUN ALL"), button:has-text("RUN")').first()
  const runNameDialog = (page: Page) =>
    page.locator('[role="dialog"]:has-text("Name and run workflow")')

  // After a reproduce the split button defaults to "RUN" (rerun by uid),
  // which starts immediately with no name dialog — switch it to "RUN ALL"
  // so the not-blocked case stops at the dialog instead of really running
  async function selectRunAllMode(page: Page) {
    await page.locator('button:has([data-testid="ArrowDropDownIcon"])').click()
    await page.locator('li:has-text("RUN ALL")').click()
    await expect(page.locator('button:has-text("RUN ALL")')).toBeVisible()
  }

  test("LC-09 - RUN over quota is blocked before the run dialog", async ({
    page,
  }) => {
    test.setTimeout(300_000)
    ensureBallast()
    setPlan(2, "INTERVAL 1 MONTH")
    setStorage(realUsage, PREMIUM_QUOTA)
    await loginKeepWarnings(page)
    await loadRunnableWorkflow(page)
    await selectRunAllMode(page)

    setStorage(realUsage, overQuota())
    await runAllButton(page).click()
    await expect(
      page.locator("text=Cannot run job: Storage quota exceeded").first(),
    ).toBeVisible({ timeout: 15_000 })
    await expect(runNameDialog(page)).toBeHidden()
  })

  test("LC-10 - RUN at 95% quota warns but is not blocked", async ({
    page,
  }) => {
    test.setTimeout(300_000)
    ensureBallast()
    setPlan(2, "INTERVAL 1 MONTH")
    setStorage(realUsage, PREMIUM_QUOTA)
    await loginKeepWarnings(page)
    await loadRunnableWorkflow(page)
    await selectRunAllMode(page)

    setStorage(realUsage, nearQuota())
    await runAllButton(page).click()
    await expect(
      page.locator("text=Storage usage is high").first(),
    ).toBeVisible({ timeout: 15_000 })
    // The run-name dialog opening proves the run was not blocked; cancel it
    // so no real workflow executes
    await expect(runNameDialog(page)).toBeVisible({ timeout: 15_000 })
    await runNameDialog(page).getByRole("button", { name: "Cancel" }).click()
    await expect(runNameDialog(page)).toBeHidden()
  })

  test("LC-11 - Expiration caption matches subscription state", async ({
    page,
  }) => {
    setStorage(0, PREMIUM_QUOTA)
    setPlan(2, "INTERVAL 1 MONTH")
    await loginKeepWarnings(page)

    await page.goto("/account")
    await expect(page.locator("text=/\\(Renew on/").first()).toBeVisible({
      timeout: 15_000,
    })

    setPlan(2, "INTERVAL 1 MONTH", 1)
    await page.goto("/account")
    await expect(page.locator("text=/\\(Expires on/").first()).toBeVisible({
      timeout: 15_000,
    })

    setPlan(2, "INTERVAL -1 DAY")
    await page.goto("/account")
    await expect(page.locator("text=/\\(Expired on/").first()).toBeVisible({
      timeout: 15_000,
    })
  })

  test("LC-12 - Downgrade opens a confirmation with retention notice; No aborts", async ({
    page,
  }) => {
    setStorage(0, PREMIUM_QUOTA)
    setPlan(2, "INTERVAL 1 MONTH")
    await loginKeepWarnings(page)

    await page.goto("/subscription")
    await page.locator('button:has-text("Downgrade")').click()

    const confirm = page.locator(
      '[role="dialog"]:has-text("Cancel Subscription")',
    )
    await expect(confirm).toBeVisible({ timeout: 15_000 })
    await expect(confirm.getByText("Data Storage Notice:")).toBeVisible()
    await expect(confirm.getByText(/stored for 30 days/)).toBeVisible()
    // The Stripe-backed "Yes, Cancel Subscription" path stays manual
    await confirm.getByRole("button", { name: "No" }).click()
    await expect(confirm).toBeHidden()
    await expect(
      page.locator('button:has-text("Current Plan")').first(),
    ).toBeVisible()
  })

  test("LC-13 - Cancelled subscription shows banner and Continue Plan", async ({
    page,
  }) => {
    setStorage(0, PREMIUM_QUOTA)
    setPlan(2, "INTERVAL 1 MONTH", 1)
    await loginKeepWarnings(page)

    await page.goto("/subscription")
    await expect(
      page.locator("text=Subscription Canceled:").first(),
    ).toBeVisible({ timeout: 15_000 })
    // Clicking it (reactivation) hits Stripe — display check only
    await expect(page.locator('button:has-text("Continue Plan")')).toBeVisible()
  })

  // The inactivity monitor only arms while the frontend holds a premium
  // assignment, which never succeeds locally (no ECS) — mock the premium
  // endpoints so the frontend believes it is assigned, then drive the
  // timers with the fake clock. This automates the manual "Triggering the
  // Inactivity Warning via DevTools" Date.now() override from the System
  // test sheet's Reference tab.
  // Log in with the fake clock installed and wait until the (mocked)
  // assignment has been processed, so the inactivity interval is armed
  // before the clock jumps
  async function loginWithArmedInactivityMonitor(page: Page) {
    await page.clock.install()
    const statusSeen = page.waitForResponse((r) =>
      r.url().includes("/users/me/premium/status"),
    )
    await loginKeepWarnings(page)
    await statusSeen
    await page.waitForTimeout(1_000)
  }

  test("LC-14 - Inactivity warning after 1h; Stay Active resets it", async ({
    page,
  }) => {
    setStorage(0, PREMIUM_QUOTA)
    setPlan(2, "INTERVAL 1 MONTH")

    // 65 min is past the 1h warning threshold but below the 2h auto-release
    await mockPremiumAssignment(page)
    await loginWithArmedInactivityMonitor(page)

    await page.clock.fastForward(65 * 60 * 1000)
    const warning = page.locator("text=Premium Instance Inactivity Warning")
    await expect(warning).toBeVisible({ timeout: 15_000 })

    await page.getByRole("button", { name: "Stay Active" }).click()
    await expect(warning).toBeHidden({ timeout: 15_000 })

    // Timer reset: another 30 fake minutes stay quiet (next warning is a
    // full hour from the Stay Active click)
    await page.clock.fastForward(30 * 60 * 1000)
    await expect(warning).toBeHidden()
  })

  test("LC-15 - 2h inactivity auto-releases the instance via beacon", async ({
    page,
  }) => {
    setStorage(0, PREMIUM_QUOTA)
    setPlan(2, "INTERVAL 1 MONTH")

    await mockPremiumAssignment(page)
    // The release path is a sendBeacon POST — the same plumbing the
    // browser-close release uses. Mock it and watch for the call.
    await page.route("**/users/me/premium/release-beacon", (route) =>
      route.fulfill({ json: { released: true } }),
    )
    await loginWithArmedInactivityMonitor(page)

    const beaconFired = page.waitForRequest(
      (r) => r.url().includes("/users/me/premium/release-beacon"),
      { timeout: 15_000 },
    )
    // Past the 2h threshold in one jump: the check must release, not warn
    await page.clock.fastForward(125 * 60 * 1000)
    await beaconFired
    await expect(
      page.locator("text=Premium Instance Inactivity Warning"),
    ).toBeHidden()
  })

  test("LC-16 - Account deletion deactivates the user", async ({ page }) => {
    // A per-run throwaway account: the flow destroys it, and a timestamped
    // address avoids collisions with leftovers from crashed runs
    const email = `e2e_local_delete_${Date.now()}@test.com`
    const api = await request.newContext({ baseURL: apiUrl() })
    try {
      const reg = await api.post("/api/register", {
        data: {
          name: "E2E Delete Me",
          role_id: 20,
          email,
          password: USER.password,
        },
      })
      expect(reg.ok()).toBeTruthy()
    } finally {
      await api.dispose()
    }
    verifyEmail(email)

    await login(page, email, USER.password, false)
    await page.goto("/account")
    await page.locator('button:has-text("Delete Account")').click()

    const confirm = dialog(page)
    await expect(confirm.getByText("This action cannot be undone")).toBeVisible(
      { timeout: 15_000 },
    )
    await confirm.locator('input[placeholder="DELETE"]').fill("DELETE")
    await confirm.getByRole("button", { name: "Delete My Account" }).click()

    // The account is deactivated and a deletion record is written; the
    // step pipeline completes asynchronously
    await expect
      .poll(
        () => runSql(`SELECT active FROM users WHERE email = '${email}';`),
        { timeout: 60_000 },
      )
      .toBe("0")
    await expect
      .poll(
        () =>
          runSql(
            `SELECT COUNT(*) FROM user_deletion_records
               WHERE user_id = (SELECT id FROM users WHERE email = '${email}')
               AND status = 'completed';`,
          ),
        { timeout: 60_000 },
      )
      .not.toBe("0")
  })

  test("LC-17 - Release clears every routing key; a gesture re-seeds them", async ({
    page,
  }) => {
    setStorage(0, PREMIUM_QUOTA)
    setPlan(2, "INTERVAL 1 MONTH")

    await mockPremiumAssignment(page)
    await page.route("**/users/me/premium/release-beacon", (route) =>
      route.fulfill({ json: { released: true } }),
    )
    await loginWithArmedInactivityMonitor(page)

    const routingKeys = () =>
      page.evaluate(() => ({
        routingId: localStorage.getItem("routing_id"),
        assigned: localStorage.getItem("premium_assigned"),
        instanceId: localStorage.getItem("premium_instance_id"),
        shared: localStorage.getItem("premium_shared"),
      }))

    await expect.poll(async () => (await routingKeys()).assigned).toBe("true")

    const beaconFired = page.waitForRequest(
      (r) => r.url().includes("/users/me/premium/release-beacon"),
      { timeout: 15_000 },
    )
    await page.clock.fastForward(125 * 60 * 1000)
    await beaconFired

    // premium_assigned=true with no routing_id is the unrecoverable pair:
    // headers are withheld while the app still believes it is routed.
    await expect.poll(routingKeys).toEqual({
      routingId: null,
      assigned: "false",
      instanceId: null,
      shared: "false",
    })

    // A gesture after the release re-runs auto-assign against the same mocks.
    await page.mouse.click(5, 5)
    await expect.poll(async () => (await routingKeys()).assigned).toBe("true")
  })
})

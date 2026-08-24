import * as fs from "fs"
import * as path from "path"

import { test, expect, Page, request } from "@playwright/test"

import {
  apiUrl,
  authHeaders,
  confirmDialog,
  ensureTutorialRecords,
  ERROR_RED,
  isLocalBaseUrl,
  localStackSkipReason,
  login,
  logout,
  mockPremiumAssignment,
  openWorkspace,
  REPO_ROOT,
  reproduceTutorial,
  runInBackend,
  runSql,
  sqlLiteral,
  verifyEmail,
} from "./helpers"

// Full subscription/storage warning lifecycle on the LOCAL stack only. The test
// names are the index; each writes its own DB scenario up front, so the serial
// group survives retries.
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
// On a local run every reason this group cannot execute is a broken
// environment, and it FAILS rather than skipping: several LC rows have no
// coverage but this spec, and a skipped row reads as a pass on the sheet. It
// still skips where BASE_URL is not local, because the DB writes it needs are
// only reachable on the docker stack.

const USER = {
  email: process.env.TEST_LIFECYCLE_EMAIL || "",
  password: process.env.TEST_LIFECYCLE_PASSWORD || "",
}

const GB = 1073741824
const FREE_QUOTA = 5 * GB
const PREMIUM_QUOTA = 200 * GB
// An expired premium account is held to the free-tier limit whatever its quota
// record says (`_effective_quota_bytes`), so the combined grace + over-quota
// state needs real usage above that limit rather than a dialed quota
const OVER_FREE_QUOTA = 6 * GB
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

const userId = `(SELECT id FROM users WHERE email = '${sqlLiteral(USER.email)}')`

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

// Always (re)sizes rather than only creating, so a test that grew the ballast
// cannot leak that size into the next one
function ensureBallast(bytes = BALLAST) {
  runInBackend(
    `sh -c "mkdir -p /app/studio_data/input/${workspaceId} && ` +
      `rm -f ${ballastPath()} && ` +
      `dd if=/dev/zero of=${ballastPath()} bs=1 count=0 seek=${bytes} 2>/dev/null"`,
  )
}

function removeBallast() {
  runInBackend(`rm -f ${ballastPath()}`)
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
    const { access_token, ex_token } = await loginRes.json()
    const headers = authHeaders(access_token, ex_token)
    const list = await api.get("/workspaces?offset=0&limit=100", { headers })
    if (!list.ok()) {
      throw new Error(`GET /workspaces ${list.status()}: ${await list.text()}`)
    }
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

// Same locator the admin spec uses
const dialog = confirmDialog

// Sign-off rows this group alone covers, named in the reason so a run that does
// not execute it says which rows it left unverified
const UNCOVERED_ELSEWHERE = "LC-01..LC-23"

test.describe.serial("Subscription/storage warning lifecycle", () => {
  let skipReason = ""

  function unrunnable(reason: string) {
    if (isLocalBaseUrl()) {
      throw new Error(`${reason}; ${UNCOVERED_ELSEWHERE} cannot run`)
    }
    skipReason = `${reason}; leaves ${UNCOVERED_ELSEWHERE} unverified`
  }

  test.beforeAll(async () => {
    if (!USER.email || !USER.password) {
      unrunnable("TEST_LIFECYCLE_EMAIL/TEST_LIFECYCLE_PASSWORD not set")
      return
    }
    const localStack = localStackSkipReason()
    if (localStack) {
      unrunnable(localStack)
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
      unrunnable(
        "SKIP_STORAGE_CHECKS=true in studio/config/.env disables storage warnings",
      )
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

  // A test that grew the ballast must not leave it grown: the percentages every
  // later test dials are derived from a measurement taken at the default size,
  // and an inline restore does not run when the test fails
  test.afterEach(() => {
    if (!skipReason) ensureBallast()
  })

  test.afterAll(() => {
    if (skipReason) return
    // Leave the user as a clean free account for the next run, without the
    // fake instance rows LC-24/25 seeded
    removeBallast()
    setPlan(1, "INTERVAL 0 DAY")
    setStorage(0, FREE_QUOTA)
    runSql(
      `DELETE FROM free_user_assignments WHERE user_id = ${userId};
       DELETE FROM instance_usage_log WHERE user_id = ${userId};`,
    )
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

  // Expired premium still inside the grace window AND over the free limit: the
  // one combined state neither the grace-only nor the free-over-quota case
  // reaches. The ballast has to grow for real, because the effective quota is
  // the hardcoded free limit rather than the quota column - no amount of dialing
  // storage_quota_bytes puts a grace user over it.
  async function loginExpiredPremiumOverQuota(page: Page) {
    // A real login, whose retry ladder is up to 45s, on top of the DB writes and
    // a ballast resize: the default 60s budget can expire during setup
    test.setTimeout(120_000)
    ensureBallast(OVER_FREE_QUOTA)
    setPlan(2, "INTERVAL -1 DAY")
    setStorage(OVER_FREE_QUOTA, PREMIUM_QUOTA)
    await loginKeepWarnings(page)
  }

  test("LC-18 - Expired premium in grace and over quota: combined warning", async ({
    page,
  }) => {
    // Not the sibling `/limit-warning/check`, which returns a different shape
    const warningSeen = page.waitForResponse((r) =>
      r.url().endsWith("/storage-limit-alerts/limit-warning"),
    )
    await loginExpiredPremiumOverQuota(page)

    // The payload carries the whole deletion timeline; the modal renders only
    // the expiry date and the time remaining, so assert the dates where they
    // actually exist.
    const warning = await (await warningSeen).json()
    expect(warning.alert_type).toBe("grace")
    expect(warning.subscription_end_date).toBeTruthy()
    expect(warning.grace_end_date).toBeTruthy()
    expect(warning.deletion_date).toBeTruthy()
    // The quota that bites is the free one, not the premium 200GB on the record
    expect(warning.storage_quota_gb).toBe(FREE_QUOTA / GB)
    expect(warning.excess_data_gb).toBeGreaterThan(0)

    const modal = dialog(page)
    // The subscription-expiry variant, not the plain storage one
    await expect(
      modal.getByText("Premium Subscription Expired", { exact: true }),
    ).toBeVisible({ timeout: 30_000 })
    await expect(modal.getByText(/exceeds the free plan limit/)).toBeVisible()
    await expect(modal.getByText("Time Remaining")).toBeVisible()
    // Both recovery paths: pay again, or delete data
    await expect(modal.getByRole("button", { name: "Upgrade" })).toBeVisible()
    await expect(
      modal.getByRole("button", { name: "Manage Files" }),
    ).toBeVisible()
  })

  test("LC-19 - Handle later on the expiry warning returns to the dashboard", async ({
    page,
  }) => {
    await loginExpiredPremiumOverQuota(page)

    const modal = dialog(page)
    await expect(
      modal.getByText("Premium Subscription Expired", { exact: true }),
    ).toBeVisible({ timeout: 30_000 })
    await modal.getByRole("button", { name: "Handle later" }).click()
    await expect(modal).toBeHidden()
    await expect(page).toHaveURL(/\/dashboard/)
  })

  test("LC-20 - Upgrade on the expiry warning lands on /subscription", async ({
    page,
  }) => {
    await loginExpiredPremiumOverQuota(page)

    const modal = dialog(page)
    await expect(
      modal.getByText("Premium Subscription Expired", { exact: true }),
    ).toBeVisible({ timeout: 30_000 })
    await modal.getByRole("button", { name: "Upgrade" }).click()
    await expect(modal).toBeHidden()
    await expect(page).toHaveURL(/\/subscription$/, { timeout: 15_000 })
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
    // "Storage usage is high" on its own is also the storage panel's caption;
    // this is the run-time snackbar, percentage and advice included
    await expect(
      page
        .locator(
          "text=/^Warning: Storage usage is high \\(\\d+\\.\\d% used\\)\\. Consider freeing up space\\.$/",
        )
        .first(),
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
    await expect(
      confirm.locator(".MuiDialogTitle-root"),
      "the dialog names the destructive action, not just the page",
    ).toHaveText("Cancel Subscription")
    await expect(confirm.getByText("Data Storage Notice:")).toBeVisible()
    await expect(confirm.getByText(/stored for 30 days/)).toBeVisible()
    // The Stripe-backed "Yes, Cancel Subscription" path stays manual
    await confirm.getByRole("button", { name: "No" }).click()
    await expect(confirm).toBeHidden()
    await expect(
      page.locator('button:has-text("Current Plan")').first(),
    ).toBeVisible()
  })

  test("LC-21 - Profile after cancellation still reads Premium", async ({
    page,
  }) => {
    setStorage(0, PREMIUM_QUOTA)
    setPlan(2, "INTERVAL 1 MONTH", 1)
    await loginKeepWarnings(page)

    await page.goto("/account")
    // The status field itself, not the word anywhere on the page
    await expect(page.locator('[data-testid="account-plan-name"]')).toHaveText(
      "Premium",
      { timeout: 15_000 },
    )
    // Cancelled but not yet lapsed: the caption says Expires, not Expired, names
    // the stored expiration, and the only action is Manage - there is nothing to
    // upgrade to
    const expires = runSql(
      `SELECT DATE_FORMAT(expiration, '%c/%e/%Y') FROM subscription_users
         WHERE user_id = ${userId};`,
    )
    await expect(page.locator(`text=(Expires on ${expires})`)).toBeVisible()
    await expect(page.locator('button:has-text("Manage")')).toBeVisible()
    await expect(page.locator('button:has-text("Upgrade")')).toBeHidden()
  })

  test("LC-22 - After renewal the plan stays Premium with a later expiry", async ({
    page,
  }) => {
    setStorage(0, PREMIUM_QUOTA)
    setPlan(2, "INTERVAL 2 DAY")
    await loginKeepWarnings(page)

    // The renewal writes a new period end and nothing else
    setPlan(2, "INTERVAL 1 MONTH")
    await page.goto("/account")
    await expect(page.locator("text=Premium").first()).toBeVisible({
      timeout: 15_000,
    })
    // The caption has to name the stored expiration, not just some later date
    // The caption renders the stored UTC date in the browser's short locale
    // format, and the run pins that timezone to UTC
    const renewedOn = runSql(
      `SELECT DATE_FORMAT(expiration, '%c/%e/%Y') FROM subscription_users
         WHERE user_id = ${userId};`,
    )
    await expect(
      page.locator(`text=/\\(Renew on\\s+${renewedOn}/`).first(),
    ).toBeVisible()

    await page.goto("/subscription")
    await expect(
      page.locator('button:has-text("Current Plan")').first(),
    ).toBeVisible({ timeout: 15_000 })
    await expect(page.locator("text=Subscription Canceled:")).toBeHidden()
  })

  test("LC-23 - Past the grace the account reads Expired, not Free", async ({
    page,
  }) => {
    // Past expiry plus the 30-day grace. No row is downgraded; the tier is
    // derived from the expiration.
    setStorage(0, PREMIUM_QUOTA)
    setPlan(2, "INTERVAL -40 DAY")
    const meSeen = page.waitForResponse(
      (r) => r.url().endsWith("/users/me") && r.request().method() === "GET",
    )
    await loginKeepWarnings(page)

    const me = await (await meSeen).json()
    expect(me.subscription_status).toBe("Expired")
    // The row itself is untouched; only the derived status changed
    expect(
      runSql(
        `SELECT plan_id FROM subscription_users WHERE user_id = ${userId};`,
      ),
    ).toBe("2")

    // The expiry modal carries its own Upgrade button and re-renders on this
    // route, so dismiss the one on the page under test, not the dashboard's
    await page.goto("/account")
    const modal = dialog(page)
    await expect(
      modal.getByText("Premium Subscription Expired", { exact: true }),
    ).toBeVisible({ timeout: 30_000 })
    await modal.getByRole("button", { name: "Handle later" }).click()
    await expect(modal).toBeHidden()

    await expect(page.locator("text=/\\(Expired on/").first()).toBeVisible({
      timeout: 15_000,
    })
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
    const deleteAccount = page.locator('button:has-text("Delete Account")')
    await expect(deleteAccount).toBeEnabled()
    await expect(
      deleteAccount,
      "the deletion option is styled as the destructive one",
    ).toHaveCSS("background-color", ERROR_RED)
    await deleteAccount.click()

    const confirm = dialog(page)
    // The free-tier warning, exactly: the two subscription lines belong to the
    // premium copy and must not appear for an account that has no subscription
    // to lose.
    await expect(confirm.locator("li")).toHaveText(
      [
        "All your data (workspaces, experiments, files) will be permanently deleted",
        "This action cannot be undone",
      ],
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

    // Release boundary: 2h of inactivity fires the release beacon.
    const beaconFired = page.waitForRequest(
      (r) => r.url().includes("/users/me/premium/release-beacon"),
      { timeout: 15_000 },
    )
    await page.clock.fastForward(125 * 60 * 1000)
    await beaconFired

    // Every routing key is cleared so premium_assigned never outlives the
    // token (the unrecoverable pair).
    await expect.poll(routingKeys).toEqual({
      routingId: null,
      assigned: "false",
      instanceId: null,
      shared: "false",
    })

    // Reassign boundary: a gesture re-runs auto-assign.
    await page.mouse.click(5, 5)
    await expect.poll(async () => (await routingKeys()).assigned).toBe("true")

    // Logout boundary: teardown clears the token and drops the assigned flag.
    await logout(page)
    await expect.poll(async () => (await routingKeys()).routingId).toBe(null)
    expect((await routingKeys()).assigned).not.toBe("true")
  })

  // The local stack never creates these rows on its own (the activity
  // middleware refuses instance_id "local"), so the assignment a deployed
  // login would have made is seeded directly; the endpoints under test only
  // ever UPDATE them.
  function seedFreeAssignment(loggedOut: boolean) {
    const stamp = loggedOut ? "UTC_TIMESTAMP()" : "NULL"
    runSql(
      `DELETE FROM free_user_assignments WHERE user_id = ${userId};
       DELETE FROM instance_usage_log WHERE user_id = ${userId};
       INSERT INTO free_user_assignments
         (user_id, instance_id, assigned_at, last_activity, logged_out_at)
         VALUES (${userId}, 'i-e2e', UTC_TIMESTAMP(), UTC_TIMESTAMP(), ${stamp});
       INSERT INTO instance_usage_log
         (user_id, instance_id, tier, started_at, ended_at)
         VALUES (${userId}, 'i-e2e', 'free', UTC_TIMESTAMP(), ${stamp});`,
    )
  }

  test("LC-24 - Free logout stamps the assignment and closes the usage log", async ({
    page,
  }) => {
    setPlan(1, "INTERVAL 1 MONTH")
    seedFreeAssignment(false)
    await login(page, USER.email, USER.password)

    const freeLogout = page.waitForResponse(
      (r) =>
        r.url().includes("/users/me/free/logout") &&
        r.request().method() === "POST",
      { timeout: 30_000 },
    )
    await logout(page)
    const response = await freeLogout
    expect(response.status()).toBe(200)
    expect((await response.json()).logged_out).toBe(true)

    // logout() asserted the redirect; the session tokens must be gone too
    expect(
      await page.evaluate(() => localStorage.getItem("access_token")),
    ).toBeNull()

    expect(
      runSql(
        `SELECT logged_out_at IS NOT NULL FROM free_user_assignments
           WHERE user_id = ${userId};`,
      ),
    ).toBe("1")
    expect(
      runSql(
        `SELECT ended_at IS NOT NULL FROM instance_usage_log
           WHERE user_id = ${userId} AND tier = 'free'
           ORDER BY id DESC LIMIT 1;`,
      ),
    ).toBe("1")
  })

  test("LC-25 - Re-login during the grace period clears logged_out_at", async ({
    page,
  }) => {
    setPlan(1, "INTERVAL 1 MONTH")
    seedFreeAssignment(true)
    await login(page, USER.email, USER.password)

    // The auth path cleared the stamp, so the cleanup job's
    // "logged_out_at IS NOT NULL" filter no longer selects this user
    expect(
      runSql(
        `SELECT COUNT(*) FROM free_user_assignments
           WHERE user_id = ${userId} AND logged_out_at IS NOT NULL;`,
      ),
    ).toBe("0")
    // Cleared, not deleted: the assignment row itself survives
    expect(
      runSql(
        `SELECT COUNT(*) FROM free_user_assignments WHERE user_id = ${userId};`,
      ),
    ).toBe("1")
  })

  // The cleanup job's selection, asked of the real code against the real
  // database. Picking the wrong user here deletes a live user's data, so the
  // boundary is the safety-critical part; the deletion it then performs is
  // covered by test_cleanup_job.py and is deliberately not run against this
  // account's own files.
  // `userId` above is a SQL subquery, not a value, so the numeric id has to be
  // read out separately to compare against what the job returns.
  function numericUserId(): number {
    const id = runSql(
      `SELECT id FROM users WHERE email = '${sqlLiteral(USER.email)}';`,
    ).trim()
    expect(id, `no local row for ${USER.email}`).toMatch(/^\d+$/)
    return Number(id)
  }

  function eligibleUserIds(): number[] {
    const out = runInBackend(
      "poetry run python -",
      "from studio.app.common.core.background.cleanup_job import DataCleanupJob\n" +
        "print([u[0] for u in DataCleanupJob._get_users_for_cleanup()])\n",
    )
    const list = out.slice(out.lastIndexOf("["))
    return JSON.parse(list) as number[]
  }

  function stampLogout(minutesAgo: number, activeWorkflows = 0) {
    runSql(
      `UPDATE free_user_assignments
         SET logged_out_at = UTC_TIMESTAMP() - INTERVAL ${minutesAgo} MINUTE,
             active_workflow_count = ${activeWorkflows},
             last_workflow_start = UTC_TIMESTAMP() - INTERVAL 45 MINUTE
         WHERE user_id = ${userId};`,
    )
  }

  test("LC-26 - Cleanup selects a logged-out free user only past the grace period", () => {
    // Each eligibleUserIds() boots the studio app in the container.
    test.setTimeout(240_000)
    setPlan(1, "INTERVAL 1 MONTH")
    seedFreeAssignment(true)

    // The grace period is 60 minutes, so half an hour out is still protected.
    stampLogout(30)
    const inGrace = eligibleUserIds()
    // The job limits to MAX_USERS_PER_RUN with no ORDER BY, so at the cap an
    // absence could be truncation rather than the grace period.
    expect(inGrace.length, "eligible set is at the job's cap").toBeLessThan(50)
    expect(
      inGrace,
      "a user inside the grace period must not be selected",
    ).not.toContain(numericUserId())

    stampLogout(61)
    expect(
      eligibleUserIds(),
      "a user past the grace period must be selected",
    ).toContain(numericUserId())
  })

  test("LC-27 - A workflow still marked active blocks cleanup until it is recovered", () => {
    // Each eligibleUserIds() boots the studio app in the container.
    test.setTimeout(240_000)
    setPlan(1, "INTERVAL 1 MONTH")
    seedFreeAssignment(true)

    // Past the grace period, but the count says a workflow is running. Deleting
    // now would pull data out from under it, so the job must pass the user over
    // however long ago they logged out.
    stampLogout(61, 1)
    expect(
      eligibleUserIds(),
      "a user with an active workflow must never be selected",
    ).not.toContain(numericUserId())

    // The count is stale, not real: last_workflow_start is 45 minutes old and
    // the threshold is 30, which is what a crashed run leaves behind. Recovery
    // resets it and the user becomes collectable again.
    const recovered = runInBackend(
      "poetry run python -",
      "from studio.app.common.core.workflow.workflow_count_recovery import " +
        "recover_stale_workflow_counts\n" +
        "print(recover_stale_workflow_counts()[0])\n",
    )
    expect(
      Number(recovered.trim().split("\n").pop()),
      "users recovered",
    ).toBeGreaterThan(0)
    expect(
      runSql(
        `SELECT active_workflow_count FROM free_user_assignments
           WHERE user_id = ${userId};`,
      ),
    ).toBe("0")
    expect(
      eligibleUserIds(),
      "after recovery the user must be selectable again",
    ).toContain(numericUserId())
  })
})

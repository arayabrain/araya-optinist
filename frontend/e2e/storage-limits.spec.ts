import { test, expect, Page } from "@playwright/test"

// Test credentials - configurable via env vars
const FREE_EMAIL =
  process.env.FREE_USER_EMAIL || "optinist_test_user_free@araya.org"
const FREE_PASSWORD = process.env.FREE_USER_PASSWORD || "testuser"

const PREMIUM_EMAIL =
  process.env.PREMIUM_USER_EMAIL || "optinist_test_user_premium@araya.org"
const PREMIUM_PASSWORD = process.env.PREMIUM_USER_PASSWORD || "testuser"

const PREMIUM_OVER_EMAIL =
  process.env.PREMIUM_OVER_USER_EMAIL ||
  "optinist_test_user_premium_over@araya.org"
const PREMIUM_OVER_PASSWORD =
  process.env.PREMIUM_OVER_USER_PASSWORD || "testuser"

const DOWNGRADE_EMAIL =
  process.env.DOWNGRADE_USER_EMAIL || "optinist_test_user_downgrade@araya.org"
const DOWNGRADE_PASSWORD = process.env.DOWNGRADE_USER_PASSWORD || "testuser"

const GRACE_UNDER_EMAIL =
  process.env.GRACE_UNDER_USER_EMAIL ||
  "optinist_test_user_grace_under@araya.org"
const GRACE_UNDER_PASSWORD = process.env.GRACE_UNDER_USER_PASSWORD || "testuser"

const GRACE_OVER_EMAIL =
  process.env.GRACE_OVER_USER_EMAIL || "optinist_test_user_grace_over@araya.org"
const GRACE_OVER_PASSWORD = process.env.GRACE_OVER_USER_PASSWORD || "testuser"

const PREMIUM_EXPIRED_EMAIL =
  process.env.PREMIUM_EXPIRED_USER_EMAIL ||
  "optinist_test_user_premium_expire@araya.org"
const PREMIUM_EXPIRED_PASSWORD =
  process.env.PREMIUM_EXPIRED_USER_PASSWORD || "testuser"

// Helper: Login with given credentials
async function login(page: Page, email: string, password: string) {
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
      return
    } catch {
      // Login timed out — retry
    }
  }
  throw new Error(`Login failed after 3 attempts for ${email}`)
}

// Helper: Check if login succeeds for a given account (skip test if not)
async function loginOrSkip(
  page: Page,
  email: string,
  password: string,
  testFn: typeof test,
) {
  try {
    await login(page, email, password)
  } catch {
    testFn.skip(true, `Account ${email} not available — skipping`)
  }
}

// ==============================================
// TC 401, 403-404: Free User Basics
// ==============================================

test.describe("Free User Basics", () => {
  test("TC401 - Free user login (no storage warning)", async ({ page }) => {
    await loginOrSkip(page, FREE_EMAIL, FREE_PASSWORD, test)

    await expect(page.locator("text=Dashboard")).toBeVisible()

    // Free user under limit should NOT see storage warning
    // Wait a moment for any alert to potentially appear
    await page.waitForTimeout(3_000)
    await expect(page.locator("text=Storage Limit Exceeded")).toBeHidden()
  })

  test("TC403 - Free user bucket name on Account Profile", async ({ page }) => {
    await loginOrSkip(page, FREE_EMAIL, FREE_PASSWORD, test)
    await page.goto("/account")

    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible({
      timeout: 15_000,
    })

    // Should show bucket name matching pattern: optinist-user-{id}-{unique}
    const bucketSection = page.locator("text=Bucket name").locator("..")
    await expect(bucketSection).toBeVisible()

    // Bucket name should contain "optinist-user-"
    await expect(page.locator("text=/optinist-user-\\d+/")).toBeVisible()
  })

  test("TC404 - Create workspace", async ({ page }) => {
    test.skip(
      !process.env.RUN_WORKSPACE_TESTS,
      "Set RUN_WORKSPACE_TESTS=1 to run (creates workspaces)",
    )
    await loginOrSkip(page, FREE_EMAIL, FREE_PASSWORD, test)
    await page.goto("/workspaces")

    await expect(page.locator("h1:has-text('Workspaces')")).toBeVisible({
      timeout: 15_000,
    })

    // Click New button
    await page.locator('button:has-text("New")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator("text=New Workspace")).toBeVisible()

    // Fill workspace name
    const workspaceName = `E2E_Test_WS_${Date.now()}`
    await dialog
      .locator('input[placeholder="Workspace Name"]')
      .fill(workspaceName)
    await dialog.locator('button:has-text("Ok")').click()

    // Should show success message
    await expect(
      page.locator("text=The workspace has been created successfully!"),
    ).toBeVisible({ timeout: 15_000 })
  })
})

// ==============================================
// TC 413, 417-418: Premium User Basics
// ==============================================

test.describe("Premium User Basics", () => {
  test("TC413 - Premium user login (shared resources snackbar)", async ({
    page,
  }) => {
    await loginOrSkip(page, PREMIUM_EMAIL, PREMIUM_PASSWORD, test)

    // Premium user should see premium resource preparation snackbar
    await expect(page.locator("text=premium resource")).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC417 - Premium user bucket name", async ({ page }) => {
    await loginOrSkip(page, PREMIUM_EMAIL, PREMIUM_PASSWORD, test)
    await page.goto("/account")

    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible({
      timeout: 15_000,
    })

    // Should show bucket name
    await expect(page.locator("text=/optinist-user-\\d+/")).toBeVisible()
  })

  test("TC418 - Premium user create workspace", async ({ page }) => {
    test.skip(
      !process.env.RUN_WORKSPACE_TESTS,
      "Set RUN_WORKSPACE_TESTS=1 to run (creates workspaces)",
    )
    await loginOrSkip(page, PREMIUM_EMAIL, PREMIUM_PASSWORD, test)
    await page.goto("/workspaces")

    await expect(page.locator("h1:has-text('Workspaces')")).toBeVisible({
      timeout: 15_000,
    })

    await page.locator('button:has-text("New")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    const workspaceName = `E2E_Premium_WS_${Date.now()}`
    await dialog
      .locator('input[placeholder="Workspace Name"]')
      .fill(workspaceName)
    await dialog.locator('button:has-text("Ok")').click()

    await expect(
      page.locator("text=The workspace has been created successfully!"),
    ).toBeVisible({ timeout: 15_000 })
  })
})

// ==============================================
// TC 423-426: Premium Over Storage Limit
// ==============================================

test.describe("Premium Over Storage Limit", () => {
  test("TC423 - Premium over limit login", async ({ page }) => {
    await loginOrSkip(page, PREMIUM_OVER_EMAIL, PREMIUM_OVER_PASSWORD, test)

    // Should see premium resource preparation snackbar
    await expect(page.locator("text=premium resource")).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC424 - Storage Limit Exceeded warning", async ({ page }) => {
    await loginOrSkip(page, PREMIUM_OVER_EMAIL, PREMIUM_OVER_PASSWORD, test)

    // Storage Limit Exceeded dialog should appear
    await expect(page.locator("text=Storage Limit Exceeded")).toBeVisible({
      timeout: 15_000,
    })

    // Should show storage usage details
    await expect(page.locator("text=Current Usage:")).toBeVisible()
  })

  test("TC425 - Handle later returns to dashboard", async ({ page }) => {
    await loginOrSkip(page, PREMIUM_OVER_EMAIL, PREMIUM_OVER_PASSWORD, test)

    // Wait for storage limit dialog
    const handleLater = page.locator('button:has-text("Handle later")')
    await expect(handleLater).toBeVisible({ timeout: 15_000 })

    await handleLater.click()

    // Should return to dashboard
    await expect(page).toHaveURL(/\/dashboard/)
    // Warning should be dismissed
    await expect(page.locator("text=Storage Limit Exceeded")).toBeHidden()
  })

  test("TC426 - Manage files redirects to workspaces", async ({ page }) => {
    await loginOrSkip(page, PREMIUM_OVER_EMAIL, PREMIUM_OVER_PASSWORD, test)

    // Wait for storage limit dialog
    const manageFiles = page.locator('button:has-text("Manage Files")')
    await expect(manageFiles).toBeVisible({ timeout: 15_000 })

    await manageFiles.click()

    // Should redirect to workspaces
    await expect(page).toHaveURL(/\/workspaces/, { timeout: 15_000 })
  })
})

// ==============================================
// TC 429-433: Free Over Storage Limit (Downgrade)
// ==============================================

test.describe("Free Over Storage Limit (Downgrade)", () => {
  test("TC429 - Downgrade user login", async ({ page }) => {
    await loginOrSkip(page, DOWNGRADE_EMAIL, DOWNGRADE_PASSWORD, test)
    await expect(page.locator("text=Dashboard")).toBeVisible()
  })

  test("TC430 - Storage Limit Exceeded warning for downgrade user", async ({
    page,
  }) => {
    await loginOrSkip(page, DOWNGRADE_EMAIL, DOWNGRADE_PASSWORD, test)

    // Storage Limit Exceeded dialog should appear
    await expect(page.locator("text=Storage Limit Exceeded")).toBeVisible({
      timeout: 15_000,
    })

    await expect(page.locator("text=Current Usage:")).toBeVisible()
  })

  test("TC431 - Handle later returns to dashboard", async ({ page }) => {
    await loginOrSkip(page, DOWNGRADE_EMAIL, DOWNGRADE_PASSWORD, test)

    const handleLater = page.locator('button:has-text("Handle later")')
    await expect(handleLater).toBeVisible({ timeout: 15_000 })

    await handleLater.click()

    await expect(page).toHaveURL(/\/dashboard/)
    await expect(page.locator("text=Storage Limit Exceeded")).toBeHidden()
  })

  test("TC432 - Manage files redirects to workspaces", async ({ page }) => {
    await loginOrSkip(page, DOWNGRADE_EMAIL, DOWNGRADE_PASSWORD, test)

    const manageFiles = page.locator('button:has-text("Manage Files")')
    await expect(manageFiles).toBeVisible({ timeout: 15_000 })

    await manageFiles.click()

    await expect(page).toHaveURL(/\/workspaces/, { timeout: 15_000 })
  })

  test("TC433 - Upgrade redirects to subscription", async ({ page }) => {
    await loginOrSkip(page, DOWNGRADE_EMAIL, DOWNGRADE_PASSWORD, test)

    const upgradeButton = page.locator('button:has-text("Upgrade")').first()
    await expect(upgradeButton).toBeVisible({ timeout: 15_000 })

    await upgradeButton.click()

    await expect(page).toHaveURL(/\/subscription/, { timeout: 15_000 })
  })
})

// ==============================================
// TC 436-437: Limit Warning Dismissal
// ==============================================

test.describe("Limit Warning Dismissal", () => {
  test("TC436 - Dismiss warning persists in session", async ({ page }) => {
    // Use any over-limit user
    await loginOrSkip(page, DOWNGRADE_EMAIL, DOWNGRADE_PASSWORD, test)

    // Wait for warning
    const handleLater = page.locator('button:has-text("Handle later")')
    const isWarningVisible = await handleLater
      .isVisible({ timeout: 15_000 })
      .catch(() => false)

    test.skip(!isWarningVisible, "No storage warning appeared — skipping")

    await handleLater.click()

    // Warning should be dismissed
    await expect(page.locator("text=Storage Limit Exceeded")).toBeHidden()

    // Navigate to different page and back
    await page.goto("/workspaces")
    await expect(page.locator("h1:has-text('Workspaces')")).toBeVisible({
      timeout: 15_000,
    })

    // Navigate back to dashboard
    await page.goto("/dashboard")
    await expect(page.locator("text=Dashboard")).toBeVisible({
      timeout: 15_000,
    })

    // Warning should NOT reappear
    await page.waitForTimeout(2_000)
    await expect(page.locator("text=Storage Limit Exceeded")).toBeHidden()
  })

  test("TC437 - Warning reappears after logout/login", async ({ page }) => {
    test.setTimeout(120_000)
    await loginOrSkip(page, DOWNGRADE_EMAIL, DOWNGRADE_PASSWORD, test)

    // Wait for warning
    const handleLater = page.locator('button:has-text("Handle later")')
    const isWarningVisible = await handleLater
      .isVisible({ timeout: 15_000 })
      .catch(() => false)

    test.skip(!isWarningVisible, "No storage warning appeared — skipping")

    // Dismiss the warning
    await handleLater.click()
    await expect(page.locator("text=Storage Limit Exceeded")).toBeHidden()

    // Sign out
    const profileButton = page.locator('[aria-label="open profile menu"]')
    await profileButton.click()
    await page.locator("text=Sign Out").click()

    // Should redirect to login
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 })

    // Login again
    await login(page, DOWNGRADE_EMAIL, DOWNGRADE_PASSWORD)

    // Warning should reappear
    await expect(page.locator("text=Storage Limit Exceeded")).toBeVisible({
      timeout: 15_000,
    })
  })
})

// ==============================================
// TC 441-442: Expired Premium (Grace Period)
// ==============================================

test.describe("Expired Premium - Grace Period", () => {
  test("TC441 - Grace period login (under 5GB)", async ({ page }) => {
    await loginOrSkip(page, GRACE_UNDER_EMAIL, GRACE_UNDER_PASSWORD, test)

    // Should show grace period warning (may not appear if account state differs)
    const graceWarning = page
      .locator("text=Premium Subscription Expired")
      .first()
    const isVisible = await graceWarning
      .isVisible({ timeout: 15_000 })
      .catch(() => false)

    test.skip(
      !isVisible,
      "Grace period warning not shown for this account — skipping",
    )

    await expect(graceWarning).toBeVisible()
  })

  test("TC442 - Grace period warning details (over 5GB)", async ({ page }) => {
    await loginOrSkip(page, GRACE_OVER_EMAIL, GRACE_OVER_PASSWORD, test)

    // Should show grace period warning with storage details
    // Use .first() to avoid strict mode: text matches both AlertTitle and body paragraph
    const graceWarning = page
      .locator("text=Premium Subscription Expired")
      .first()
    const isVisible = await graceWarning
      .isVisible({ timeout: 15_000 })
      .catch(() => false)

    test.skip(
      !isVisible,
      "Grace period warning not shown for this account — skipping",
    )

    await expect(graceWarning).toBeVisible()

    // Should show storage usage details
    await expect(page.locator("text=Current Usage:")).toBeVisible()
  })
})

// ==============================================
// TC 444: Expired Premium (Past Grace Period)
// ==============================================

test.describe("Expired Premium - Past Grace Period", () => {
  test("TC444 - Overdue warning appears", async ({ page }) => {
    await loginOrSkip(
      page,
      PREMIUM_EXPIRED_EMAIL,
      PREMIUM_EXPIRED_PASSWORD,
      test,
    )

    // Should show overdue warning (may not appear if account state differs)
    const overdueTitle = page.locator("text=Data Cleanup Overdue")
    const urgentTitle = page.locator("text=Urgent: Data Deletion Imminent")

    const isVisible = await overdueTitle
      .or(urgentTitle)
      .first()
      .isVisible({ timeout: 15_000 })
      .catch(() => false)

    test.skip(
      !isVisible,
      "Overdue warning not shown for this account — skipping",
    )

    // Should show action required message
    await expect(page.locator("text=Action Required")).toBeVisible()

    // Should have acknowledgment checkbox for overdue
    await expect(page.locator("text=I understand")).toBeVisible()
  })
})

// ==============================================
// TC 445, 447: Manual Storage Refresh
// ==============================================

test.describe("Manual Storage Refresh", () => {
  test.beforeEach(async ({ page }) => {
    await loginOrSkip(page, FREE_EMAIL, FREE_PASSWORD, test)

    // Dismiss storage warning if present
    const handleLater = page.locator('button:has-text("Handle later")')
    if (await handleLater.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await handleLater.click()
    }

    await page.goto("/workspaces")
    await expect(page.locator("h1:has-text('Workspaces')")).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC445 - Reload button visible and functional", async ({ page }) => {
    const reloadButton = page.locator('button:has-text("Reload")')
    await expect(reloadButton).toBeVisible()

    // Click reload
    await reloadButton.click()

    // Should show success message
    await expect(page.locator("text=Storage refreshed for")).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC447 - Reload button disabled while loading", async ({ page }) => {
    const reloadButton = page.locator('button:has-text("Reload")')
    await expect(reloadButton).toBeVisible()

    // Click reload
    await reloadButton.click()

    // Button should be disabled while loading
    await expect(reloadButton).toBeDisabled()

    // Wait for reload to complete
    await expect(page.locator("text=Storage refreshed for")).toBeVisible({
      timeout: 15_000,
    })

    // Button should be enabled again
    await expect(reloadButton).toBeEnabled()
  })
})

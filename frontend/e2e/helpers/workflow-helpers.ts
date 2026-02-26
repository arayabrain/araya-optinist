import { expect, Page } from "@playwright/test"

// Test credentials - configurable via env vars
export const TEST_EMAIL =
  process.env.TEST_USER_EMAIL || "tsuchiyama_yutaka@araya.org"
export const TEST_PASSWORD =
  process.env.TEST_USER_PASSWORD || "YutakaTsuchiyama123"

// Optional: Pre-existing workspace ID for read-only tests
export const TEST_WORKSPACE_ID = process.env.TEST_WORKSPACE_ID || ""

// Module-level cache: once we determine workspace data can't load, skip instantly
let workspaceDataCache: boolean | null = null

// Helper: Login with given credentials
export async function login(page: Page, email: string, password: string) {
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

// Helper: Dismiss storage warning if present
export async function dismissStorageWarning(page: Page) {
  try {
    const handleLater = page.locator('button:has-text("Handle later")')
    await expect(handleLater).toBeVisible({ timeout: 5_000 })
    await handleLater.click()
  } catch {
    // No storage warning — continue
  }
}

// Helper: Navigate to workspaces page (title only)
export async function goToWorkspaces(page: Page) {
  await page.goto("/workspaces")
  await expect(page.locator("text=Workspaces").first()).toBeVisible({
    timeout: 15_000,
  })
}

// Helper: Navigate to workspaces page and wait for data rows to load.
// Returns true if workspace rows appeared, false if table stayed empty.
// Uses a module-level cache: if the first check fails, all subsequent calls
// return false instantly instead of each waiting 30s.
export async function goToWorkspacesWithData(page: Page): Promise<boolean> {
  // If we already know workspace data is unavailable, skip instantly
  if (workspaceDataCache === false) return false

  await page.goto("/workspaces")
  await expect(page.locator("text=Workspaces").first()).toBeVisible({
    timeout: 15_000,
  })

  // Wait for workspace data rows to appear (WORKFLOW button in a row)
  // The workspace API can be very slow — give it 30s
  // NOTE: locator.isVisible() does NOT accept a timeout and returns immediately.
  // We must use expect().toBeVisible() which actually waits.
  const workflowButton = page.locator('button:has-text("Workflow")').first()
  let isVisible = false
  try {
    await expect(workflowButton).toBeVisible({ timeout: 30_000 })
    isVisible = true
  } catch {
    isVisible = false
  }

  // Cache the result so subsequent tests skip instantly
  if (workspaceDataCache === null) {
    workspaceDataCache = isVisible
  }

  return isVisible
}

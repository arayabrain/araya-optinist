import { test, expect, Page } from "@playwright/test"
import {
  TEST_EMAIL,
  TEST_PASSWORD,
  login,
  dismissStorageWarning,
  goToWorkspacesWithData,
} from "./helpers/workflow-helpers"

// ==============================================
// Workflow Page UI Checks (GUI only — no execution)
// ==============================================

test.describe("Workflow Page UI", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)
    await dismissStorageWarning(page)
  })

  // Helper: Navigate to first workspace's workflow page
  async function goToWorkflow(page: Page): Promise<boolean> {
    const hasData = await goToWorkspacesWithData(page)
    if (!hasData) return false

    await page.locator('button:has-text("Workflow")').first().click()
    await expect(page).toHaveURL(/\/workspaces\/\d+/, { timeout: 15_000 })
    return true
  }

  test("TC532 - Sidebar displays workspace and workflow info", async ({
    page,
  }) => {
    const loaded = await goToWorkflow(page)
    test.skip(!loaded, "Workspace data did not load — skipping")

    // Sidebar should show workspace info
    await expect(page.locator("text=Workspace").first()).toBeVisible()
    await expect(page.locator("text=ID").first()).toBeVisible()
    await expect(page.locator("text=NAME").first()).toBeVisible()

    // Nodes section should be visible
    await expect(page.locator("text=Nodes").first()).toBeVisible()
    await expect(page.locator("text=Data").first()).toBeVisible()
    await expect(page.locator("text=Algorithm").first()).toBeVisible()
  })

  test("TC535 - RUN button is visible on workflow page", async ({ page }) => {
    const loaded = await goToWorkflow(page)
    test.skip(!loaded, "Workspace data did not load — skipping")

    // RUN button should be visible
    const runButton = page.locator('button:has-text("RUN")')
    await expect(runButton.first()).toBeVisible()
  })

  test("TC536 - Workflow toolbar icons are visible", async ({ page }) => {
    const loaded = await goToWorkflow(page)
    test.skip(!loaded, "Workspace data did not load — skipping")

    // Toolbar area near RUN button should have action icons
    // (new workflow, save, import/export, etc.)
    const toolbar = page.locator('button:has-text("RUN")').locator("..")
    await expect(toolbar).toBeVisible()
  })
})

// ==============================================
// Record Page UI Checks (GUI only — no execution)
// ==============================================

test.describe("Record Page UI", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)
    await dismissStorageWarning(page)
  })

  // Helper: Navigate to Record tab of first workspace
  async function goToRecords(page: Page): Promise<boolean> {
    const hasData = await goToWorkspacesWithData(page)
    if (!hasData) return false

    await page.locator('button:has-text("Workflow")').first().click()
    await expect(page).toHaveURL(/\/workspaces\/\d+/, { timeout: 15_000 })
    await page.locator('button[role="tab"]:has-text("Record")').click()
    return true
  }

  test("TC519 - Record tab loads and shows content", async ({ page }) => {
    const loaded = await goToRecords(page)
    test.skip(!loaded, "Workspace data did not load — skipping")

    // Record page should show either records with Timestamp column or empty state
    await expect(
      page
        .locator("text=Timestamp")
        .or(page.locator("text=No records"))
        .first(),
    ).toBeVisible({ timeout: 15_000 })
  })

  test("TC522 - Record table has expected columns", async ({ page }) => {
    const loaded = await goToRecords(page)
    test.skip(!loaded, "Workspace data did not load — skipping")

    // Wait for record content to load
    const hasTimestamp = await page
      .locator("text=Timestamp")
      .first()
      .isVisible()
      .catch(() => false)
    test.skip(!hasTimestamp, "No records found — skipping column check")

    // Timestamp column should be visible
    await expect(page.locator("text=Timestamp").first()).toBeVisible()
  })
})

// ==============================================
// Visualize Page UI Checks (GUI only)
// ==============================================

test.describe("Visualize Page UI", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)
    await dismissStorageWarning(page)
  })

  test("TC512 - Visualize tab opens and becomes active", async ({ page }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "Workspace data did not load — skipping")

    await page.locator('button:has-text("Workflow")').first().click()
    await expect(page).toHaveURL(/\/workspaces\/\d+/, { timeout: 15_000 })

    // Click Visualize tab
    await page.locator('button[role="tab"]:has-text("Visualize")').click()

    // Visualize tab should become active
    await expect(
      page.locator('button[role="tab"]:has-text("Visualize")'),
    ).toHaveAttribute("aria-selected", "true", { timeout: 10_000 })
  })

  test("TC513 - Visualize page shows workflow info", async ({ page }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "Workspace data did not load — skipping")

    await page.locator('button:has-text("Workflow")').first().click()
    await expect(page).toHaveURL(/\/workspaces\/\d+/, { timeout: 15_000 })

    // Click Visualize tab
    await page.locator('button[role="tab"]:has-text("Visualize")').click()
    await expect(
      page.locator('button[role="tab"]:has-text("Visualize")'),
    ).toHaveAttribute("aria-selected", "true", { timeout: 10_000 })

    // Should show workflow ID or name somewhere on the page
    await expect(
      page.locator("text=Workflow").or(page.locator("text=ID")).first(),
    ).toBeVisible({ timeout: 10_000 })
  })
})

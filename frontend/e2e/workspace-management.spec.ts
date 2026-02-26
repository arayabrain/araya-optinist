import { test, expect } from "@playwright/test"
import {
  TEST_EMAIL,
  TEST_PASSWORD,
  login,
  dismissStorageWarning,
  goToWorkspaces,
  goToWorkspacesWithData,
} from "./helpers/workflow-helpers"

// ==============================================
// TC 501-508: Workspace Management (GUI checks only)
// ==============================================

test.describe("Workspace Management", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)
    await dismissStorageWarning(page)
  })

  test("TC501 - Create workspace dialog", async ({ page }) => {
    await goToWorkspaces(page)

    // NEW button should be visible
    const newButton = page.locator('button:has-text("New")')
    await expect(newButton).toBeVisible()

    // Click New button — dialog should open
    await newButton.click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator("text=New Workspace")).toBeVisible()

    // Dialog should have workspace name input and Ok/Cancel buttons
    await expect(dialog.locator('[placeholder="Workspace Name"]')).toBeVisible()
    await expect(dialog.locator('button:has-text("Ok")')).toBeVisible()

    // Close dialog without creating
    await dialog
      .locator('button:has-text("Cancel")')
      .click()
      .catch(async () => {
        await page.keyboard.press("Escape")
      })
  })

  test("TC502 - Display columns in workspace list", async ({ page }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "Workspace data did not load — skipping")

    // Column headers should be visible in the table
    await expect(page.locator("text=ID").first()).toBeVisible()
    await expect(page.locator("text=Workspace Name").first()).toBeVisible()
    await expect(page.locator("text=Owner").first()).toBeVisible()
    await expect(page.locator("text=Created").first()).toBeVisible()
    await expect(page.locator("text=Data size").first()).toBeVisible()
  })

  test("TC503 - Reload button visibility", async ({ page }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "Workspace data did not load — skipping")

    const reloadButton = page.locator('button:has-text("Reload")')
    await expect(reloadButton).toBeVisible()
  })

  test("TC505 - Workspace action buttons visibility", async ({ page }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "Workspace data did not load — skipping")

    // Each workspace row should have WORKFLOW, RECORDS, DATAVIEW buttons
    await expect(
      page.locator('button:has-text("Workflow")').first(),
    ).toBeVisible()
    await expect(
      page.locator('button:has-text("Records")').first(),
    ).toBeVisible()
    await expect(
      page.locator('button:has-text("Dataview")').first(),
    ).toBeVisible()
  })

  test("TC506 - Dataview button navigates to dataview page", async ({
    page,
  }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "Workspace data did not load — skipping")

    await page.locator('button:has-text("Dataview")').first().click()
    await expect(page).toHaveURL(/\/dataview\/\d+/, { timeout: 15_000 })
  })
})

// ==============================================
// TC 507-508: Workflow & Record Page Navigation
// ==============================================

test.describe("Workflow & Record Page Navigation", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)
    await dismissStorageWarning(page)
  })

  test("TC507 - Workflow page loads with correct tabs", async ({ page }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "Workspace data did not load — skipping")

    await page.locator('button:has-text("Workflow")').first().click()
    await expect(page).toHaveURL(/\/workspaces\/\d+/, { timeout: 15_000 })

    // All three tabs should be visible
    await expect(
      page.locator('button[role="tab"]:has-text("Workflow")'),
    ).toBeVisible()
    await expect(
      page.locator('button[role="tab"]:has-text("Visualize")'),
    ).toBeVisible()
    await expect(
      page.locator('button[role="tab"]:has-text("Record")'),
    ).toBeVisible()

    // WORKSPACES link should be visible in header
    await expect(page.locator("text=Workspaces").first()).toBeVisible()
  })

  test("TC508 - Workflow page UI elements", async ({ page }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "Workspace data did not load — skipping")

    await page.locator('button:has-text("Workflow")').first().click()
    await expect(page).toHaveURL(/\/workspaces\/\d+/, { timeout: 15_000 })

    // RUN button should be visible
    await expect(page.locator('button:has-text("RUN")').first()).toBeVisible()

    // Workspace info should be displayed in sidebar
    await expect(page.locator("text=Workspace").first()).toBeVisible()
    await expect(page.locator("text=Nodes").first()).toBeVisible()
  })
})

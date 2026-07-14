import { test, expect } from "@playwright/test"

import {
  skipWithoutCreds,
  freeStorageState,
  gotoDashboard,
  goToWorkspaces,
  goToWorkspacesWithData,
  createWorkspace,
} from "./helpers"

// Workspace list: create, display, navigation, storage reload, delete

const WS_NAME = `e2e-ws-${Date.now()}`

test.describe("Workspace", () => {
  test.use({ storageState: freeStorageState() })

  test.beforeEach(async ({ page }) => {
    skipWithoutCreds()
    await gotoDashboard(page)
  })

  test("WS-01 - Create new workspace", async ({ page }) => {
    await createWorkspace(page, WS_NAME)
  })

  test("WS-02 - Workspace list displays with columns", async ({ page }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "No workspace rows loaded")

    await expect(page.locator("text=ID").first()).toBeVisible()
    await expect(page.locator("text=Workspace Name").first()).toBeVisible()
    await expect(page.locator("text=Owner").first()).toBeVisible()
    await expect(page.locator("text=Created").first()).toBeVisible()
    await expect(page.locator("text=Data size").first()).toBeVisible()
  })

  test("WS-03 - Workflow button navigates to workflow page", async ({
    page,
  }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "No workspace rows loaded")

    await page.locator('button:has-text("Workflow")').first().click()
    await expect(page).toHaveURL(/\/workspaces\/\d+/, { timeout: 15_000 })
    await expect(
      page.locator('button[role="tab"]:has-text("Workflow")'),
    ).toBeVisible()
  })

  test("WS-04 - Storage reload button refreshes storage", async ({ page }) => {
    await goToWorkspaces(page)
    const reloadButton = page.locator('button:has-text("Reload")')
    await expect(reloadButton).toBeVisible()
    await reloadButton.click()

    await expect(
      page.locator("text=/Storage refreshed|refreshed/i").first(),
    ).toBeVisible({ timeout: 60_000 })
  })

  test("WS-05 - Dataview button navigates to dataview page", async ({
    page,
  }) => {
    const hasData = await goToWorkspacesWithData(page)
    test.skip(!hasData, "No workspace rows loaded")

    await page.locator('button:has-text("Dataview")').first().click()
    await expect(page).toHaveURL(/\/dataview\/\d+/, { timeout: 15_000 })
  })

  test("WS-06 - Delete workspace", async ({ page }) => {
    await goToWorkspaces(page)
    // Wait for the grid to load before checking for the row, otherwise the
    // fallback creates a duplicate workspace
    await page
      .locator(".MuiDataGrid-row")
      .first()
      .waitFor({ timeout: 30_000 })
      .catch(() => {})

    // Delete the workspace created in WS-01; create one if it isn't there
    let row = page.locator(`.MuiDataGrid-row:has-text("${WS_NAME}")`)
    if (!(await row.count())) {
      await createWorkspace(page, WS_NAME)
      row = page.locator(`.MuiDataGrid-row:has-text("${WS_NAME}")`)
    }

    await row.locator('[data-testid="DeleteIcon"]').click()
    await page.locator('[role="dialog"] [placeholder="DELETE"]').fill("DELETE")
    await page.locator('button:has-text("Delete Workspace")').click()

    await expect(
      page.locator(`.MuiDataGrid-row:has-text("${WS_NAME}")`),
    ).toHaveCount(0, { timeout: 15_000 })
  })
})

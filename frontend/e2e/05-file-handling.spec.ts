import { test, expect } from "@playwright/test"

import {
  skipWithoutCreds,
  freeStorageState,
  gotoDashboard,
  openWorkspace,
  ensureTutorialRecords,
  reproduceTutorial,
  DATA_WS,
} from "./helpers"

// File select dialog: tree display, wildcard filtering, check-all, and the
// workflow-page sidebar toggle. HDF5/CSV node dialogs need specific node
// types wired in the flowchart — left manual.
// Sample data must be imported first or the file tree is empty (a fresh
// workspace still shows a default input node, so node presence is no signal).

test.describe("File Select Dialog", () => {
  test.use({ storageState: freeStorageState() })

  test.beforeEach(async ({ page }) => {
    test.setTimeout(240_000)
    skipWithoutCreds()
    await gotoDashboard(page)
    await openWorkspace(page, DATA_WS)
    await ensureTutorialRecords(page, DATA_WS)
    // Tutorial1 is image-based; the flowchart otherwise restores whichever
    // pipeline ran last, which may have no image node at all
    await reproduceTutorial(page, "Tutorial1")

    const selectFile = page
      .locator(
        '.react-flow__node-ImageFileNode button:has([data-testid="ChecklistRtlIcon"])',
      )
      .first()
    await expect(selectFile).toBeVisible({ timeout: 15_000 })
    await selectFile.click()
    await expect(page.locator('[role="dialog"]')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("FILE-01 - File tree displays available files", async ({ page }) => {
    const dialog = page.locator('[role="dialog"]')
    await expect(
      dialog.locator('[placeholder="Filter... (* as wildcard)"]'),
    ).toBeVisible()
    // Tree should list at least one file entry (sample data is imported).
    // treeitem only — an "or li" fallback matches the Selected Files panel,
    // which renders even when the tree is empty
    await expect(dialog.locator('[role="treeitem"]').first()).toBeVisible({
      timeout: 15_000,
    })
  })

  test("FILE-02 - Wildcard filter narrows file list", async ({ page }) => {
    const dialog = page.locator('[role="dialog"]')
    const filter = dialog.locator('[placeholder="Filter... (* as wildcard)"]')
    // The image node's tree only ever holds image files, so the negative
    // case must use a pattern that excludes the known-present tiff — a
    // ".csv absent" check passes even with the filter broken. Scope to tree
    // rows: the dialog's Selected Files panel shows the node's current
    // selection no matter what the filter says.
    const tiffRows = dialog
      .locator('[role="treeitem"]')
      .filter({ hasText: /\.tiff/i })
    // The tiff must be listed BEFORE filtering, or the exclusion check
    // passes vacuously against a still-loading tree
    await expect(tiffRows.first()).toBeVisible({ timeout: 15_000 })

    await filter.fill("*.nomatch")
    await expect(tiffRows).toHaveCount(0, { timeout: 10_000 })

    await filter.fill("*.tiff")
    await expect(tiffRows.first()).toBeVisible({ timeout: 10_000 })
  })

  test("FILE-03 - Check all / uncheck all selects and clears all files", async ({
    page,
  }) => {
    const dialog = page.locator('[role="dialog"]')
    // Wait for the file tree to load: check-all no-ops on an empty tree, so
    // the header box (1) plus >=1 file box must be present first
    const boxes = dialog.locator('input[type="checkbox"]')
    await expect(async () => {
      expect(await boxes.count()).toBeGreaterThan(1)
    }).toPass({ timeout: 15_000 })

    // The header check-all box is the first checkbox. Its checked state is the
    // component's own "all files selected" signal (allChecked). Directory rows
    // render their own derived checkboxes and the tree lazy-renders, so assert
    // the header aggregate + the unambiguous "clear selects nothing", not a
    // whole-dialog count.
    const checkAll = boxes.first()
    const checked = dialog.locator('input[type="checkbox"]:checked')

    // Drive to select-all (idempotent regardless of the node's initial pick)
    if (!(await checkAll.isChecked())) await checkAll.click()
    await expect(checkAll).toBeChecked({ timeout: 10_000 })
    await expect(checked).not.toHaveCount(0)

    // Uncheck all -> selection cleared, nothing checked anywhere
    await checkAll.click()
    await expect(checked).toHaveCount(0, { timeout: 10_000 })

    // Leave the node's original selection untouched
    await dialog.locator('button:has-text("cancel")').click()
  })

  test("FILE-04 - Workflow sidebar toggle hides and shows the sidebar", async ({
    page,
  }) => {
    // The sidebar lives on the workflow page behind the dialog
    await page.locator('[role="dialog"] button:has-text("cancel")').click()
    await expect(page.locator('[role="dialog"]')).toBeHidden()

    // The header hamburger also uses MenuIcon; exclude it by its aria-label
    const openSidebar = page.locator(
      'button:has([data-testid="MenuIcon"]):not([aria-label="open drawer"])',
    )
    const closeSidebar = page.locator(
      'button:has([data-testid="MenuOpenIcon"])',
    )

    await expect(openSidebar).toBeHidden()
    await closeSidebar.click()
    await expect(openSidebar).toBeVisible()
    await openSidebar.click()
    await expect(openSidebar).toBeHidden()
  })
})

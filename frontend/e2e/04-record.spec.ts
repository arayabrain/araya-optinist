import { readFile } from "fs/promises"

import { test, expect, Page } from "@playwright/test"

import {
  skipWithoutCreds,
  freeStorageState,
  gotoDashboard,
  openWorkspace,
  ensureTutorialRecords,
  DATA_WS,
} from "./helpers"

// Record page: list, expand, copy, delete, downloads (single and multi-select).

async function firstRecordRow(page: Page) {
  const row = page.locator('tr:has([data-testid="reproduce-button"])').first()
  const visible = await row.isVisible().catch(() => false)
  return visible ? row : null
}

test.describe("Record Management", () => {
  test.use({ storageState: freeStorageState() })

  test.beforeEach(async ({ page }) => {
    test.setTimeout(180_000)
    skipWithoutCreds()
    await gotoDashboard(page)
    await openWorkspace(page, DATA_WS)
    await ensureTutorialRecords(page, DATA_WS)
  })

  test("REC-01 - Record page loads and shows records", async ({ page }) => {
    // beforeEach guarantees records exist, so no "No records" fallback —
    // it could mask a table that failed to render its rows
    await expect(page.locator("text=Timestamp").first()).toBeVisible({
      timeout: 15_000,
    })
    await expect(
      page.locator('tr:has([data-testid="reproduce-button"])').first(),
    ).toBeVisible()
  })

  test("REC-02 - Expand record shows workflow parameters", async ({ page }) => {
    const row = await firstRecordRow(page)
    test.skip(!row, "No records — import sample data first")

    await row!.locator('[aria-label="expand row"]').click()
    // Expanded panel shows the per-node function/parameter table
    await expect(page.locator("text=Function").first()).toBeVisible({
      timeout: 10_000,
    })
  })

  test("REC-03 - Copy single record", async ({ page }) => {
    test.setTimeout(120_000)
    const row = await firstRecordRow(page)
    test.skip(!row, "No records — import sample data first")

    const rowCount = await page
      .locator('tr:has([data-testid="reproduce-button"])')
      .count()

    await row!.locator('input[type="checkbox"]').check()
    await page.locator('button:has-text("COPY")').click()
    await page.locator('[role="dialog"] button:has-text("copy")').click()

    await expect(
      page.locator('tr:has([data-testid="reproduce-button"])'),
    ).toHaveCount(rowCount + 1, { timeout: 60_000 })
  })

  test("REC-04 - Delete single record", async ({ page }) => {
    test.setTimeout(120_000)
    // Delete the copy made by REC-03 (falls back to making one first)
    let copyRow = page.locator('tr:has-text("_copy")').first()
    if (!(await copyRow.isVisible().catch(() => false))) {
      const row = await firstRecordRow(page)
      test.skip(!row, "No records — import sample data first")
      await row!.locator('input[type="checkbox"]').check()
      await page.locator('button:has-text("COPY")').click()
      await page.locator('[role="dialog"] button:has-text("copy")').click()
      copyRow = page.locator('tr:has-text("_copy")').first()
      await expect(copyRow).toBeVisible({ timeout: 60_000 })
      await row!.locator('input[type="checkbox"]').uncheck()
    }

    const rowCount = await page
      .locator('tr:has([data-testid="reproduce-button"])')
      .count()

    await copyRow.locator('input[type="checkbox"]').check()
    await page.locator('[data-testid="delete-selected-button"]').click()
    await page.locator('[role="dialog"] button:has-text("delete")').click()

    await expect(
      page.locator('tr:has([data-testid="reproduce-button"])'),
    ).toHaveCount(rowCount - 1, { timeout: 60_000 })
  })

  test("REC-05 - Download workflow file", async ({ page }) => {
    const row = await firstRecordRow(page)
    test.skip(!row, "No records — import sample data first")

    const downloadPromise = page.waitForEvent("download", { timeout: 30_000 })
    await row!.locator('[data-testid="workflow-download-button"]').click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/\.yaml$/)
  })

  test("REC-06 - Download Snakemake file", async ({ page }) => {
    const row = await firstRecordRow(page)
    test.skip(!row, "No records — import sample data first")

    // The testid anchor is hidden; the visible IconButton next to it triggers
    // the download
    const downloadPromise = page.waitForEvent("download", { timeout: 30_000 })
    await row!
      .locator('td:has([data-testid="snakemake-download-link"]) button')
      .click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/^snakemake_.*\.yaml$/)
  })

  test("REC-07 - Download NWB file", async ({ page }) => {
    // NWB only exists after a workflow run — the button is disabled (or the
    // cell empty) until then
    const nwbButton = page
      .locator('td:has([data-testid="nwb-download-link"]) button:enabled')
      .first()
    const hasNwb = await nwbButton
      .waitFor({ timeout: 5_000 })
      .then(() => true)
      .catch(() => false)
    test.skip(
      !hasNwb,
      "No NWB output — requires a completed workflow run (WF-04)",
    )

    const downloadPromise = page.waitForEvent("download", { timeout: 60_000 })
    await nwbButton.click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/\.nwb$/)

    // The filename alone passed while the API answered 200 with the body
    // `false`: the button builds the blob from whatever it receives, so a 5-byte
    // download still fires the event and still ends in .nwb. Assert the payload.
    const path = await download.path()
    expect(path).not.toBeNull()
    const contents = await readFile(path!)
    expect(contents.byteLength).toBeGreaterThan(64)
    // HDF5, which NWB is built on, opens with this 8-byte signature.
    expect(contents.subarray(0, 8)).toEqual(
      Buffer.from([0x89, 0x48, 0x44, 0x46, 0x0d, 0x0a, 0x1a, 0x0a]),
    )
  })

  test("REC-08 - Copy multiple records", async ({ page }) => {
    test.setTimeout(180_000)
    const rows = page.locator('tr:has([data-testid="reproduce-button"])')
    const rowCount = await rows.count()
    test.skip(rowCount < 2, "Needs at least 2 records")

    await rows.nth(0).locator('input[type="checkbox"]').check()
    await rows.nth(1).locator('input[type="checkbox"]').check()
    await page.locator('button:has-text("COPY")').click()
    await page.locator('[role="dialog"] button:has-text("copy")').click()

    await expect(rows).toHaveCount(rowCount + 2, { timeout: 120_000 })
  })

  test("REC-09 - Delete multiple records", async ({ page }) => {
    test.setTimeout(180_000)
    const copies = page.locator('tr:has-text("_copy")')
    const copyCount = await copies.count()
    test.skip(copyCount < 2, "Needs 2 copied records (REC-08 creates them)")

    const rows = page.locator('tr:has([data-testid="reproduce-button"])')
    const rowCount = await rows.count()

    await copies.nth(0).locator('input[type="checkbox"]').check()
    await copies.nth(1).locator('input[type="checkbox"]').check()
    await page.locator('[data-testid="delete-selected-button"]').click()
    await page.locator('[role="dialog"] button:has-text("delete")').click()

    await expect(rows).toHaveCount(rowCount - 2, { timeout: 120_000 })
  })
})

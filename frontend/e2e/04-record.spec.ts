import { readFile } from "fs/promises"

import { test, expect, Page } from "@playwright/test"

import {
  skipWithoutCreds,
  freeStorageState,
  gotoDashboard,
  openWorkspace,
  ensureCompletedTutorialRun,
  ensureTutorialRecords,
  DATA_WS,
} from "./helpers"

// Record page: list, expand, copy, delete, downloads (single and multi-select).

// The records the hook imports are a precondition, not a maybe: this used to
// answer null on an unrendered table (isVisible does not wait), and each test
// then skipped - which reads as a pass in the summary the sheets are signed
// off against
async function firstRecordRow(page: Page) {
  const row = page.locator('tr:has([data-testid="reproduce-button"])').first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  return row
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

    await row.locator('[aria-label="expand row"]').click()
    // `text=Function` is a case-insensitive substring, so it matched the page
    // with the panel's own table absent. The claim is the per-node table: its
    // four columns, and a row naming a function and its node id.
    const panel = page
      .locator("td")
      .filter({ has: page.getByText("Details", { exact: true }) })
    await expect(panel).toBeVisible({ timeout: 10_000 })
    await expect(panel.locator("thead th")).toHaveText([
      "Function",
      "nodeID",
      "Success",
      "NWB",
    ])
    const nodeCells = panel.locator("tbody tr").first().locator("th, td")
    await expect(nodeCells.nth(0)).not.toBeEmpty()
    await expect(nodeCells.nth(1)).not.toBeEmpty()
  })

  test("REC-03 - Copy single record", async ({ page }) => {
    test.setTimeout(120_000)
    const row = await firstRecordRow(page)

    const rowCount = await page
      .locator('tr:has([data-testid="reproduce-button"])')
      .count()

    await row.locator('input[type="checkbox"]').check()
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
      await row.locator('input[type="checkbox"]').check()
      await page.locator('button:has-text("COPY")').click()
      await page.locator('[role="dialog"] button:has-text("copy")').click()
      copyRow = page.locator('tr:has-text("_copy")').first()
      await expect(copyRow).toBeVisible({ timeout: 60_000 })
      await row.locator('input[type="checkbox"]').uncheck()
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

    const downloadPromise = page.waitForEvent("download", { timeout: 30_000 })
    await row.locator('[data-testid="workflow-download-button"]').click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/\.yaml$/)
  })

  test("REC-06 - Download Snakemake file", async ({ page }) => {
    const row = await firstRecordRow(page)

    // The testid anchor is hidden; the visible IconButton next to it triggers
    // the download
    const downloadPromise = page.waitForEvent("download", { timeout: 30_000 })
    await row
      .locator('td:has([data-testid="snakemake-download-link"]) button')
      .click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/^snakemake_.*\.yaml$/)
  })

  test("REC-07 - Download NWB file @slow", async ({ page }) => {
    // An NWB exists only after a completed run, and global setup deletes the
    // workspace at the start of every run, so the run is this test's
    // precondition. It used to skip on the resulting 404; now it runs the
    // workflow, which is what puts it in the @slow lane.
    test.setTimeout(900_000)
    await ensureCompletedTutorialRun(page, DATA_WS)

    const nwbButton = page
      .locator('tr:has([data-testid="reproduce-button"])')
      .filter({ has: page.getByText("Tutorial1", { exact: true }) })
      .first()
      .locator('td:has([data-testid="nwb-download-link"]) button')
    // The record's nwb flag lands after "Workflow finished", and the table only
    // reads it when the tab mounts
    await expect(async () => {
      await page.reload()
      await page.locator('button[role="tab"]:has-text("Record")').click()
      await expect(nwbButton).toBeEnabled({ timeout: 15_000 })
    }).toPass({ timeout: 180_000 })

    const nwbResponse = page.waitForResponse((r) => /\/nwb\//.test(r.url()), {
      timeout: 60_000,
    })
    const downloadPromise = page.waitForEvent("download", { timeout: 60_000 })
    await nwbButton.click()
    // The button being enabled is not evidence the file exists: the imported
    // tutorial metadata declares an nwb section but ships no .nwb, so the
    // button enables for records that were never run and the route 404s.
    expect((await nwbResponse).status()).toBe(200)

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
    expect(rowCount).toBeGreaterThanOrEqual(2)

    await rows.nth(0).locator('input[type="checkbox"]').check()
    await rows.nth(1).locator('input[type="checkbox"]').check()
    await page.locator('button:has-text("COPY")').click()
    await page.locator('[role="dialog"] button:has-text("copy")').click()

    await expect(rows).toHaveCount(rowCount + 2, { timeout: 120_000 })
  })

  test("REC-09 - Delete multiple records", async ({ page }) => {
    test.setTimeout(180_000)
    const copies = page.locator('tr:has-text("_copy")')
    // The copies REC-08 leaves behind, waited for rather than probed
    await expect(copies.nth(1)).toBeVisible({ timeout: 30_000 })

    const rows = page.locator('tr:has([data-testid="reproduce-button"])')
    const rowCount = await rows.count()

    await copies.nth(0).locator('input[type="checkbox"]').check()
    await copies.nth(1).locator('input[type="checkbox"]').check()
    await page.locator('[data-testid="delete-selected-button"]').click()
    await page.locator('[role="dialog"] button:has-text("delete")').click()

    await expect(rows).toHaveCount(rowCount - 2, { timeout: 120_000 })
  })
})

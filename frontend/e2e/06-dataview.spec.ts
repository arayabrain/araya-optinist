import { execSync } from "child_process"
import * as path from "path"

import { test, expect, Page } from "@playwright/test"

import {
  login,
  skipWithoutCreds,
  freeStorageState,
  gotoDashboard,
  ensureWorkspaceId,
  ensureCompletedTutorialRun,
  openWorkspace,
  apiUrl,
  DATA_WS,
} from "./helpers"

// Dataview: table display, public access, filters, sort, pagination,
// dialogs, thumbnails, publish/unpublish (UI + public listing). Left manual:
// DB/S3 sync verification, sync status states.

async function hasDataRows(page: Page): Promise<boolean> {
  // Wait out the data fetch rather than counting immediately
  return await page
    .locator('[role="grid"] [role="row"]')
    .nth(1)
    .waitFor({ timeout: 10_000 })
    .then(() => true)
    .catch(() => false)
}

// Global-setup wipes the e2e-* workspaces each run, so the success records
// the data-dependent tests need are minted once per run: the fast no-op
// rerun of the imported Tutorial1, then a record COPY of it (bulk operations
// need "multiple" records, and only Tutorial1's rerun is a reliable no-op —
// Tutorial2's recomputes CaImAn locally and fails). The registration lands
// slightly after "Workflow finished", hence the reload-poll.
// Publishing requires a cloud bucket on the account (the backend 400s
// without one). Local-stack users have none, so set a placeholder attribute
// — the S3-existence check is skipped in local storage mode, and all S3
// size lookups swallow errors. On deployed envs (no docker) this silently
// no-ops; users there have real buckets.
function ensurePublishableAccount() {
  const email = process.env.TEST_USER_EMAIL
  if (!email) return
  try {
    execSync(
      `docker compose -f docker-compose.dev.multiuser.yml exec -T db sh -c ` +
        `'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" -N "$MYSQL_DATABASE"'`,
      {
        cwd: path.resolve(__dirname, "../.."),
        stdio: ["pipe", "pipe", "pipe"],
        input: `UPDATE users SET attributes = JSON_SET(
             COALESCE(attributes, JSON_OBJECT()),
             '$.remote_bucket_name', 'e2e-local-placeholder')
           WHERE email = '${email.replace(/'/g, "''")}';`,
      },
    )
  } catch {
    // Not a local stack
  }
}

// Reload-poll the dataview until it lists `min` data rows; each attempt
// must WAIT for the grid to render — counting right after reload races the
// page's loading state
async function waitForDataRows(page: Page, wsId: number, min: number) {
  await page.goto(`/dataview/${wsId}`)
  await expect(async () => {
    await page.reload()
    await expect(
      page.locator('[role="grid"] [role="row"]').nth(min),
    ).toBeVisible({ timeout: 8_000 })
  }).toPass({ timeout: 90_000 })
}

let recordsMinted = false
async function ensureDataviewRows(page: Page): Promise<number> {
  const id = await ensureWorkspaceId(page, DATA_WS)
  if (!recordsMinted) {
    // Read the record count from the list response — counting grid rows
    // right after the container renders races the data fetch and causes
    // spurious re-mints
    const listSeen = page.waitForResponse((r) =>
      r.url().includes("/api/dataview"),
    )
    await page.goto(`/dataview/${id}`)
    const total = ((await (await listSeen).json()) as { total?: number }).total
    if ((total ?? 0) < 2) {
      await openWorkspace(page, DATA_WS)
      await ensureCompletedTutorialRun(page, DATA_WS, "Tutorial1")
      // The record is registered slightly AFTER "Workflow finished" — the
      // copy must wait for it, or it duplicates a not-yet-successful row
      // that the dataview never lists
      await waitForDataRows(page, id, 1)

      // Copy the success record; the copy keeps its success state in the DB
      await openWorkspace(page, DATA_WS)
      await page.locator('button[role="tab"]:has-text("Record")').click()
      const t1row = page
        .locator('tr:has([data-testid="reproduce-button"])')
        .filter({ has: page.getByText("Tutorial1", { exact: true }) })
        .first()
      await t1row.locator('input[type="checkbox"]').check()
      await page.locator('button:has-text("COPY")').click()
      await page.locator('[role="dialog"] button:has-text("copy")').click()
      await expect(
        page.getByText("Tutorial1_copy", { exact: true }).first(),
      ).toBeVisible({ timeout: 60_000 })

      await waitForDataRows(page, id, 2)
    }
    recordsMinted = true
  }
  return id
}

test.describe("Private Dataview", () => {
  test.use({ storageState: freeStorageState() })

  let dataviewId = 0

  test.beforeAll(() => {
    ensurePublishableAccount()
  })

  test.beforeEach(async ({ page }) => {
    skipWithoutCreds()
    // The first hook mints its rows with a real Tutorial1 run (the sample data
    // ships metadata YAML only, so snakemake recomputes). runTutorial's inner
    // wait is 840s, so a 600s budget here expired mid-run and reported the
    // timeout against this hook rather than against the run.
    if (!recordsMinted) test.setTimeout(900_000)
    await gotoDashboard(page)
    dataviewId = await ensureDataviewRows(page)
    await page.goto(`/dataview/${dataviewId}`)
    await expect(page.locator('[role="grid"]')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("DV-01 - Table displays with expected columns", async ({ page }) => {
    const headers = page.locator('[role="grid"] [role="columnheader"]')
    for (const name of [
      "ID",
      "Name",
      "Workspace",
      "Inputs",
      "Outputs",
      "Details",
      "Timestamp",
    ]) {
      await expect(headers.filter({ hasText: name }).first()).toBeVisible()
    }
  })

  test("DV-02 - Publish toggle shown per record", async ({ page }) => {
    test.skip(!(await hasDataRows(page)), "No experiment records")

    const headers = page.locator('[role="grid"] [role="columnheader"]')
    await expect(headers.filter({ hasText: "Publish" }).first()).toBeVisible()
    await expect(
      page
        .locator(
          '[role="grid"] input[type="checkbox"], [role="grid"] .MuiSwitch-root',
        )
        .first(),
    ).toBeVisible()
  })

  // The grid filters server-side via per-column menus (no global search
  // box): header menu → Filter → debounced value input
  async function filterByColumn(page: Page, field: string, value: string) {
    const header = page.locator(
      `.MuiDataGrid-columnHeader[data-field="${field}"]`,
    )
    await header.hover()
    await header.locator(".MuiDataGrid-menuIcon button").click()
    await page.getByRole("menuitem", { name: /^filter$/i }).click()
    await page.locator(".MuiDataGrid-filterForm input").last().fill(value)
    await expect(async () => {
      const rows = await page.locator('[role="grid"] [role="row"]').count()
      expect(rows).toBe(2) // header + 1 match
    }).toPass({ timeout: 15_000 })
    await page.keyboard.press("Escape")
  }

  test("DV-03 - Filter by ID via the column menu", async ({ page }) => {
    test.skip(!(await hasDataRows(page)), "No experiment records")
    await filterByColumn(page, "uid", "tutorial1")
    await expect(
      page.locator('.MuiDataGrid-cell[data-field="uid"]').first(),
    ).toHaveText("tutorial1")
  })

  test("DV-13 - Filter by name via the column menu", async ({ page }) => {
    test.skip(!(await hasDataRows(page)), "No experiment records")
    await filterByColumn(page, "name", "copy")
    await expect(
      page.locator('.MuiDataGrid-cell[data-field="name"]').first(),
    ).toHaveText("Tutorial1_copy")
  })

  test("DV-04 - Sort by column header", async ({ page }) => {
    test.skip(!(await hasDataRows(page)), "No experiment records")

    const timestampHeader = page
      .locator('[role="columnheader"]')
      .filter({ hasText: "Timestamp" })
      .first()
    await timestampHeader.click()
    await expect(
      timestampHeader.locator(
        '[data-testid="ArrowUpwardIcon"], [data-testid="ArrowDownwardIcon"]',
      ),
    ).toBeVisible({ timeout: 10_000 })
  })

  test("DV-05 - Change page size", async ({ page }) => {
    test.skip(!(await hasDataRows(page)), "No experiment records")

    // Custom pagination: a native <select name="limit"> (10/50/100)
    const limitSelect = page.locator('select[name="limit"]')
    const refetch = page.waitForResponse(
      (r) => r.url().includes("/api/dataview") && r.url().includes("limit=10"),
    )
    await limitSelect.selectOption("10")
    await refetch
    await expect(limitSelect).toHaveValue("10")
    await expect(page.locator('[role="grid"] [role="row"]').nth(1)).toBeVisible(
      { timeout: 15_000 },
    )
  })

  test("DV-06 - Inputs dialog opens", async ({ page }) => {
    test.skip(!(await hasDataRows(page)), "No experiment records")

    // The cell's click target is the thumbnail (a spinner while loading)
    // or the fallback icon when no thumbnail exists
    const cellinput = page
      .locator(
        '.MuiDataGrid-cell[data-field="input_data"] img, .MuiDataGrid-cell[data-field="input_data"] [data-testid="ImageIcon"]',
      )
      .first()
    await expect(cellinput).toBeVisible({ timeout: 30_000 })
    await cellinput.click()
    await expect(page.locator('[role="dialog"]')).toBeVisible({
      timeout: 10_000,
    })
  })

  test("DV-07 - Outputs dialog opens", async ({ page }) => {
    test.skip(!(await hasDataRows(page)), "No experiment records")

    // The cell's click target is the thumbnail (a spinner while loading)
    // or the fallback icon when no thumbnail exists
    const celloutput = page
      .locator(
        '.MuiDataGrid-cell[data-field="output_data"] img, .MuiDataGrid-cell[data-field="output_data"] [data-testid="ImageIcon"]',
      )
      .first()
    await expect(celloutput).toBeVisible({ timeout: 30_000 })
    await celloutput.click()
    await expect(page.locator('[role="dialog"]')).toBeVisible({
      timeout: 10_000,
    })
  })

  test("DV-08 - Details dialog opens and closes", async ({ page }) => {
    test.skip(!(await hasDataRows(page)), "No experiment records")

    await page
      .locator(
        '.MuiDataGrid-cell[data-field="details"] button, .MuiDataGrid-cell[data-field="details"] svg',
      )
      .first()
      .click()
    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible({ timeout: 10_000 })

    await page.keyboard.press("Escape")
    await expect(dialog).toBeHidden({ timeout: 5_000 })
    await expect(page.locator('[role="grid"]')).toBeVisible()
  })

  test("DV-12 - Records show image and ROI thumbnails", async ({ page }) => {
    // Both thumbnails render: image plot and ROI plot
    await expect(
      page.locator('[role="grid"] [role="row"] img').first(),
    ).toBeVisible({ timeout: 15_000 })
    await expect(
      page.locator('[role="grid"] [role="row"] img').nth(1),
    ).toBeVisible()
  })

  // Exact text match — "Tutorial1" is a substring of "Tutorial1_copy"
  const rowByName = (page: Page, name: string) =>
    page
      .locator('[role="row"]')
      .filter({ has: page.getByText(name, { exact: true }) })
  const publishSwitch = (page: Page, name: string) =>
    rowByName(page, name).locator(
      '[data-field="publish_status"] input[type="checkbox"]',
    )

  test("DV-14 - Publish lists the record publicly; unpublish removes it", async ({
    page,
  }) => {
    // Publish Tutorial1 from its toggle (no confirmation for single records)
    await expect(publishSwitch(page, "Tutorial1")).not.toBeChecked()
    await rowByName(page, "Tutorial1")
      .locator('[data-field="publish_status"]')
      .click()
    await expect(publishSwitch(page, "Tutorial1")).toBeChecked({
      timeout: 15_000,
    })

    // Listed on the public dataview (S3 sync stays manual — the listing
    // gates on publish_status only)
    await page.goto("/public")
    await expect(
      page
        .locator('.MuiDataGrid-cell[data-field="name"]')
        .getByText("Tutorial1", { exact: true }),
    ).toBeVisible({ timeout: 15_000 })

    // Unpublish removes it from the public page
    await page.goto(`/dataview/${dataviewId}`)
    await rowByName(page, "Tutorial1")
      .locator('[data-field="publish_status"]')
      .click()
    await expect(publishSwitch(page, "Tutorial1")).not.toBeChecked({
      timeout: 15_000,
    })
    await page.goto("/public")
    await expect(page.locator('[role="grid"]')).toBeVisible({
      timeout: 15_000,
    })
    await expect(
      page
        .locator('.MuiDataGrid-cell[data-field="name"]')
        .getByText("Tutorial1", { exact: true }),
    ).toBeHidden()
  })

  test("DV-15 - Bulk publish and unpublish with confirmation", async ({
    page,
  }) => {
    // Select all records via the header check-all
    await page
      .locator('.MuiDataGrid-columnHeader[data-field="checkbox"] input')
      .check()

    // Bulk publish: confirmation dialog, then every toggle flips on
    await page.locator('button:has([data-testid="PublicIcon"])').click()
    const confirm = page.locator('[role="dialog"]:has-text("Bulk Publish")')
    await expect(confirm).toBeVisible({ timeout: 10_000 })
    await confirm.getByRole("button", { name: "ok" }).click()
    await expect(publishSwitch(page, "Tutorial1")).toBeChecked({
      timeout: 15_000,
    })
    await expect(publishSwitch(page, "Tutorial1_copy")).toBeChecked()

    // Bulk unpublish the same selection
    await page
      .locator('.MuiDataGrid-columnHeader[data-field="checkbox"] input')
      .check()
    await page.locator('button:has([data-testid="PublicOffIcon"])').click()
    const unconfirm = page.locator('[role="dialog"]:has-text("Bulk UnPublish")')
    await expect(unconfirm).toBeVisible({ timeout: 10_000 })
    await unconfirm.getByRole("button", { name: "ok" }).click()
    await expect(publishSwitch(page, "Tutorial1")).not.toBeChecked({
      timeout: 15_000,
    })
    await expect(publishSwitch(page, "Tutorial1_copy")).not.toBeChecked()
  })
})

test.describe("Public Dataview", () => {
  test("DV-09 - Public dataview while logged in hides Publish", async ({
    page,
  }) => {
    skipWithoutCreds()
    await login(page)
    await page.goto("/public")

    await expect(
      page.locator("text=OptiNiSt Public Repository").first(),
    ).toBeVisible({ timeout: 15_000 })

    const headers = page.locator('[role="grid"] [role="columnheader"]')
    if (
      await headers
        .first()
        .isVisible()
        .catch(() => false)
    ) {
      await expect(headers.filter({ hasText: "Publish" })).toHaveCount(0)
    }
  })

  test("DV-10 - Public dataview loads without authentication", async ({
    page,
  }) => {
    const response = await page.goto("/public")
    expect(response?.status()).toBe(200)
    await expect(
      page.locator("text=OptiNiSt Public Repository").first(),
    ).toBeVisible({ timeout: 15_000 })
    await expect(page).not.toHaveURL(/\/login/)
  })

  test("DV-11 - Public API is open, private API rejects a bad token", async ({
    page,
  }) => {
    const pub = await page.request.get(
      `${apiUrl()}/api/public/dataview?limit=5`,
    )
    expect(pub.status()).toBe(200)

    const priv = await page.request.get(`${apiUrl()}/api/dataview?limit=5`, {
      headers: { Authorization: "Bearer invalid-token" },
    })
    expect([401, 403]).toContain(priv.status())
  })
})

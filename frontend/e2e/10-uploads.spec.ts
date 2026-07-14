import * as fs from "fs"
import * as path from "path"

import { test, expect, Page } from "@playwright/test"

import {
  skipWithoutCreds,
  freeStorageState,
  gotoDashboard,
  openWorkspace,
  ensureTutorialRecords,
  reproduceTutorial,
  DATA_WS,
} from "./helpers"

// Input-node file dialogs and uploads. Reproduce loads a tutorial whose input
// nodes already carry file paths, which is what surfaces the CSV Settings and
// HDF5 Structure buttons. Uploads reuse the committed sample_data fixtures,
// sent under a unique name so the assertion proves the upload (not the file
// the tutorial import already placed).

const SAMPLE = path.join(__dirname, "..", "..", "sample_data")
const IMAGE_FIXTURE = path.join(SAMPLE, "dev_mouse2p_short_image.tiff")
const HDF5_FIXTURE = path.join(SAMPLE, "tutorial", "input", "sample_hdf5.h5")

// Upload a fixture through a node's hidden file input under a unique name,
// then confirm it lands in the workspace inputs (shows in the select dialog).
async function uploadAndVerify(
  page: Page,
  nodeClass: string,
  fixture: string,
  uniqueName: string,
  mimeType: string,
) {
  const node = page.locator(nodeClass)
  const [response] = await Promise.all([
    page.waitForResponse((r) => /\/files\/\d+\/upload\//.test(r.url()), {
      timeout: 120_000,
    }),
    node
      .locator('input[type="file"]')
      .setInputFiles({
        name: uniqueName,
        mimeType,
        buffer: fs.readFileSync(fixture),
      }),
  ])
  expect(response.ok()).toBeTruthy()

  // "Appears in inputs": open Select-from-uploaded-files and filter to it
  await node.locator('button:has([data-testid="ChecklistRtlIcon"])').click()
  const dialog = page.locator('[role="dialog"]')
  await expect(dialog).toBeVisible({ timeout: 15_000 })
  await dialog
    .locator('[placeholder="Filter... (* as wildcard)"]')
    .fill(uniqueName)
  await expect(dialog.locator(`text=${uniqueName}`).first()).toBeVisible({
    timeout: 15_000,
  })
}

test.describe("Input node dialogs and uploads", () => {
  test.use({ storageState: freeStorageState() })

  test.beforeEach(async ({ page }) => {
    test.setTimeout(240_000)
    skipWithoutCreds()
    await gotoDashboard(page)
    await openWorkspace(page, DATA_WS)
    await ensureTutorialRecords(page, DATA_WS)
  })

  test("UPL-01 - CSV node parameter dialog shows transpose/header/index", async ({
    page,
  }) => {
    // Tutorial1's behavior node reads a CSV, so it carries the CSV Settings
    // button. Target the node that renders it rather than a node-type class.
    await reproduceTutorial(page, "Tutorial1")
    const settings = page.locator(
      '.react-flow__node [data-testid="SettingsIcon"]',
    )
    await expect(settings.first()).toBeVisible({ timeout: 15_000 })

    await settings.first().click()
    const dialog = page.locator('[role="dialog"]:has-text("Csv Setting")')
    await expect(dialog).toBeVisible({ timeout: 30_000 })
    await expect(dialog.locator("text=Transpose")).toBeVisible()
    await expect(dialog.locator("text=Set Header")).toBeVisible()
    await expect(dialog.locator("text=Set Index")).toBeVisible()
  })

  test("UPL-02 - HDF5 node structure dialog shows the tree", async ({
    page,
  }) => {
    // Tutorial4 has an HDF5 node with a file path -> Structure button
    await reproduceTutorial(page, "Tutorial4")
    const hdf5Node = page.locator(".react-flow__node-HDF5FileNode")
    await expect(hdf5Node).toBeVisible({ timeout: 15_000 })

    await hdf5Node.locator('[data-testid="AccountTreeIcon"]').click()
    const dialog = page.locator('[role="dialog"]:has-text("Select Structure")')
    await expect(dialog).toBeVisible({ timeout: 30_000 })
    // Tree loads from the file; at least one entry appears
    await expect(dialog.locator('[role="treeitem"]').first()).toBeVisible({
      timeout: 30_000,
    })
  })

  test("UPL-03 - Upload an image file appears in inputs", async ({ page }) => {
    await reproduceTutorial(page, "Tutorial1")
    await expect(page.locator(".react-flow__node-ImageFileNode")).toBeVisible({
      timeout: 15_000,
    })

    await uploadAndVerify(
      page,
      ".react-flow__node-ImageFileNode",
      IMAGE_FIXTURE,
      `e2e_upload_${Date.now()}.tiff`,
      "image/tiff",
    )
  })

  test("UPL-04 - Upload an HDF5 file appears in inputs", async ({ page }) => {
    await reproduceTutorial(page, "Tutorial4")
    await expect(page.locator(".react-flow__node-HDF5FileNode")).toBeVisible({
      timeout: 15_000,
    })

    await uploadAndVerify(
      page,
      ".react-flow__node-HDF5FileNode",
      HDF5_FIXTURE,
      `e2e_upload_${Date.now()}.hdf5`,
      "application/x-hdf5",
    )
  })
})

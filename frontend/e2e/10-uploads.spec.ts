import * as fs from "fs"
import * as path from "path"

import { test, expect, Locator, Page } from "@playwright/test"

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
const MAT_FIXTURE = path.join(SAMPLE, "tutorial", "input", "sample_matlab.mat")

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
    node.locator('input[type="file"]').setInputFiles({
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

// Both sample_hdf5.h5 and sample_matlab.mat carry the same two datasets under
// a "data" group, so one table describes either file's tree. Asserting the
// shapes and sizes is what separates "the tree rendered" from "the tree
// rendered this file".
const SAMPLE_DATASETS = [
  ["behavior", "(500, 8)", "32.0 KB"],
  ["image", "(500, 128, 128)", "16.4 MB"],
] as const

async function expectSampleStructureTree(dialog: Locator) {
  await expect(dialog.locator('[role="treeitem"]')).toHaveCount(
    SAMPLE_DATASETS.length + 1,
  )
  await expect(dialog.locator('[role="treeitem"][id$="-data"]')).toBeVisible()
  for (const [name, shape, nbytes] of SAMPLE_DATASETS) {
    const row = dialog.locator(`[role="treeitem"][id$="-data/${name}"]`)
    await expect(row, `the ${name} dataset row`).toContainText(name)
    await expect(row).toContainText("array")
    await expect(row).toContainText(shape)
    await expect(row).toContainText(nbytes)
  }
}

// Move the selection to a dataset that is not the one the tutorial arrived
// with, and require the change to reach the node. Both file types drive the
// same dialog, so the round-trip is written once.
async function selectAnotherStructurePath(
  dialog: Locator,
  node: Locator,
): Promise<string> {
  await expect(dialog.getByText("Selected Path")).toBeVisible()
  const selectedPath = dialog.locator("h6").first()
  const before = await selectedPath.textContent()
  // "---" is the placeholder, and it is truthy; without this the test would
  // silently weaken to "the placeholder was replaced"
  expect(before?.trim(), "no selected-path text to compare").toBeTruthy()
  expect(before?.trim()).not.toBe("---")

  await dialog.locator('input[type="checkbox"]:not(:checked)').first().check()
  await expect(selectedPath).not.toHaveText(before ?? "")
  const chosen = (await selectedPath.textContent())?.trim() ?? ""
  expect(chosen).toBeTruthy()

  // OK writes the choice back onto the node
  await dialog.getByRole("button", { name: "OK" }).click()
  await expect(dialog).toBeHidden({ timeout: 15_000 })
  // The node renders two .selectFilePath captions - the file path and the
  // structure path; the arrow prefix is the structure one
  await expect(
    node.locator(".selectFilePath").filter({ hasText: "↳" }),
  ).toHaveText(`↳ ${chosen}`, { timeout: 15_000 })
  return chosen
}

// Tick one named dataset (whatever the record arrived with) and write it
// back onto the node.
async function selectStructurePath(
  dialog: Locator,
  node: Locator,
  datasetPath: string,
) {
  const checkbox = dialog
    .locator(`[role="treeitem"][id$="-${datasetPath}"]`)
    .locator('input[type="checkbox"]')
    .first()
  if (!(await checkbox.isChecked())) await checkbox.check()
  await dialog.getByRole("button", { name: "OK" }).click()
  await expect(dialog).toBeHidden({ timeout: 15_000 })
  await expect(
    node.locator(".selectFilePath").filter({ hasText: "↳" }),
  ).toHaveText(`↳ ${datasetPath}`, { timeout: 15_000 })
}

async function openStructureDialog(page: Page, nodeClass: string) {
  const node = page.locator(nodeClass)
  await expect(node).toBeVisible({ timeout: 15_000 })
  await node.locator('[data-testid="AccountTreeIcon"]').click()
  const dialog = page.locator('[role="dialog"]:has-text("Select Structure")')
  await expect(dialog.locator('[role="treeitem"]').first()).toBeVisible({
    timeout: 30_000,
  })
  return { node, dialog }
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
    await expect(dialog.locator('[role="treeitem"]').first()).toBeVisible({
      timeout: 30_000,
    })
    await expectSampleStructureTree(dialog)
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

  // Tutorial4 carries the MAT input node as well as the HDF5 one
  test("UPL-05 - An uploaded MAT file appears in inputs and can be read", async ({
    page,
  }) => {
    await reproduceTutorial(page, "Tutorial4")
    const uniqueName = `e2e_upload_${Date.now()}.mat`
    await uploadAndVerify(
      page,
      ".react-flow__node-MatlabFileNode",
      MAT_FIXTURE,
      uniqueName,
      "application/octet-stream",
    )

    // Point the node at the file just uploaded, so the structure below is read
    // from it rather than from the copy the tutorial import placed
    const selectFile = page.locator('[role="dialog"]:has-text("Select File")')
    await selectFile.locator(`text=${uniqueName}`).first().click()
    await selectFile.getByRole("button", { name: "OK" }).click()
    // Repointing an input while a workflow is loaded asks to branch the run
    const confirmChange = page.locator(
      '[role="dialog"]:has-text("Change this parameter?")',
    )
    await expect(confirmChange).toBeVisible({ timeout: 15_000 })
    await confirmChange.getByRole("button", { name: "OK" }).click()
    await expect(selectFile).toBeHidden({ timeout: 15_000 })

    const structure = await openStructureDialog(
      page,
      ".react-flow__node-MatlabFileNode",
    )
    await expect(structure.dialog.getByText("Selected Path")).toBeVisible()
  })

  test("UPL-06 - MAT node structure dialog shows the tree", async ({
    page,
  }) => {
    await reproduceTutorial(page, "Tutorial4")
    const { dialog } = await openStructureDialog(
      page,
      ".react-flow__node-MatlabFileNode",
    )
    await expectSampleStructureTree(dialog)
  })

  test("UPL-07 - A data path inside the MAT file can be selected", async ({
    page,
  }) => {
    await reproduceTutorial(page, "Tutorial4")
    const { node: matNode, dialog } = await openStructureDialog(
      page,
      ".react-flow__node-MatlabFileNode",
    )

    // The dialog opens with Tutorial4's own matPath already ticked, so picking
    // that same leaf would assert nothing. Tick a different one and require the
    // selection to move: sample_matlab.mat carries both data/behavior and
    // data/image.
    await selectAnotherStructurePath(dialog, matNode)
  })

  test("UPL-08 - A data path inside the HDF5 file can be selected", async ({
    page,
  }) => {
    await reproduceTutorial(page, "Tutorial4")
    const { node: hdf5Node, dialog } = await openStructureDialog(
      page,
      ".react-flow__node-HDF5FileNode",
    )
    // Same round-trip on the file the HDF5 node reads: the tutorial arrives
    // with data/image ticked, so the selection has to move to data/behavior and
    // reach the node for this to mean anything.
    expect(await selectAnotherStructurePath(dialog, hdf5Node)).toBe(
      "data/behavior",
    )
  })

  // The HDF5 handle is typed by the rank of the selected dataset: a 2D
  // dataset cannot reach an ImageData input in either drag direction, a 3D
  // one can. Tutorial4 arrives wired data/image -> suite2p_file_convert.
  test("UPL-09 - HDF5 handle type follows the dataset and gates connections", async ({
    page,
  }) => {
    await reproduceTutorial(page, "Tutorial4")
    const hdf5Node = page.locator(".react-flow__node-HDF5FileNode")
    const hdf5Handle = hdf5Node.locator(".react-flow__handle")
    const convertHandle = page.locator(
      '.react-flow__node-AlgorithmNode:has-text("suite2p_file_convert") .react-flow__handle[data-handleid$="--image--ImageData"]',
    )
    const first = await openStructureDialog(
      page,
      ".react-flow__node-HDF5FileNode",
    )
    await selectStructurePath(first.dialog, hdf5Node, "data/image")
    await expect(hdf5Handle).toHaveAttribute("title", "type: ImageData", {
      timeout: 30_000,
    })

    const edgeCount = await page.locator(".react-flow__edge").count()
    // Remove the shipped HDF5 -> suite2p_file_convert edge (the x on the edge)
    const hdf5Edge = page.locator(
      `.react-flow__edge[data-testid^="rf__edge-"][data-testid*="--hdf5--HDF5Data"]`,
    )
    await hdf5Edge.hover()
    await hdf5Edge.locator("button.flowbutton").click()
    await expect(page.locator(".react-flow__edge")).toHaveCount(edgeCount - 1)

    const second = await openStructureDialog(
      page,
      ".react-flow__node-HDF5FileNode",
    )
    await selectStructurePath(second.dialog, hdf5Node, "data/behavior")
    await expect(hdf5Handle).toHaveAttribute("title", "type: FluoData")

    await dragConnect(page, hdf5Handle, convertHandle)
    await expect(page.locator(".react-flow__edge")).toHaveCount(edgeCount - 1)
    await dragConnect(page, convertHandle, hdf5Handle)
    await expect(page.locator(".react-flow__edge")).toHaveCount(edgeCount - 1)

    const again = await openStructureDialog(
      page,
      ".react-flow__node-HDF5FileNode",
    )
    await selectStructurePath(again.dialog, hdf5Node, "data/image")
    await expect(hdf5Handle).toHaveAttribute("title", "type: ImageData")
    await dragConnect(page, hdf5Handle, convertHandle)
    await expect(page.locator(".react-flow__edge")).toHaveCount(edgeCount)
  })

  // A saved workflow can still carry a mismatched edge (or the tree may not
  // have loaded when the user connected), so the backend refuses it at
  // submission and the message reaches the snackbar.
  test("UPL-10 - Submitting a 2D dataset into an image input is refused with the reason", async ({
    page,
  }) => {
    await reproduceTutorial(page, "Tutorial4")
    const hdf5Node = page.locator(".react-flow__node-HDF5FileNode")
    const { dialog } = await openStructureDialog(
      page,
      ".react-flow__node-HDF5FileNode",
    )
    await selectStructurePath(dialog, hdf5Node, "data/behavior")

    const runResponse = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        /\/run\//.test(r.url()) &&
        !r.url().includes("/run/result"),
    )
    await page.locator('button:has([data-testid="ArrowDropDownIcon"])').click()
    await page.locator('li:text-is("RUN")').click()
    await page.locator('button:text-is("RUN")').click()
    const response = await runResponse
    expect(response.status()).toBe(422)
    const detail = (await response.json()).detail as string
    expect(detail).toContain("data/behavior")
    expect(detail).toContain("suite2p_file_convert.image expects ImageData")
    await expect(page.getByText(detail)).toBeVisible({ timeout: 15_000 })
  })
})

// React Flow connects on pointer events, so drive the mouse from the centre
// of one handle to the centre of the other.
async function dragConnect(page: Page, from: Locator, to: Locator) {
  const a = await from.boundingBox()
  const b = await to.boundingBox()
  if (!a || !b) throw new Error("handle not visible")
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 })
  await page.mouse.up()
}

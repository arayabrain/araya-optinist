import { test, expect } from "@playwright/test"

import {
  skipWithoutCreds,
  freeStorageState,
  gotoDashboard,
  openWorkspace,
  importSampleData,
  ensureTutorialRecords,
  reproduceTutorial,
  runTutorial,
  DATA_WS,
} from "./helpers"

// Workflow page: sample data import, reproduce, runs, validation, tabs.
// WF-04..06 are tagged @slow (RUN_SLOW=1 yarn test:e2e); WF-05/06 run the
// full pipeline (5-10 min each), WF-04's by-uid RUN finishes in seconds.
// Run-without-input-file validation needs manual node wiring — not automated.

test.describe("Workflow Execution", () => {
  test.use({ storageState: freeStorageState() })

  test.beforeEach(async ({ page }) => {
    skipWithoutCreds()
    await gotoDashboard(page)
  })

  test("WF-01 - Workflow page loads with workspace info", async ({ page }) => {
    await openWorkspace(page, DATA_WS)

    await expect(page.locator("text=Workspace").first()).toBeVisible()
    await expect(page.locator("text=ID").first()).toBeVisible()
    await expect(page.locator("text=Nodes").first()).toBeVisible()
  })

  test("WF-02 - Import sample data creates tutorial records", async ({
    page,
  }) => {
    test.setTimeout(180_000)
    await openWorkspace(page, DATA_WS)

    await importSampleData(page, DATA_WS)

    for (const name of ["Tutorial1", "Tutorial2", "Tutorial3", "Tutorial4"]) {
      await expect(page.locator(`tr:has-text("${name}")`).first()).toBeVisible({
        timeout: 30_000,
      })
    }
  })

  test("WF-03 - Reproduce workflow from record", async ({ page }) => {
    test.setTimeout(180_000)
    await openWorkspace(page, DATA_WS)
    await ensureTutorialRecords(page, DATA_WS)

    await reproduceTutorial(page, "Tutorial1")
    // Flowchart should contain nodes from the reproduced workflow
    await expect(page.locator(".react-flow__node").first()).toBeVisible({
      timeout: 15_000,
    })
  })

  // Both run-button paths get real coverage: RUN ALL executes the full
  // pipeline under a fresh uid; RUN reruns the reproduced uid (fast for the
  // imported tutorials since their outputs ship with the sample data)
  const RUNS: Array<[string, string, "RUN" | "RUN ALL"]> = [
    ["WF-04", "Tutorial1", "RUN"],
    ["WF-05", "Tutorial2", "RUN ALL"],
    ["WF-06", "Tutorial3", "RUN ALL"],
  ]
  for (const [id, tutorial, mode] of RUNS) {
    test(`${id} - Run ${tutorial} workflow via ${mode} @slow`, async ({
      page,
    }) => {
      test.setTimeout(900_000)
      await openWorkspace(page, DATA_WS)
      await ensureTutorialRecords(page, DATA_WS)

      await runTutorial(page, tutorial, mode)
    })
  }

  test("WF-07 - Run without algorithm nodes shows error", async ({ page }) => {
    await openWorkspace(page, "e2e-empty")

    await page.locator('button:has-text("RUN ALL")').first().click()
    // A fresh workspace has a default input node with no file selected, so
    // the input-file validation message wins over the algorithm-nodes one
    await expect(
      page.locator(
        "text=/please (add some algorithm nodes to the flowchart|select input file)/",
      ),
    ).toBeVisible({ timeout: 15_000 })
  })

  test("WF-08 - Rapid clicks produce one error snackbar, not duplicates", async ({
    page,
  }) => {
    await openWorkspace(page, "e2e-cooldown")

    // Guarded by the SnackbarProvider's preventDuplicate (verified by
    // toggling it off), NOT the run-request debounce — the validation path
    // never reaches it. The debounce on actual run POSTs stays manual.
    const runButton = page.locator('button:has-text("RUN ALL")').first()
    await runButton.click()
    await runButton.click({ force: true })
    await runButton.click({ force: true })

    await expect(
      page.locator(
        "text=/please (add some algorithm nodes to the flowchart|select input file)/",
      ),
    ).toHaveCount(1, { timeout: 15_000 })
  })

  test("WF-09 - Tab navigation between Workflow/Visualize/Record", async ({
    page,
  }) => {
    await openWorkspace(page, DATA_WS)

    for (const tab of ["Visualize", "Record", "Workflow"]) {
      await page.locator(`button[role="tab"]:has-text("${tab}")`).click()
      await expect(
        page.locator(`button[role="tab"]:has-text("${tab}")`),
      ).toHaveAttribute("aria-selected", "true", { timeout: 10_000 })
    }
  })
})

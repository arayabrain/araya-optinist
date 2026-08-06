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

// Visualize tab. VIS-01 asserts the sidebar info; VIS-02..05 drive the plot
// editor against the imported Tutorial1 outputs (the sample data ships with
// outputs, so no workflow run is needed). Left manual: Edit ROI commit (OK
// mutates the ROI data and kicks a processing run).

// MUI standard Select: the label's FormControl wraps the select div. The
// sidebar stacks one control group per plot box, so pick the box's group.
async function selectFromMui(
  page: Page,
  label: string,
  option: string,
  box: "first" | "last" = "first",
) {
  const select = page.locator(
    `div:has(> label:text-is("${label}")) .MuiSelect-select`,
  )
  await (box === "first" ? select.first() : select.last()).click()
  await page.getByRole("option", { name: option, exact: true }).click()
}

// Open Visualize, add a plot box, and select the sample TIFF into it
async function addImagePlot(page: Page) {
  await page.locator('button[role="tab"]:has-text("Visualize")').click()
  await page
    .locator('main main button:has([data-testid="AddIcon"])')
    .first()
    .click()
  await selectFromMui(page, "Select Item", "sample_mouse2p_image.tiff")
  await expect(page.locator(".js-plotly-plot").first()).toBeVisible({
    timeout: 60_000,
  })
}

test.describe("Visualize", () => {
  test.use({ storageState: freeStorageState() })

  test.beforeEach(async ({ page }) => {
    test.setTimeout(240_000)
    skipWithoutCreds()
    await gotoDashboard(page)
    await openWorkspace(page, DATA_WS)
    await ensureTutorialRecords(page, DATA_WS)
    // Load a known workflow into the store so the sidebar has data
    await reproduceTutorial(page, "Tutorial1")
  })

  test("VIS-01 - Visualize tab shows workspace and workflow info", async ({
    page,
  }) => {
    await page.locator('button[role="tab"]:has-text("Visualize")').click()
    await expect(
      page.locator('button[role="tab"]:has-text("Visualize")'),
    ).toHaveAttribute("aria-selected", "true", { timeout: 10_000 })

    // CurrentPipelineInfo sidebar: workspace NAME and workflow NAME rows
    await expect(page.locator("text=NAME").first()).toBeVisible({
      timeout: 15_000,
    })
    await expect(page.locator(`text=${DATA_WS}`).first()).toBeVisible()
    await expect(page.locator("text=Tutorial1").first()).toBeVisible()
  })

  test("VIS-02 - Add Cell ROI plot renders image with ROI overlay", async ({
    page,
  }) => {
    await addImagePlot(page)

    // `.js-plotly-plot` and `text=cell_roi` are both already true before the ROI
    // loads: addImagePlot awaited the plot, and `cell_roi` is the select's own
    // displayed value. ImagePlot gates rendering on the *image* error only, so a
    // failed getRoiData leaves the plot visible and both of those assertions
    // passing.
    //
    // Trace *count* is no good either: ImagePlot always builds a fixed two-trace
    // array ("images" then "roi"), with the roi trace's z starting as []. So the
    // ROI-specific artefact is that trace's z gaining rows.
    const roiRowCount = () =>
      page.evaluate(() => {
        const plot = document.querySelector(".js-plotly-plot") as unknown as {
          data?: { name?: string; z?: unknown[] }[]
        }
        return plot?.data?.find((t) => t.name === "roi")?.z?.length ?? 0
      })
    expect(await roiRowCount()).toBe(0)

    const roiResponse = page.waitForResponse(
      (r) => /\/api\/visualizations\/image\/.*roi/i.test(r.url()),
      { timeout: 60_000 },
    )
    await selectFromMui(page, "Select Roi", "cell_roi")
    expect((await roiResponse).status()).toBe(200)

    await expect.poll(roiRowCount, { timeout: 60_000 }).toBeGreaterThan(0)

    // Both selectors keep their values after the plot re-renders
    await expect(
      page.locator("text=sample_mouse2p_image.tiff").first(),
    ).toBeVisible()
    await expect(page.locator("text=cell_roi").first()).toBeVisible()
  })

  test("VIS-03 - Play advances image frames; Pause stops", async ({ page }) => {
    await addImagePlot(page)
    const frame = page.locator('input[type="range"]').first()
    const start = Number(await frame.inputValue())
    await page.getByRole("button", { name: "Play" }).click()
    // 500ms/frame default — the index must advance
    await expect
      .poll(async () => Number(await frame.inputValue()), { timeout: 15_000 })
      .toBeGreaterThan(start)
    await page.getByRole("button", { name: "Pause" }).click()
    const paused = Number(await frame.inputValue())
    await page.waitForTimeout(1_500)
    expect(Number(await frame.inputValue())).toBe(paused)
  })

  test("VIS-04 - Add a second plot of a different type", async ({ page }) => {
    await addImagePlot(page)
    // The next empty plot box gets a timeseries item
    await page
      .locator('main main button:has([data-testid="AddIcon"])')
      .first()
      .click()
    await selectFromMui(page, "Select Item", "fluorescence", "last")
    await expect(page.locator(".js-plotly-plot")).toHaveCount(2, {
      timeout: 60_000,
    })

    // Two plots is also what two image plots give. The image plot draws a
    // plotly heatmap and the timeseries one does not, so exactly one of the two
    // being a heatmap is where "a different type" is observable. (The
    // timeseries traces stay out of _fullData until a curve is selected, so
    // their own type is not assertable here.)
    const heatmapPlots = () =>
      page.evaluate(
        () =>
          Array.from(document.querySelectorAll(".js-plotly-plot")).filter(
            (el) =>
              (
                (el as unknown as { _fullData?: { type?: string }[] })
                  ._fullData ?? []
              ).some((trace) => trace.type === "heatmap"),
          ).length,
      )
    await expect.poll(heatmapPlots, { timeout: 60_000 }).toBe(1)
  })

  test("VIS-05 - Edit ROI opens the ROI editor; Cancel exits", async ({
    page,
  }) => {
    await addImagePlot(page)
    await selectFromMui(page, "Select Roi", "cell_roi")
    await page.getByText("Edit ROI", { exact: true }).click()

    for (const action of ["Add ROI", "Delete ROI", "Merge ROI"]) {
      await expect(page.getByText(action, { exact: true })).toBeVisible({
        timeout: 15_000,
      })
    }
    // Committing (OK) mutates the ROI data and starts a processing run —
    // that half stays manual; Cancel must leave the editor cleanly
    await page.getByText("Cancel", { exact: true }).first().click()
    await expect(page.getByText("Add ROI", { exact: true })).toBeHidden()
  })
})

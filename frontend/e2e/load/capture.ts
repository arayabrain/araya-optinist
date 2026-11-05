import {
  Browser,
  BrowserContext,
  expect,
  Page,
  Request,
} from "@playwright/test"

import { dismissStorageWarning, login } from "../helpers"

import { Account } from "./config"
import { CaptureRow } from "./screens"

// The requests a real browser sends for each screen, recorded at the start of
// a run so the replay follows today's frontend. Rows carry method, path,
// type, status and timing; only the anonymous capture keeps request headers
// (no cookie), since the signed-in ones would carry tokens.

// Quiet for this long with nothing in flight: the screen has finished loading
const QUIET_MS = 2_000
const SCREEN_TIMEOUT_MS = 120_000

type Recorder = {
  rows: CaptureRow[]
  setPhase: (phase: string) => void
  settled: () => Promise<void>
}

function record(
  context: BrowserContext,
  base: URL,
  withHeaders: boolean,
): Recorder {
  const rows: CaptureRow[] = []
  const phaseOf = new WeakMap<Request, string>()
  let phase = "start"
  let inflight = 0
  let pendingRows = 0
  let lastActivity = Date.now()
  context.on("request", (req) => {
    // The phase the request started in, not the one it finished in
    phaseOf.set(req, phase)
    inflight++
    lastActivity = Date.now()
  })
  const finish = async (req: Request, failed?: string) => {
    pendingRows++
    try {
      const url = new URL(req.url())
      const res = failed ? null : await req.response()
      const row: CaptureRow = {
        phase: phaseOf.get(req) ?? phase,
        method: req.method(),
        // Another origin (fonts, analytics) keeps its whole URL, so it never
        // reads as one of the app's own paths
        path: url.origin === base.origin ? url.pathname + url.search : url.href,
        type: req.resourceType(),
        status: res ? res.status() : null,
      }
      if (failed) row.failed = failed
      if (withHeaders && !failed) {
        const headers = await req.allHeaders()
        delete headers.cookie
        row.request_headers = headers
      }
      rows.push(row)
    } finally {
      pendingRows--
      inflight--
      lastActivity = Date.now()
    }
  }
  context.on("requestfinished", (req) => void finish(req))
  context.on(
    "requestfailed",
    (req) => void finish(req, req.failure()?.errorText ?? "failed"),
  )
  return {
    rows,
    setPhase: (next) => {
      phase = next
    },
    settled: () =>
      expect
        .poll(
          () =>
            inflight === 0 &&
            pendingRows === 0 &&
            Date.now() - lastActivity > QUIET_MS,
          {
            timeout: SCREEN_TIMEOUT_MS,
            message: "the screen never stopped loading",
          },
        )
        .toBe(true),
  }
}

async function newContext(
  browser: Browser,
  base: URL,
): Promise<BrowserContext> {
  const context = await browser.newContext({ baseURL: base.origin })
  // The analytics consent notice would front the UI in a build with GTM
  await context.addInitScript(() =>
    localStorage.setItem("analyticsConsent", "denied"),
  )
  return context
}

// A storage-limit modal fronts the grid for an account over its quota
async function clearModals(page: Page): Promise<void> {
  await dismissStorageWarning(page)
  const dialog = page.locator('[role="dialog"]')
  for (let i = 0; i < 3 && (await dialog.count()) > 0; i++) {
    await page.keyboard.press("Escape")
    await expect(dialog)
      .toHaveCount(0, { timeout: 2_000 })
      .catch(() => undefined)
  }
}

const OUTPUT_CELL =
  '.MuiDataGrid-cell[data-field="output_data"] img, ' +
  '.MuiDataGrid-cell[data-field="output_data"] [data-testid="ImageIcon"]'

// The grid's row id is the record's primary key, so this is exact
function recordRow(page: Page, recordId: number) {
  return page.locator(`.MuiDataGrid-row[data-id="${recordId}"]`)
}

async function openOutputs(
  page: Page,
  recordId: number,
  rec: Recorder,
): Promise<void> {
  await recordRow(page, recordId).locator(OUTPUT_CELL).first().click()
  const dialog = page.locator('[role="dialog"]')
  // Cold, the record first comes down from S3: tens of seconds
  await expect(dialog.getByText("Workflow Outputs")).toBeVisible({
    timeout: SCREEN_TIMEOUT_MS,
  })
  await rec.settled()
}

async function closeOutputs(page: Page, rec: Recorder): Promise<void> {
  await page.keyboard.press("Escape")
  await expect(page.locator('[role="dialog"]')).toHaveCount(0, {
    timeout: 10_000,
  })
  await rec.settled()
}

// The recorded open must reach the server as a first visit does: items the
// warm-up fetched could otherwise come from the browser's cache
async function clearCache(context: BrowserContext, page: Page): Promise<void> {
  const cdp = await context.newCDPSession(page)
  await cdp.send("Network.clearBrowserCache")
  await cdp.detach()
}

// Sign in, open the top page in a new tab (a new tab runs the storage
// refresh; a sign-in through the form does not), then the record's output
// dialog from DataView. The first open warms the record on the instance
// (cold, it comes down from S3 first); the second is recorded as "dialog".
export async function captureOwner(
  browser: Browser,
  base: URL,
  owner: Account,
  workspaceId: number,
  recordId: number,
): Promise<CaptureRow[]> {
  const context = await newContext(browser, base)
  const rec = record(context, base, false)
  try {
    rec.setPhase("login")
    const page = await context.newPage()
    await login(page, owner.email, owner.password)
    await rec.settled()

    rec.setPhase("dashboard_new_tab")
    const tab = await context.newPage()
    await tab.goto("/dashboard")
    await rec.settled()

    rec.setPhase("dataview_list")
    await tab.goto(`/dataview/${workspaceId}`)
    await expect(recordRow(tab, recordId)).toBeVisible({ timeout: 60_000 })
    await rec.settled()
    await clearModals(tab)

    rec.setPhase("dialog_warmup")
    await openOutputs(tab, recordId, rec)
    await closeOutputs(tab, rec)

    await clearCache(context, tab)
    rec.setPhase("dialog")
    await openOutputs(tab, recordId, rec)
    return rec.rows
  } finally {
    await context.close()
  }
}

async function openPublicRecord(
  page: Page,
  recordId: number,
  rec: Recorder,
  filterTo: (page: Page) => Promise<void>,
): Promise<void> {
  await page.goto("/public")
  await expect(page.locator('[role="grid"] [role="row"]').nth(1)).toBeVisible({
    timeout: 60_000,
  })
  await rec.settled()
  await clearModals(page)
  if (!(await recordRow(page, recordId).isVisible())) {
    rec.setPhase("filter")
    await filterTo(page)
    await expect(recordRow(page, recordId)).toBeVisible({ timeout: 30_000 })
    await rec.settled()
  }
}

// An anonymous visitor: /public, then the record's output dialog. The record
// is found on the first page of the listing or, failing that, through the uid
// filter, whose own requests are marked and left out of the replay. One whole
// visit warms the record on the public service (a cold first open after
// publishing answers its thumbnails late, or 423); the second is recorded.
export async function capturePublic(
  browser: Browser,
  base: URL,
  recordId: number,
  filterTo: (page: Page) => Promise<void>,
): Promise<CaptureRow[]> {
  const context = await newContext(browser, base)
  const rec = record(context, base, true)
  try {
    const page = await context.newPage()
    rec.setPhase("warmup_list")
    await openPublicRecord(page, recordId, rec, filterTo)
    rec.setPhase("dialog_warmup")
    await openOutputs(page, recordId, rec)

    await clearCache(context, page)
    rec.setPhase("public_list")
    await openPublicRecord(page, recordId, rec, filterTo)
    rec.setPhase("dialog")
    await openOutputs(page, recordId, rec)
    return rec.rows
  } finally {
    await context.close()
  }
}

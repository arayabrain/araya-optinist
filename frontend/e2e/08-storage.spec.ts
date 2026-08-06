import { test, expect, Page } from "@playwright/test"

import {
  FREE_USER,
  PREMIUM_USER,
  login,
  mockPremiumAssignment,
  skipWithoutCreds,
} from "./helpers"

// Storage warnings on login (manual storage reload is covered by WS-04;
// over-limit/threshold states by 11-lifecycle on a local stack).
// Left manual: S3 verification, auto-refresh network tracing.

test("STO-01 - Free user under limit logs in without storage warning", async ({
  page,
}) => {
  skipWithoutCreds()
  // The modal is rendered from this response, so a hidden-modal assertion made
  // before it resolves passes for an over-quota user too
  const warningSeen = page.waitForResponse(
    (r) => r.url().endsWith("/storage-limit-alerts/limit-warning"),
    { timeout: 60_000 },
  )
  await page.goto("/login")
  await page.locator('[data-testid="email"]').fill(FREE_USER.email)
  await page.locator('[data-testid="password"]').fill(FREE_USER.password)
  await page.locator('[data-testid="button-submit"]').click()
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })

  // No alert for this account is the premise; the modal staying away is the
  // claim. Both are asserted so a broken premise cannot read as a pass.
  const warning = await (await warningSeen).json()
  expect(warning?.has_alert ?? false).toBe(false)
  // The dashboard having rendered is what makes the absence an absence:
  // `toBeHidden` resolves on its first poll for an element that has not been
  // given the chance to render yet.
  await expect(page.getByRole("link", { name: "Workspaces" })).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.locator("text=Storage Limit Exceeded")).toBeHidden()
})

// The endpoint answers `null` with a 200 for an account with no alert, so the
// negative above is also satisfied by a backend that says nothing at all. No
// free account can be put over quota on demand from the UI, so the direction
// that renders the modal is pinned on a fulfilled response instead.
const OVER_QUOTA_ALERT = {
  has_alert: true,
  alert_type: "storage",
  days_remaining: 7,
  excess_data_bytes: 1073741824,
  excess_data_gb: 1,
  storage_usage_bytes: 6442450944,
  storage_usage_gb: 6,
  storage_quota_bytes: 5368709120,
  storage_quota_gb: 5,
  deletion_date: "2099-01-01T00:00:00Z",
  message: "Storage limit exceeded",
}

test("STO-04 - An over-quota alert opens the storage modal on login", async ({
  page,
}) => {
  skipWithoutCreds()
  await page.route("**/storage-limit-alerts/limit-warning", (route) =>
    route.fulfill({ json: OVER_QUOTA_ALERT }),
  )
  // dismissWarning=false: the modal under test is the one login() would close
  await login(page, FREE_USER.email, FREE_USER.password, false)

  // Exact: the alert body repeats the title in lower case, and getByText is
  // case-insensitive without it
  const modal = page.locator('[role="dialog"]')
  await expect(
    modal.getByText("Storage Limit Exceeded", { exact: true }),
  ).toBeVisible({ timeout: 30_000 })
  await expect(
    modal.getByRole("button", { name: "Manage Files" }),
  ).toBeVisible()
})

// One literal, so a copy change cannot quietly defang the negative assertion
// in the other test
const DEDICATED_SNACKBAR = "Premium instance assigned successfully"
const PREPARING_SNACKBAR =
  "Please wait while your dedicated premium resource is being prepared"

const premiumShared = (page: Page) =>
  page.evaluate(() => localStorage.getItem("premium_shared"))

test("STO-02 - Premium login shows an assignment snackbar", async ({
  page,
}) => {
  skipWithoutCreds(PREMIUM_USER, "TEST_PREMIUM_EMAIL/TEST_PREMIUM_PASSWORD")

  // The real assignment flow depends on the backend's AWS access (with
  // credentials it fails into a fallback snackbar on local stacks; without
  // them it 500s silently), so it isn't assertable outside a deployed env
  // and stays a manual check there. Mock the assigned state everywhere and
  // verify the frontend announces it.
  await mockPremiumAssignment(page)
  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)

  await expect(page.locator(`text=${DEDICATED_SNACKBAR}`).first()).toBeVisible({
    timeout: 30_000,
  })
  await expect.poll(() => premiumShared(page)).toBe("false")
})

test("STO-03 - Premium login on shared resources records the fallback", async ({
  page,
}) => {
  skipWithoutCreds(PREMIUM_USER, "TEST_PREMIUM_EMAIL/TEST_PREMIUM_PASSWORD")

  await mockPremiumAssignment(page, { shared: true })
  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)

  // The only state that distinguishes "landed on shared" from "still being
  // assigned": both show the same notice, so the notice alone proves nothing
  await expect.poll(() => premiumShared(page)).toBe("true")
  await expect(page.locator(`text=${PREPARING_SNACKBAR}`).first()).toBeVisible({
    timeout: 30_000,
  })
  // Announcing a dedicated instance while on shared resources is the bug this
  // guards; give the success effect a chance to fire before ruling it out
  await page.waitForTimeout(1_000)
  await expect(page.locator(`text=${DEDICATED_SNACKBAR}`)).toHaveCount(0)
})

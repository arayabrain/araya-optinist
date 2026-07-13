import { test, expect } from "@playwright/test"

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
  await page.goto("/login")
  await page.locator('[data-testid="email"]').fill(FREE_USER.email)
  await page.locator('[data-testid="password"]').fill(FREE_USER.password)
  await page.locator('[data-testid="button-submit"]').click()
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })

  await expect(page.locator("text=Storage Limit Exceeded")).toBeHidden()
})

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

  await expect(
    page.locator("text=Premium instance assigned successfully").first(),
  ).toBeVisible({ timeout: 30_000 })
})

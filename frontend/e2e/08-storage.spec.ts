import { test, expect } from "@playwright/test"

import { FREE_USER, PREMIUM_USER, login, skipWithoutCreds } from "./helpers"

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
  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)

  // Deployed (real ECS): assignment must succeed or be in progress — the
  // "Premium assignment issue" fallback is a failure there. On the local
  // stack (no ECS) the fallback is the expected outcome.
  const isLocal = /localhost|127\.0\.0\.1/.test(
    process.env.BASE_URL || "http://localhost:3000",
  )
  const accepted = isLocal
    ? /Premium instance assigned successfully|dedicated premium resource is being prepared|Premium assignment issue/
    : /Premium instance assigned successfully|dedicated premium resource is being prepared/
  await expect(page.locator(`text=${accepted}`).first()).toBeVisible({
    timeout: 30_000,
  })
})

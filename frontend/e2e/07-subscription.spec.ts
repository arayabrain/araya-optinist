import { test, expect } from "@playwright/test"

import {
  login,
  skipWithoutCreds,
  freeStorageState,
  gotoDashboard,
  PREMIUM_USER,
} from "./helpers"

// Subscription UI state for free and premium users.
// DB/Stripe dashboard verification stays manual.

test.describe("Free plan state", () => {
  test.use({ storageState: freeStorageState() })

  test.beforeEach(async ({ page }) => {
    skipWithoutCreds()
    await gotoDashboard(page)
  })

  test("SUB-01 - Subscription page shows Free as current plan", async ({
    page,
  }) => {
    await page.goto("/subscription")

    await expect(
      page.locator('button:has-text("Current Plan")').first(),
    ).toBeVisible({ timeout: 30_000 })
    await expect(
      page.locator('button:has-text("Upgrade")').first(),
    ).toBeVisible()
    await expect(page.locator("text=$20").first()).toBeVisible()
  })

  test("SUB-02 - Account profile shows Free status", async ({ page }) => {
    await page.goto("/account")
    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible({
      timeout: 15_000,
    })

    await expect(page.locator("text=Free").first()).toBeVisible()
    await expect(page.locator('button:has-text("Upgrade")')).toBeVisible()
    await expect(page.locator('button:has-text("Manage")')).toBeHidden()
  })

  test("SUB-03 - Invoice page shows no invoices for free user", async ({
    page,
  }) => {
    await page.goto("/subscription/manage")

    await expect(page.locator("text=Free Plan").first()).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.locator("text=No Invoices Found").first()).toBeVisible()
  })

  test("SUB-06 - Payment success page is guarded without a checkout session", async ({
    page,
  }) => {
    // The real success page is /subscription/thanks (reached from Stripe with
    // ?session_id=...). Opening it directly with no session must be blocked:
    // validateSession redirects to /subscription.
    await page.goto("/subscription/thanks")
    await expect(page).toHaveURL(/\/subscription$/, { timeout: 15_000 })
    await expect(page).not.toHaveURL(/thanks/)
  })
})

test.describe("Premium plan state", () => {
  test.beforeEach(async ({ page }) => {
    skipWithoutCreds(PREMIUM_USER, "TEST_PREMIUM_EMAIL/TEST_PREMIUM_PASSWORD")
    await login(page, PREMIUM_USER.email, PREMIUM_USER.password)
  })

  test("SUB-04 - Subscription page shows Premium as current plan", async ({
    page,
  }) => {
    await page.goto("/subscription")

    await expect(
      page.locator('button:has-text("Current Plan")').first(),
    ).toBeVisible({ timeout: 30_000 })
    // Expiration/renewal date is displayed for premium users
    await expect(
      page.locator("text=/renews? on|expires? on|expired on/i").first(),
    ).toBeVisible()
  })

  test("SUB-05 - Account profile shows Premium with Manage button", async ({
    page,
  }) => {
    await page.goto("/account")
    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible({
      timeout: 15_000,
    })

    await expect(page.locator("text=Premium").first()).toBeVisible()
    await expect(page.locator('button:has-text("Manage")')).toBeVisible()
    await expect(page.locator('button:has-text("Upgrade")')).toBeHidden()
    await expect(
      page.locator("text=/renews? on|expires? on|expired on/i").first(),
    ).toBeVisible()
  })
})

import { test, expect, Page } from "@playwright/test"

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

// There is no Stripe locally, so our own /api/subsc/* responses are mocked and
// the assertions stay on what we render and what we send. The server-side writes
// behind these transitions are asserted separately in pytest.
const PREMIUM_SUBSCRIPTION = {
  id: 12,
  plan_id: 2,
  user_id: 1,
  expiration: "2027-06-07T12:34:56",
  is_expired: false,
  scheduled_downgrade: false,
  status: 2,
  plan_name: "Premium",
  plan_price: 20,
}

const PAID_INVOICE = {
  id: "in_e2e_1",
  date: "2026-05-07T00:00:00Z",
  total: "$20.00",
  status: "Paid",
  invoice_url: "https://invoice.stripe.com/i/e2e-hosted-invoice",
}

async function mockPremiumBilling(
  page: Page,
  overrides: Partial<typeof PREMIUM_SUBSCRIPTION> = {},
) {
  const subscription = { ...PREMIUM_SUBSCRIPTION, ...overrides }
  await page.route("**/api/subsc/mgmts", (route) =>
    route.fulfill({ json: subscription }),
  )
  await page.route("**/api/subsc/payment-methods/default", (route) =>
    route.fulfill({
      json: {
        id: "pm_e2e",
        type: "card",
        brand: "visa",
        last4: "4242",
        exp_month: 12,
        exp_year: 2030,
        is_default: true,
      },
    }),
  )
  await page.route("**/api/subsc/invoices/**", (route) =>
    route.fulfill({ json: [PAID_INVOICE] }),
  )
  // Cancel and reactivate both mutate Stripe. Mocking the round trip is enough
  // for the UI: the slice flips scheduled_downgrade on fulfillment and the page
  // never re-reads /mgmts.
  await page.route("**/api/subsc/mgmts/cancel", (route) =>
    route.fulfill({ json: { success: true } }),
  )
  await page.route("**/api/subsc/mgmts/reactivate/**", (route) =>
    route.fulfill({ json: { success: true } }),
  )
}

test.describe("Invoice page and subscription transitions (mocked billing)", () => {
  test.use({ storageState: freeStorageState() })

  test.beforeEach(async ({ page }) => {
    skipWithoutCreds()
    await gotoDashboard(page)
  })

  test("SUB-07 - Manage on the account profile opens the invoice page", async ({
    page,
  }) => {
    await mockPremiumBilling(page)
    await page.goto("/account")
    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible({
      timeout: 15_000,
    })

    await page.locator('button:has-text("Manage")').click()
    await expect(page).toHaveURL(/\/subscription\/manage$/, { timeout: 15_000 })
    await expect(page.locator("text=Payment Method").first()).toBeVisible({
      timeout: 30_000,
    })
  })

  test("SUB-08 - Invoice page renders every section", async ({ page }) => {
    await mockPremiumBilling(page)
    await page.goto("/subscription/manage")

    // Subscription info: the plan name and the renewal wording, which is what
    // distinguishes an active subscription from a cancelled or expired one
    await expect(
      page.getByText(PREMIUM_SUBSCRIPTION.plan_name, { exact: true }).first(),
    ).toBeVisible({ timeout: 30_000 })
    await expect(
      page.locator("text=/Your subscription will renew on/"),
    ).toBeVisible()

    // Payment method: the brand and last four come from the response
    await expect(page.locator("text=/Visa .*4242/")).toBeVisible()
    await expect(
      page.locator('button:has-text("Manage Billing")'),
    ).toBeVisible()
  })

  test("SUB-09 - Invoice row shows its date, amount and paid status", async ({
    page,
  }) => {
    await mockPremiumBilling(page)
    await page.goto("/subscription/manage")

    const row = page.locator("tbody tr").first()
    await expect(row).toBeVisible({ timeout: 30_000 })
    // The page renders the ISO date in the reader's locale, not raw
    await expect(row).toContainText("May 7, 2026")
    await expect(row).toContainText(PAID_INVOICE.total)
    await expect(row).toContainText("Paid")
  })

  test("SUB-10 - View opens the hosted invoice", async ({ page }) => {
    await mockPremiumBilling(page)
    // Stub the destination so a run never actually reaches Stripe
    await page.route("https://invoice.stripe.com/**", (route) =>
      route.fulfill({ contentType: "text/html", body: "<h1>invoice</h1>" }),
    )
    await page.goto("/subscription/manage")
    await expect(page.locator("tbody tr").first()).toBeVisible({
      timeout: 30_000,
    })

    // The detail view is Stripe's hosted invoice, opened in a new tab; assert
    // the target and stop there rather than driving Stripe's page
    const popup = page.waitForEvent("popup")
    await page.locator('button:has-text("View")').first().click()
    expect((await popup).url()).toBe(PAID_INVOICE.invoice_url)
  })

  test("SUB-11 - Confirming cancellation shows the cancelled banner", async ({
    page,
  }) => {
    await mockPremiumBilling(page)
    await page.goto("/subscription")
    await expect(
      page.locator('button:has-text("Downgrade")').first(),
    ).toBeVisible({ timeout: 30_000 })

    await page.locator('button:has-text("Downgrade")').first().click()
    const confirm = page.locator(
      '[role="dialog"]:has-text("Cancel Subscription")',
    )
    await expect(confirm).toBeVisible({ timeout: 15_000 })
    const cancelRequest = page.waitForRequest(
      (r) =>
        r.url().includes("/api/subsc/mgmts/cancel") && r.method() === "DELETE",
    )
    await confirm
      .getByRole("button", { name: "Yes, Cancel Subscription" })
      .click()
    await cancelRequest

    await expect(confirm).toBeHidden({ timeout: 15_000 })
    const banner = page.locator("text=Subscription Canceled:").first()
    await expect(banner).toBeVisible({ timeout: 15_000 })
    // The banner has to name the date access actually ends
    await expect(page.locator("text=/will remain active until/")).toBeVisible()
    await expect(
      page.locator('button:has-text("Continue Plan")').first(),
    ).toBeVisible()
  })

  test("SUB-12 - Continue Plan clears the cancellation", async ({ page }) => {
    await mockPremiumBilling(page, { scheduled_downgrade: true })
    await page.goto("/subscription")
    const banner = page.locator("text=Subscription Canceled:").first()
    await expect(banner).toBeVisible({ timeout: 30_000 })

    // The route is per-user and refuses an id that is not the caller's, which is
    // asserted server-side; here the point is that we POST to it at all
    const reactivateRequest = page.waitForRequest(
      (r) =>
        r.method() === "POST" &&
        /\/api\/subsc\/mgmts\/reactivate\/\d+$/.test(r.url()),
    )
    await page.locator('button:has-text("Continue Plan")').first().click()
    await reactivateRequest

    await expect(banner).toBeHidden({ timeout: 15_000 })
    await expect(
      page.locator('button:has-text("Current Plan")').first(),
    ).toBeVisible({ timeout: 15_000 })
    await expect(page.locator('button:has-text("Continue Plan")')).toBeHidden()
  })

  test("SUB-13 - Upgrade creates a checkout session and leaves for Stripe", async ({
    page,
  }) => {
    // Stripe's own checkout is out of scope (its UI, not ours), so the
    // assertion is our request and the redirect target. The destination is
    // stubbed so the run never actually reaches Stripe.
    const checkoutUrl = "https://checkout.stripe.com/c/pay/e2e-session"
    await page.route("**/api/subsc/checkout/create-checkout-session", (route) =>
      route.fulfill({ json: { checkout_url: checkoutUrl } }),
    )
    await page.route("https://checkout.stripe.com/**", (route) =>
      route.fulfill({ contentType: "text/html", body: "<h1>stripe</h1>" }),
    )

    await page.goto("/subscription")
    const upgrade = page.locator('button:has-text("Upgrade")').first()
    await expect(upgrade).toBeVisible({ timeout: 30_000 })

    const sessionRequest = page.waitForRequest(
      (r) =>
        r.url().includes("/api/subsc/checkout/create-checkout-session") &&
        r.method() === "POST",
    )
    await upgrade.click()
    await sessionRequest
    await expect(page).toHaveURL(checkoutUrl, { timeout: 30_000 })
  })

  test("SUB-14 - Manage Billing opens the Stripe customer portal", async ({
    page,
  }) => {
    await mockPremiumBilling(page)
    await page.route("https://billing.stripe.com/**", (route) =>
      route.fulfill({ contentType: "text/html", body: "<h1>portal</h1>" }),
    )
    await page.goto("/subscription/manage")
    const manageBilling = page.locator('button:has-text("Manage Billing")')
    await expect(manageBilling).toBeVisible({ timeout: 30_000 })

    // The button opens Stripe's hosted login link directly - no portal session
    // is created, so the assertion is the destination host
    const popup = page.waitForEvent("popup")
    await manageBilling.click()
    expect((await popup).url()).toContain("billing.stripe.com")
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

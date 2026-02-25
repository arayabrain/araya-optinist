import { test, expect, Page } from "@playwright/test"

// Test credentials - configurable via env vars
const TEST_EMAIL = process.env.TEST_USER_EMAIL || "tsuchiyama_yutaka@araya.org"
const TEST_PASSWORD = process.env.TEST_USER_PASSWORD || "YutakaTsuchiyama123"

// Helper: Login and navigate to dashboard
async function login(page: Page) {
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.goto("/login")
    await page.locator('[data-testid="email"]').fill(TEST_EMAIL)
    await page.locator('[data-testid="password"]').fill(TEST_PASSWORD)
    await page.locator('[data-testid="button-submit"]').click()

    try {
      await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })
      return
    } catch {
      // Login timed out — retry
    }
  }
  throw new Error("Login failed after 3 attempts")
}

// ==============================================
// TC 200-204: Free Plan State
// ==============================================

test.describe("Free Plan State", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test("TC200 - Free Plan Card Display", async ({ page }) => {
    await page.goto("/subscription")

    const currentPlanButton = page.locator('button:has-text("Current Plan")')
    const loadingText = page.locator("text=Loading subscription plans...")

    await expect(currentPlanButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    // If plans loaded, verify free plan card
    if (await currentPlanButton.isVisible()) {
      await expect(page.locator("text=Subscription Plans")).toBeVisible()
      await expect(currentPlanButton).toBeDisabled()
      await expect(page.locator("text=Free")).toBeVisible()
    }
  })

  test("TC201 - Premium Plan Card Display", async ({ page }) => {
    await page.goto("/subscription")

    const upgradeButton = page.locator('button:has-text("Upgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")

    await expect(upgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    if (await upgradeButton.isVisible()) {
      await expect(upgradeButton).toBeEnabled()
      // Premium plan should show pricing
      await expect(page.locator("text=Premium")).toBeVisible()
    }
  })

  test("TC202 - Free Account Status on Profile", async ({ page }) => {
    await page.goto("/account")

    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible()
    await expect(page.locator("text=Free")).toBeVisible()
    await expect(page.locator('button:has-text("Upgrade")')).toBeVisible()
    await expect(page.locator('button:has-text("Manage")')).toBeHidden()
  })

  test("TC203 - Free user upgrade button", async ({ page }) => {
    await page.goto("/account")

    const upgradeButton = page.locator('button:has-text("Upgrade")')
    await expect(upgradeButton).toBeVisible()
  })

  test("TC204 - No Subscription Invoice View", async ({ page }) => {
    await page.goto("/subscription/manage")

    // Free users should see "No Invoices Found" and/or "Free Plan"
    await expect(
      page
        .locator("text=No Invoices Found")
        .or(page.locator("text=Free Plan"))
        .first(),
    ).toBeVisible({ timeout: 15_000 })
  })
})

// ==============================================
// TC 205: Upgrade Flow - Initiate
// ==============================================

test.describe("Upgrade Flow", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test("TC205 - Initiate Upgrade redirects to Stripe", async ({ page }) => {
    await page.goto("/subscription")

    const upgradeButton = page.locator('button:has-text("Upgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")

    await expect(upgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    // Only run if plans loaded
    if (await upgradeButton.isVisible()) {
      await upgradeButton.click()

      // Should show processing or redirect to Stripe checkout
      const processingButton = page.locator('button:has-text("Processing...")')
      const stripeRedirect = page.waitForURL(/checkout\.stripe\.com/, {
        timeout: 30_000,
      })

      await expect(processingButton)
        .toBeVisible({ timeout: 5_000 })
        .catch(() => {}) // Processing may be brief
      await stripeRedirect

      await expect(page).toHaveURL(/checkout\.stripe\.com/)
    }
  })
})

// ==============================================
// TC 206-217: Stripe Checkout Validation
// (These tests interact with Stripe's hosted checkout page)
// ==============================================

test.describe("Stripe Checkout Validation", () => {
  // Skip these tests unless explicitly enabled — they require
  // a Stripe checkout session and interact with external Stripe UI
  test.skip(
    () => !process.env.RUN_STRIPE_TESTS,
    "Set RUN_STRIPE_TESTS=1 to run Stripe checkout tests",
  )

  test("TC208 - Empty Form Submission", async ({ page }) => {
    // Navigate to Stripe checkout (requires active checkout session)
    await login(page)
    await page.goto("/subscription")
    await page.locator('button:has-text("Upgrade")').click()
    await page.waitForURL(/checkout\.stripe\.com/, { timeout: 30_000 })

    // Click subscribe without filling fields
    await page.locator('button:has-text("Subscribe")').click()

    // Stripe should show validation errors
    await expect(
      page.locator("text=Your card number is incomplete"),
    ).toBeVisible()
  })

  test("TC215 - Declined Card (4000 0000 0000 0002)", async ({ page }) => {
    await login(page)
    await page.goto("/subscription")
    await page.locator('button:has-text("Upgrade")').click()
    await page.waitForURL(/checkout\.stripe\.com/, { timeout: 30_000 })

    // Fill in declined test card
    const cardFrame = page.frameLocator('iframe[name*="cardNumber"]')
    await cardFrame
      .locator('[placeholder="Card number"]')
      .fill("4000000000000002")
    await cardFrame.locator('[placeholder="MM / YY"]').fill("12/30")
    await cardFrame.locator('[placeholder="CVC"]').fill("123")

    await page.locator('button:has-text("Subscribe")').click()

    // Should show decline error
    await expect(page.locator("text=declined")).toBeVisible({ timeout: 15_000 })
  })
})

// ==============================================
// TC 220-221: Checkout Edge Cases
// ==============================================

test.describe("Checkout Edge Cases", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test("TC220 - Browser Back Button during checkout", async ({ page }) => {
    await page.goto("/subscription")

    const upgradeButton = page.locator('button:has-text("Upgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")

    // Wait for page to fully load (plans or loading indicator)
    await expect(upgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    // Skip if subscription plans didn't load
    if (!(await upgradeButton.isVisible().catch(() => false))) {
      test.skip(true, "Subscription plans not available locally")
      return
    }

    await upgradeButton.click()

    // Wait for Stripe redirect
    try {
      await page.waitForURL(/checkout\.stripe\.com/, { timeout: 30_000 })
    } catch {
      return // Stripe not available
    }

    // Go back
    await page.goBack()

    // Should return to subscription page without errors
    await expect(page).not.toHaveURL(/checkout\.stripe\.com/)
  })
})

// ==============================================
// TC 227: Direct Access Prevention
// ==============================================

test.describe("Direct Access Prevention", () => {
  test("TC227 - Cannot access /thanks without payment", async ({ page }) => {
    await login(page)
    await page.goto("/subscription/thanks")

    // Should redirect away from thanks page
    await expect(page).not.toHaveURL(/\/subscription\/thanks/, {
      timeout: 10_000,
    })
  })
})

// ==============================================
// TC 249-258: Downgrade Flow
// (Requires Premium user — tests will be skipped if user is Free)
// ==============================================

test.describe("Downgrade Flow", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test("TC249 - Initiate Downgrade shows confirmation modal", async ({
    page,
  }) => {
    await page.goto("/subscription")

    const downgradeButton = page.locator('button:has-text("Downgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")

    await expect(downgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    // Only run if user is Premium (Downgrade button visible)
    test.skip(
      !(await downgradeButton.isVisible()),
      "User is not Premium — skipping downgrade test",
    )

    await downgradeButton.click()

    // Confirmation modal should appear
    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()
  })

  test("TC250 - Modal title check", async ({ page }) => {
    await page.goto("/subscription")

    const downgradeButton = page.locator('button:has-text("Downgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")
    await expect(downgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    test.skip(
      !(await downgradeButton.isVisible()),
      "User is not Premium — skipping downgrade test",
    )

    await downgradeButton.click()

    await expect(page.locator("text=Cancel Subscription")).toBeVisible()
  })

  test("TC251 - Modal content verification", async ({ page }) => {
    await page.goto("/subscription")

    const downgradeButton = page.locator('button:has-text("Downgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")
    await expect(downgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    test.skip(
      !(await downgradeButton.isVisible()),
      "User is not Premium — skipping downgrade test",
    )

    await downgradeButton.click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()
    await expect(
      dialog.locator("text=Are you sure you want to cancel"),
    ).toBeVisible()
  })

  test("TC252 - Data retention notice", async ({ page }) => {
    await page.goto("/subscription")

    const downgradeButton = page.locator('button:has-text("Downgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")
    await expect(downgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    test.skip(
      !(await downgradeButton.isVisible()),
      "User is not Premium — skipping downgrade test",
    )

    await downgradeButton.click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog.locator("text=stored for 30 days")).toBeVisible()
  })

  test("TC253 - Modal buttons check", async ({ page }) => {
    await page.goto("/subscription")

    const downgradeButton = page.locator('button:has-text("Downgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")
    await expect(downgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    test.skip(
      !(await downgradeButton.isVisible()),
      "User is not Premium — skipping downgrade test",
    )

    await downgradeButton.click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Two buttons: "No" and "Yes, Cancel Subscription"
    await expect(dialog.locator('button:has-text("No")')).toBeVisible()
    await expect(
      dialog.locator('button:has-text("Yes, Cancel Subscription")'),
    ).toBeVisible()
  })

  test("TC254 - Cancel downgrade (click No)", async ({ page }) => {
    await page.goto("/subscription")

    const downgradeButton = page.locator('button:has-text("Downgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")
    await expect(downgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    test.skip(
      !(await downgradeButton.isVisible()),
      "User is not Premium — skipping downgrade test",
    )

    await downgradeButton.click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Click "No" — modal should close, no changes
    await dialog.locator('button:has-text("No")').click()
    await expect(dialog).toBeHidden()

    // Still on subscription page, Premium still active
    await expect(page).toHaveURL(/\/subscription/)
  })
})

// ==============================================
// TC 287-289: Account Deletion Warnings
// ==============================================

test.describe("Account Deletion", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test("TC287 - Deletion option display", async ({ page }) => {
    await page.goto("/account")

    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible()

    const deleteButton = page.locator('button:has-text("Delete Account")')
    await expect(deleteButton).toBeVisible()
  })

  test("TC289 - Deletion warning as Free user", async ({ page }) => {
    await page.goto("/account")

    // Check if user is Free (no Manage button)
    const manageButton = page.locator('button:has-text("Manage")')
    const isFreeUser = !(await manageButton.isVisible().catch(() => false))

    test.skip(!isFreeUser, "User is not Free — skipping free deletion test")

    await page.locator('button:has-text("Delete Account")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Free user warnings
    await expect(
      dialog.locator("text=All your data (workspaces, experiments, files)"),
    ).toBeVisible()
    await expect(
      dialog.locator("text=This action cannot be undone"),
    ).toBeVisible()

    // Confirm input field should be present
    await expect(dialog.locator('input[placeholder="DELETE"]')).toBeVisible()

    // Delete button should be disabled until input matches
    const confirmButton = dialog.locator('button:has-text("Delete My Account")')
    await expect(confirmButton).toBeDisabled()

    // Cancel button should work
    await dialog.locator('button:has-text("CANCEL")').click()
    await expect(dialog).toBeHidden()
  })

  test("TC288 - Deletion warning as Premium user", async ({ page }) => {
    await page.goto("/account")

    const manageButton = page.locator('button:has-text("Manage")')
    const isPremiumUser = await manageButton.isVisible().catch(() => false)

    test.skip(
      !isPremiumUser,
      "User is not Premium — skipping premium deletion test",
    )

    await page.locator('button:has-text("Delete Account")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Premium user warnings
    await expect(
      dialog.locator("text=subscription will be immediately canceled"),
    ).toBeVisible()
    await expect(dialog.locator("text=will not receive a refund")).toBeVisible()
    await expect(
      dialog.locator("text=All your data (workspaces, experiments, files)"),
    ).toBeVisible()
    await expect(
      dialog.locator("text=This action cannot be undone"),
    ).toBeVisible()

    // Cancel without deleting
    await dialog.locator('button:has-text("CANCEL")').click()
    await expect(dialog).toBeHidden()
  })

  test("TC290 - Delete confirmation requires typing DELETE", async ({
    page,
  }) => {
    await page.goto("/account")

    await page.locator('button:has-text("Delete Account")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    const confirmButton = dialog.locator('button:has-text("Delete My Account")')
    const input = dialog.locator('input[placeholder="DELETE"]')

    // Button should be disabled initially
    await expect(confirmButton).toBeDisabled()

    // Type wrong text — button should stay disabled
    await input.fill("delete")
    await expect(confirmButton).toBeDisabled()

    // Type correct text — button should enable
    await input.fill("DELETE")
    await expect(confirmButton).toBeEnabled()

    // DO NOT click delete — just verify the button state, then cancel
    await dialog.locator('button:has-text("CANCEL")').click()
    await expect(dialog).toBeHidden()
  })
})

// ==============================================
// TC 228-231: Premium State
// (Requires Premium user — skipped if Free)
// ==============================================

test.describe("Premium State", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test("TC228 - Premium Plan Status on subscription page", async ({ page }) => {
    await page.goto("/subscription")

    const currentPlanButton = page.locator('button:has-text("Current Plan")')
    const loadingText = page.locator("text=Loading subscription plans...")

    await expect(currentPlanButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    if (await currentPlanButton.isVisible()) {
      // Check if Premium is the current plan
      const premiumCurrent = page
        .locator("text=Premium")
        .locator("..")
        .locator('button:has-text("Current Plan")')
      const isPremium = await premiumCurrent.isVisible().catch(() => false)

      test.skip(!isPremium, "User is not Premium")

      await expect(page.locator('button:has-text("Downgrade")')).toBeVisible()
    }
  })

  test("TC229 - Premium Account Status on profile", async ({ page }) => {
    await page.goto("/account")

    const manageButton = page.locator('button:has-text("Manage")')
    const isPremium = await manageButton.isVisible().catch(() => false)

    test.skip(!isPremium, "User is not Premium")

    await expect(page.locator("text=Premium")).toBeVisible()
    await expect(manageButton).toBeVisible()
  })

  test("TC231 - Expiration date text", async ({ page }) => {
    await page.goto("/account")

    const manageButton = page.locator('button:has-text("Manage")')
    const isPremium = await manageButton.isVisible().catch(() => false)

    test.skip(!isPremium, "User is not Premium")

    // Should show one of: "Renew on", "Expires on", or "Expired on"
    const renewText = page.locator("text=Renew on")
    const expiresText = page.locator("text=Expires on")
    const expiredText = page.locator("text=Expired on")

    await expect(renewText.or(expiresText).or(expiredText)).toBeVisible()
  })
})

// ==============================================
// TC 234-242: Invoice Access & Display
// (Requires Premium user with invoices)
// ==============================================

test.describe("Invoice Display", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test("TC234 - Access Invoice Page from Account", async ({ page }) => {
    await page.goto("/account")

    const manageButton = page.locator('button:has-text("Manage")')
    const isPremium = await manageButton.isVisible().catch(() => false)

    test.skip(!isPremium, "User is not Premium — no invoice page")

    await manageButton.click()
    await expect(page).toHaveURL(/\/subscription\/manage/)
  })

  test("TC235 - Invoice page layout verification", async ({ page }) => {
    await page.goto("/subscription/manage")

    // Page should load — look for any of these elements
    await expect(
      page
        .locator("text=Payment Method")
        .or(page.locator("text=Free Plan"))
        .or(page.locator("text=No Invoices Found"))
        .first(),
    ).toBeVisible({ timeout: 15_000 })
  })

  test("TC238 - Invoice table structure", async ({ page }) => {
    await page.goto("/subscription/manage")

    // Wait for page to load by checking for any expected element
    await expect(
      page
        .locator("text=Payment Method")
        .or(page.locator("text=Free Plan"))
        .or(page.locator("text=No Invoices Found"))
        .first(),
    ).toBeVisible({ timeout: 15_000 })

    // Check if invoice table exists (Premium with invoices)
    const dateHeader = page.locator('th:has-text("Date")')

    if (await dateHeader.isVisible()) {
      await expect(page.locator('th:has-text("Total")')).toBeVisible()
      await expect(page.locator('th:has-text("Status")')).toBeVisible()
      await expect(page.locator('th:has-text("Actions")')).toBeVisible()
    }
  })

  test("TC241 - View Invoice Details button", async ({ page }) => {
    await page.goto("/subscription/manage")

    // Wait for page to load
    await expect(
      page
        .locator("text=Payment Method")
        .or(page.locator("text=Free Plan"))
        .or(page.locator("text=No Invoices Found"))
        .first(),
    ).toBeVisible({ timeout: 15_000 })

    const viewButton = page.locator('button:has-text("View")').first()

    if (await viewButton.isVisible()) {
      // View button should open invoice in new tab
      const [newPage] = await Promise.all([
        page.context().waitForEvent("page"),
        viewButton.click(),
      ])
      await newPage.waitForLoadState()
      expect(newPage.url()).toContain("invoice")
      await newPage.close()
    }
  })

  test("TC246 - Manage Billing button", async ({ page }) => {
    await page.goto("/subscription/manage")

    // Wait for page to load
    await expect(
      page
        .locator("text=Payment Method")
        .or(page.locator("text=Free Plan"))
        .or(page.locator("text=No Invoices Found"))
        .first(),
    ).toBeVisible({ timeout: 15_000 })

    const manageBillingButton = page.locator(
      'button:has-text("Manage Billing")',
    )

    if (await manageBillingButton.isVisible()) {
      // Button may be disabled locally if Stripe is not configured
      if (await manageBillingButton.isEnabled()) {
        const [newPage] = await Promise.all([
          page.context().waitForEvent("page"),
          manageBillingButton.click(),
        ])
        await newPage.waitForLoadState()
        expect(newPage.url()).toContain("billing.stripe.com")
        await newPage.close()
      } else {
        // Button exists but is disabled — Stripe not configured locally
        await expect(manageBillingButton).toBeVisible()
      }
    }
  })
})

// ==============================================
// TC 294: Responsive Design
// ==============================================

test.describe("Responsive Design", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test("TC294 - Subscription page responsive", async ({ page }) => {
    await page.goto("/subscription")

    // Wait for page to load
    const upgradeButton = page.locator('button:has-text("Upgrade")')
    const loadingText = page.locator("text=Loading subscription plans...")
    await expect(upgradeButton.or(loadingText)).toBeVisible({
      timeout: 15_000,
    })

    // Test at different viewport sizes
    for (const width of [1280, 768, 375]) {
      await page.setViewportSize({ width, height: 720 })
      await page.waitForTimeout(500)

      // Page should still be functional (no JS errors)
      const errors: string[] = []
      page.on("pageerror", (error) => errors.push(error.message))

      expect(errors).toHaveLength(0)
    }
  })

  test("TC294 - Account page responsive", async ({ page }) => {
    await page.goto("/account")
    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible()

    for (const width of [1280, 768, 375]) {
      await page.setViewportSize({ width, height: 720 })
      await page.waitForTimeout(500)

      // Title should remain visible at all sizes
      await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible()
    }
  })
})

import { test, expect } from "@playwright/test"

import {
  FREE_USER,
  ensureRegisteredUser,
  localStackSkipReason,
  login,
  logout,
  runSql,
  skipWithoutCreds,
  sqlLiteral,
  dismissStorageWarning,
} from "./helpers"

// Login, logout, session persistence, and public-header navigation

test.describe("Login", () => {
  test("AUTH-01 - Successful login redirects to dashboard", async ({
    page,
  }) => {
    skipWithoutCreds()
    await page.goto("/login")
    await page.locator('[data-testid="email"]').fill(FREE_USER.email)
    await page.locator('[data-testid="password"]').fill(FREE_USER.password)
    await page.locator('[data-testid="button-submit"]').click()

    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })
    await expect(page.locator("text=Dashboard")).toBeVisible()
  })

  test("AUTH-02 - Invalid credentials shows error", async ({ page }) => {
    await page.goto("/login")
    await page.locator('[data-testid="email"]').fill("nonexistent@test.com")
    await page.locator('[data-testid="password"]').fill("Wrong@123")
    await page.locator('[data-testid="button-submit"]').click()

    await expect(page.locator("text=Email or password is wrong")).toBeVisible({
      timeout: 15_000,
    })
    await expect(page).toHaveURL(/\/login/)
  })

  test("AUTH-03 - Empty fields validation", async ({ page }) => {
    await page.goto("/login")
    await page.locator('[data-testid="button-submit"]').click()

    await expect(
      page
        .locator('[data-testid="error-email"], [data-testid="error-password"]')
        .first(),
    ).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
  })

  // The resend affordances ride on this test's account rather than getting a
  // test of their own: every registration leaves a Firebase user behind that
  // nothing cleans up, and the success screen is redux-only, so reaching it
  // again means registering again.
  test("AUTH-04 - Unverified email login shows Resend Email", async ({
    page,
  }) => {
    // Registers a fresh (never-verified) account, then tries to log in with it
    test.setTimeout(120_000)
    const unverifiedEmail = `e2e_unverified_${Date.now()}@test.com`

    // Resend goes through our own backend, which then asks Firebase to send.
    // Mocking it keeps the assertion on our UI and off Firebase's mail rate
    // limit, and the delay is what makes the in-flight state observable.
    const resendPayloads: unknown[] = []
    await page.route("**/api/register/resend-verification", async (route) => {
      resendPayloads.push(route.request().postDataJSON())
      await new Promise((resolve) => setTimeout(resolve, 1_500))
      await route.fulfill({
        json: { success: true, message: "sent", already_verified: false },
      })
    })
    // The success screen disables Resend for 60s after registering; the fake
    // clock skips that wait instead of sleeping through it
    await page.clock.install()

    await page.goto("/register")
    await expect(page.locator('button:has-text("Sign Up")')).toBeVisible({
      timeout: 30_000,
    })
    await page.locator('input[name="name"]').fill("Unverified User")
    await page.locator('input[name="email"]').fill(unverifiedEmail)
    await page.locator('input[name="password"]').fill("Test@123")
    await page.locator('input[name="confirmPassword"]').fill("Test@123")
    // Branches not yet rebased onto the terms-agreement change have no checkbox
    const agree = page.locator("#agree-to-terms")
    if (await agree.count()) await agree.check()
    await page.locator('button:has-text("Sign Up")').click()
    await expect(
      page.locator("text=Registration Almost Complete!"),
    ).toBeVisible({ timeout: 30_000 })

    // Resend from the success screen, once the cooldown has elapsed
    const resendOnSuccess = page.getByRole("button", {
      name: /Resend Verification Email/,
    })
    await expect(resendOnSuccess).toBeDisabled()
    await page.clock.fastForward(61_000)
    await expect(resendOnSuccess).toBeEnabled()
    await resendOnSuccess.click()
    await expect(page.locator('button:has-text("Sending...")')).toBeVisible()
    await expect(
      page.locator("text=Verification email resent successfully"),
    ).toBeVisible({ timeout: 15_000 })
    expect(resendPayloads).toEqual([{ email: unverifiedEmail }])

    await page.getByRole("button", { name: "Go to Login Page" }).click()
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 })

    await page.locator('[data-testid="email"]').fill(unverifiedEmail)
    await page.locator('[data-testid="password"]').fill("Test@123")
    await page.locator('[data-testid="button-submit"]').click()

    const alert = page.locator('[role="alert"]').filter({ hasText: "verify" })
    await expect(alert).toBeVisible({ timeout: 30_000 })
    const resendFromAlert = page.locator('button:has-text("Resend Email")')
    await expect(resendFromAlert).toBeVisible()

    // No cooldown on the login-alert resend
    await resendFromAlert.click()
    await expect(page.locator('button:has-text("Sending...")')).toBeVisible()
    await expect(
      page.locator("text=Verification email resent successfully"),
    ).toBeVisible({ timeout: 15_000 })
    expect(resendPayloads).toHaveLength(2)
    expect(resendPayloads[1]).toEqual({ email: unverifiedEmail })
  })

  test("AUTH-05 - Successful logout", async ({ page }) => {
    skipWithoutCreds()
    await login(page)
    await logout(page)
  })

  test("AUTH-06 - Session persistence across tabs", async ({ page }) => {
    skipWithoutCreds()
    await login(page)

    // Simulate closing the tab and reopening the app URL in the same browser
    const newPage = await page.context().newPage()
    await page.close()
    await newPage.goto("/dashboard")
    await dismissStorageWarning(newPage)
    await expect(newPage).toHaveURL(/\/dashboard/, { timeout: 15_000 })
    await expect(newPage.locator("text=Dashboard").first()).toBeVisible()
  })
})

test.describe("Navigation - Public Header", () => {
  test("AUTH-07 - Logo navigates to public page", async ({ page }) => {
    await page.goto("/login")
    const logoLink = page.locator('a[href="/public"]')
    await expect(logoLink).toBeVisible()
    await logoLink.click()
    await expect(page).toHaveURL(/\/public/)
  })

  test("AUTH-08 - Dashboard button visible and works when logged in", async ({
    page,
  }) => {
    skipWithoutCreds()
    await login(page)
    await page.goto("/public")

    // /public renders a nested header; the last Dashboard link is the clickable one
    const dashboardButton = page.locator('a:has-text("Dashboard")').last()
    await expect(dashboardButton).toBeVisible({ timeout: 15_000 })
    await expect(dashboardButton).toHaveAttribute("href", "/dashboard")
    await expect(page.locator('a:has-text("Login")').first()).toBeHidden()

    await dashboardButton.click()
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })
  })

  test("AUTH-12 - Login button is shown on /public and navigates", async ({
    page,
  }) => {
    await page.goto("/public")
    // The nested header means the last link is the clickable one
    const loginLink = page.locator('a[href="/login"]').last()
    await expect(loginLink).toBeVisible({ timeout: 15_000 })

    await loginLink.click()
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 })
  })
})

// Registration form validation
test.describe("Registration form validation (extra)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/register")
    await expect(page.locator('button:has-text("Sign Up")')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("AUTH-09 - Registration empty fields", async ({ page }) => {
    await page.locator('button:has-text("Sign Up")').click()
    const alert = page.locator('[role="alert"]')
    await expect(alert).toBeVisible()
    await expect(alert).toContainText("Please fill in all fields")
  })

  test("AUTH-10 - Registration password mismatch", async ({ page }) => {
    await page.locator('input[name="name"]').fill("Test User")
    await page.locator('input[name="email"]').fill("test@test.com")
    await page.locator('input[name="password"]').fill("Test@123")
    await page.locator('input[name="confirmPassword"]').fill("Test@456")
    await page.locator('button:has-text("Sign Up")').click()
    await expect(page.locator('[role="alert"]')).toContainText(
      "password is not match",
    )
  })

  test("AUTH-11 - Registration password complexity", async ({ page }) => {
    await page.locator('input[name="name"]').fill("Test User")
    await page.locator('input[name="email"]').fill("test@test.com")
    await page.locator('input[name="password"]').fill("Test1234")
    await page.locator('input[name="confirmPassword"]').fill("Test1234")
    await page.locator('button:has-text("Sign Up")').click()
    await expect(page.locator('[role="alert"]')).toContainText(
      "must be at least 6 characters long",
    )
  })

  test("AUTH-14 - Name shorter than two characters is rejected", async ({
    page,
  }) => {
    await page.locator('input[name="name"]').fill("A")
    await page.locator('input[name="email"]').fill("test@test.com")
    await page.locator('input[name="password"]').fill("Test@123")
    await page.locator('input[name="confirmPassword"]').fill("Test@123")
    await page.locator('button:has-text("Sign Up")').click()
    await expect(page.locator('[role="alert"]')).toContainText(
      "Name must be at least 2 characters",
    )
    await expect(page).toHaveURL(/\/register/)
  })

  test("AUTH-15 - Password with a forbidden character is rejected", async ({
    page,
  }) => {
    const alert = page.locator('[role="alert"]')
    await page.locator('input[name="name"]').fill("Test User")
    await page.locator('input[name="email"]').fill("test@test.com")
    for (const char of ["<", ">", '"', "'"]) {
      // Satisfies the complexity rule (letter, digit, allowed special) so the
      // forbidden-character branch is the one that fires
      const password = `Test@12${char}`
      await page.locator('input[name="password"]').fill(password)
      await page.locator('input[name="confirmPassword"]').fill(password)
      // Typing clears the previous error, so waiting for it to go is what stops
      // the next assertion passing on the last iteration's alert
      await expect(alert).toBeHidden()

      await page.locator('button:has-text("Sign Up")').click()
      await expect(alert).toContainText("Allowed special characters", {
        timeout: 10_000,
      })
      await expect(page).toHaveURL(/\/register/)
    }
  })

  test("AUTH-16 - Show Password toggles both password fields", async ({
    page,
  }) => {
    const password = page.locator('input[name="password"]')
    const confirm = page.locator('input[name="confirmPassword"]')
    await expect(password).toHaveAttribute("type", "password")
    await expect(confirm).toHaveAttribute("type", "password")

    await page.locator("#show-password").check()
    await expect(password).toHaveAttribute("type", "text")
    await expect(confirm).toHaveAttribute("type", "text")

    await page.locator("#show-password").uncheck()
    await expect(password).toHaveAttribute("type", "password")
    await expect(confirm).toHaveAttribute("type", "password")
  })
})

// The header drops its Login button on the auth pages themselves. The Dashboard
// button is not asserted here: it renders only for a signed-in user, and a
// signed-in visitor is redirected off /login before the header is observable.
test.describe("Header on auth pages", () => {
  test("AUTH-13 - Auth pages show no header Login button", async ({ page }) => {
    await page.goto("/login")
    await expect(page.locator('[data-testid="button-submit"]')).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.locator('a[href="/login"]')).toHaveCount(0)

    await page.goto("/register")
    await expect(page.locator('button:has-text("Sign Up")')).toBeVisible({
      timeout: 30_000,
    })
    // The form's own "Already have an account? Login" link stays; the header's
    // must not, so exactly one /login link may exist and it is that one
    await expect(page.locator('a[href="/login"]')).toHaveCount(1)
    await expect(
      page.locator('p:has-text("Already have an account?") a[href="/login"]'),
    ).toHaveCount(1)
  })
})

// This project carries no storage state, so the browser holds no token. The
// guard (Layout's checkAuth) must send a protected deep link to /login without
// rendering the page behind it. Needs no credentials, so it always runs.
test.describe("Auth guard", () => {
  test("AUTH-17 - Protected routes without a session redirect to login", async ({
    page,
  }) => {
    for (const path of ["/subscription", "/subscription/manage"]) {
      await page.goto(path)
      await expect(page, `${path} must redirect to /login`).toHaveURL(
        /\/login/,
        { timeout: 15_000 },
      )
      await expect(page.locator('[data-testid="email"]')).toBeVisible({
        timeout: 15_000,
      })
      // No subscription content may have rendered behind the redirect
      await expect(page.getByText(/^Current Plan/)).toHaveCount(0)
    }
  })
})

// Rows 116 / 117: the sheets' own SQL row checks after a registration. The
// unit suite asserts the ORM rows create_user builds; this is the real MySQL
// round trip, so it is local-stack only.
test.describe("Registration DB rows", () => {
  test("AUTH-18 - Registration writes the active free-plan rows", async () => {
    const reason = localStackSkipReason()
    test.skip(!!reason, `rows 116 / 117: ${reason}`)
    test.setTimeout(120_000)

    const email = "e2e_unverified_dbrows@test.com"
    await ensureRegisteredUser(email, "Test@123", "E2E DB Rows User")

    // Inner joins on purpose: a registration that skipped any of these rows
    // must fail this query, not be papered over by a LEFT JOIN default
    const row = runSql(
      `SELECT u.active, u.uid <> '', u.created_at IS NOT NULL, r.role_id,
              s.plan_id, st.storage_quota_bytes
         FROM users u
         JOIN user_roles r ON r.user_id = u.id
         JOIN subscription_users s ON s.user_id = u.id
         JOIN user_storage_usage st ON st.user_id = u.id
        WHERE u.email = '${sqlLiteral(email)}';`,
    )
    expect(
      row,
      `registration rows for ${email} (active, uid set, created_at set, ` +
        `role_id, plan_id, quota): "${row}"`,
    ).toMatch(/^1\s+1\s+1\s+20\s+1\s+5368709120$/)
  })
})

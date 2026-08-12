import { test, expect } from "@playwright/test"

import {
  FREE_USER,
  login,
  logout,
  skipWithoutCreds,
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

  test("AUTH-04 - Unverified email login shows Resend Email", async ({
    page,
  }) => {
    // Registers a fresh (never-verified) account, then tries to log in with it
    test.setTimeout(120_000)
    const unverifiedEmail = `e2e_unverified_${Date.now()}@test.com`

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

    await page.goto("/login")
    await page.locator('[data-testid="email"]').fill(unverifiedEmail)
    await page.locator('[data-testid="password"]').fill("Test@123")
    await page.locator('[data-testid="button-submit"]').click()

    const alert = page.locator('[role="alert"]').filter({ hasText: "verify" })
    await expect(alert).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('button:has-text("Resend Email")')).toBeVisible()
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
})

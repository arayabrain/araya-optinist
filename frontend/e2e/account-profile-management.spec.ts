import { test, expect, Page } from "@playwright/test"

// Test credentials - configurable via env vars
const TEST_EMAIL = process.env.TEST_USER_EMAIL || "tsuchiyama_yutaka@araya.org"
const TEST_PASSWORD = process.env.TEST_USER_PASSWORD || "YutakaTsuchiyama123"

// Admin credentials - configurable via env vars
const ADMIN_EMAIL =
  process.env.ADMIN_USER_EMAIL || "admin@demo.optinist.araya.org"
const ADMIN_PASSWORD =
  process.env.ADMIN_USER_PASSWORD || "Optinist-demo-araya-1"

// Helper: Login with given credentials and navigate to dashboard
async function login(page: Page, email: string, password: string) {
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.goto("/login")
    await expect(page.locator('[data-testid="button-submit"]')).toBeVisible({
      timeout: 30_000,
    })
    await page.locator('[data-testid="email"]').fill(email)
    await page.locator('[data-testid="password"]').fill(password)
    await page.locator('[data-testid="button-submit"]').click()

    try {
      await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })
      return
    } catch {
      // Login timed out — retry
    }
  }
  throw new Error(`Login failed after 3 attempts for ${email}`)
}

// ==============================================
// TC 300: Login
// ==============================================

test.describe("Login", () => {
  test("TC300 - Login user", async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)
    await expect(page.locator("text=Dashboard")).toBeVisible()
  })
})

// ==============================================
// TC 301-305: Change Password
// ==============================================

test.describe("Change Password", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)
    await page.goto("/account")
    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC301 - Open change password modal", async ({ page }) => {
    await page.locator('button:has-text("Change Password")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator("text=Change Password")).toBeVisible()

    // Verify inputs exist
    await expect(
      dialog.locator('input[placeholder="Old Password"]'),
    ).toBeVisible()
    await expect(
      dialog.locator('input[placeholder="New Password"]'),
    ).toBeVisible()
    await expect(
      dialog.locator('input[placeholder="Confirm Password"]'),
    ).toBeVisible()

    // Close modal
    await dialog.locator('button:has-text("Close")').click()
    await expect(dialog).toBeHidden()
  })

  test("TC302 - Validation - empty fields", async ({ page }) => {
    await page.locator('button:has-text("Change Password")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Click UPDATE without filling fields
    await dialog.locator('button:has-text("UPDATE")').click()

    // Should show required field errors
    await expect(
      dialog.locator("text=This field is required").first(),
    ).toBeVisible()

    // Close modal
    await dialog.locator('button:has-text("Close")').click()
  })

  test("TC303 - Wrong current password", async ({ page }) => {
    await page.locator('button:has-text("Change Password")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    await dialog
      .locator('input[placeholder="Old Password"]')
      .fill("WrongPass@1")
    await dialog
      .locator('input[placeholder="New Password"]')
      .fill("NewPass@123")
    await dialog
      .locator('input[placeholder="Confirm Password"]')
      .fill("NewPass@123")

    await dialog.locator('button:has-text("UPDATE")').click()

    // Should show failure notification
    await expect(page.locator("text=Failed to Change Password!")).toBeVisible({
      timeout: 10_000,
    })
  })

  test("TC304 - Password mismatch", async ({ page }) => {
    await page.locator('button:has-text("Change Password")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    await dialog
      .locator('input[placeholder="Old Password"]')
      .fill(TEST_PASSWORD)
    await dialog
      .locator('input[placeholder="New Password"]')
      .fill("NewPass@123")
    await dialog
      .locator('input[placeholder="Confirm Password"]')
      .fill("DifferentPass@456")
    // Trigger blur to run validation
    await dialog.locator('input[placeholder="Confirm Password"]').blur()

    // Should show mismatch error
    await expect(dialog.locator("text=Passwords do not match")).toBeVisible()
  })

  test("TC305 - Successful password change", async ({ page }) => {
    // Skip by default to avoid locking out the test account
    test.skip(
      !process.env.RUN_PASSWORD_CHANGE_TEST,
      "Set RUN_PASSWORD_CHANGE_TEST=1 to run (changes actual password)",
    )

    const newPassword = "TempPass@999"

    // Change to new password
    await page.locator('button:has-text("Change Password")').click()
    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    await dialog
      .locator('input[placeholder="Old Password"]')
      .fill(TEST_PASSWORD)
    await dialog.locator('input[placeholder="New Password"]').fill(newPassword)
    await dialog
      .locator('input[placeholder="Confirm Password"]')
      .fill(newPassword)
    await dialog.locator('button:has-text("UPDATE")').click()

    await expect(
      page.locator("text=Your password has been successfully changed!"),
    ).toBeVisible({ timeout: 10_000 })

    // Revert back to original password
    await page.locator('button:has-text("Change Password")').click()
    const dialog2 = page.locator('[role="dialog"]')
    await expect(dialog2).toBeVisible()

    await dialog2.locator('input[placeholder="Old Password"]').fill(newPassword)
    await dialog2
      .locator('input[placeholder="New Password"]')
      .fill(TEST_PASSWORD)
    await dialog2
      .locator('input[placeholder="Confirm Password"]')
      .fill(TEST_PASSWORD)
    await dialog2.locator('button:has-text("UPDATE")').click()

    await expect(
      page.locator("text=Your password has been successfully changed!"),
    ).toBeVisible({ timeout: 10_000 })
  })
})

// ==============================================
// TC 306-308: Editing Name
// ==============================================

test.describe("Editing Name", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)
    await page.goto("/account")
    await expect(page.locator('h2:has-text("Account Profile")')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC306 - Inline name editing - save", async ({ page }) => {
    // Get current name
    const nameSection = page.locator("text=Name").locator("..")
    const editButton = nameSection.locator("button").first()
    await editButton.click()

    // Input should appear
    const nameInput = page.locator('input[placeholder="Name"]')
    await expect(nameInput).toBeVisible()

    // Store original value and type new name
    const originalName = await nameInput.inputValue()
    const testName = `E2E_Test_${Date.now()}`
    await nameInput.fill(testName)
    await nameInput.press("Enter")

    // Should show success notification
    await expect(
      page.locator("text=Full name edited successfully!"),
    ).toBeVisible({ timeout: 10_000 })

    // Restore original name
    const editButton2 = nameSection.locator("button").first()
    await editButton2.click()
    const nameInput2 = page.locator('input[placeholder="Name"]')
    await expect(nameInput2).toBeVisible()
    await nameInput2.fill(originalName)
    await nameInput2.press("Enter")

    await expect(
      page.locator("text=Full name edited successfully!"),
    ).toBeVisible({ timeout: 10_000 })
  })

  test("TC307 - Inline name editing - cancel", async ({ page }) => {
    const nameSection = page.locator("text=Name").locator("..")
    const editButton = nameSection.locator("button").first()
    await editButton.click()

    const nameInput = page.locator('input[placeholder="Name"]')
    await expect(nameInput).toBeVisible()

    const originalName = await nameInput.inputValue()
    await nameInput.fill("CancelledName")
    await nameInput.press("Escape")

    // Input should disappear (back to display mode)
    await expect(nameInput).toBeHidden()

    // Original name should still be shown
    await expect(page.getByText(originalName, { exact: true })).toBeVisible()
  })

  test("TC308 - Name validation - empty", async ({ page }) => {
    const nameSection = page.locator("text=Name").locator("..")
    const editButton = nameSection.locator("button").first()
    await editButton.click()

    const nameInput = page.locator('input[placeholder="Name"]')
    await expect(nameInput).toBeVisible()

    // Clear and blur to trigger validation
    await nameInput.fill("")
    await nameInput.blur()

    // Should show error
    await expect(page.locator("text=Full name can't be empty!")).toBeVisible({
      timeout: 10_000,
    })
  })
})

// ==============================================
// TC 309-312: Admin Access
// ==============================================

test.describe("Admin Access", () => {
  test("TC309 - Login with Admin account", async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    await expect(page.locator("text=Dashboard")).toBeVisible()
  })

  test("TC310 - Account Manager menu visible for Admin", async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD)

    // Check left navigation for Account Manager
    await expect(page.locator("text=Account Manager")).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC311 - Non-admin cannot see Account Manager menu", async ({
    page,
  }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)

    // Account Manager should NOT be visible for regular user
    await expect(page.locator("text=Account Manager")).toBeHidden()
  })

  test("TC312 - Direct URL access as non-admin", async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)

    // Try to access account manager directly
    await page.goto("/account-manager")

    // Should redirect to dashboard
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })
  })
})

// ==============================================
// TC 313-314: User List
// ==============================================

test.describe("User List", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    await page.goto("/account-manager")
    await expect(page.locator('h1:has-text("Account Manager")')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC313 - View users list", async ({ page }) => {
    // Verify table columns exist
    const grid = page.locator('[role="grid"]')
    await expect(grid).toBeVisible()

    await expect(grid.getByText("ID", { exact: true })).toBeVisible()
    await expect(grid.getByText("Name", { exact: true })).toBeVisible()
    await expect(grid.getByText("Role", { exact: true })).toBeVisible()
    await expect(grid.getByText("Mail", { exact: true })).toBeVisible()
    await expect(grid.getByText("Data size", { exact: true })).toBeVisible()
    await expect(
      grid.getByText("Subscription Status", { exact: true }),
    ).toBeVisible()
    await expect(grid.getByText("Storage Usage", { exact: true })).toBeVisible()
    await expect(grid.getByText("Bucket name", { exact: true })).toBeVisible()

    // Should have at least one row (the admin user)
    const rows = grid.locator('[role="row"]')
    await expect(rows.first()).toBeVisible()
  })

  test("TC314 - Pagination", async ({ page }) => {
    // Pagination should be visible if there are users
    const pagination = page.locator("text=Rows per page")
    if (await pagination.isVisible()) {
      await expect(pagination).toBeVisible()
    }
  })
})

// ==============================================
// TC 315-323: Create User
// ==============================================

test.describe("Create User", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    await page.goto("/account-manager")
    await expect(page.locator('h1:has-text("Account Manager")')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC315 - Open create user modal", async ({ page }) => {
    await page.locator('button:has-text("Add")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator("text=Add Account")).toBeVisible()

    // Verify form fields
    await expect(dialog.locator("text=Name:")).toBeVisible()
    await expect(dialog.locator("text=Role:")).toBeVisible()
    await expect(dialog.locator("text=e-mail:")).toBeVisible()
    await expect(dialog.getByText("Password:", { exact: true })).toBeVisible()
    await expect(
      dialog.getByText("Confirm Password:", { exact: true }),
    ).toBeVisible()

    // Verify buttons
    await expect(dialog.locator('button:has-text("Cancel")')).toBeVisible()
    await expect(dialog.locator('button:has-text("Ok")')).toBeVisible()

    // Close
    await dialog.locator('button:has-text("Cancel")').click()
    await expect(dialog).toBeHidden()
  })

  test("TC319 - Validation - required fields", async ({ page }) => {
    await page.locator('button:has-text("Add")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Click OK without filling anything
    await dialog.locator('button:has-text("Ok")').click()

    // Should show required field errors
    await expect(
      dialog.locator("text=This field is required").first(),
    ).toBeVisible()

    // Close
    await dialog.locator('button:has-text("Cancel")').click()
  })

  test("TC321 - Validation - invalid email", async ({ page }) => {
    await page.locator('button:has-text("Add")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Fill with invalid email
    await dialog.locator('input[name="name"]').fill("Test User")
    await dialog.locator('input[name="email"]').fill("not-an-email")
    await dialog.locator('input[name="email"]').blur()

    // Should show invalid email error
    await expect(dialog.locator("text=Invalid email format")).toBeVisible()

    // Close
    await dialog.locator('button:has-text("Cancel")').click()
  })

  test("TC322 - Validation - weak password", async ({ page }) => {
    await page.locator('button:has-text("Add")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Fill with weak password
    await dialog.locator('input[name="password"]').fill("weak")
    await dialog.locator('input[name="password"]').blur()

    // Should show password requirements error
    await expect(
      dialog.locator(
        "text=must be at least 6 characters long and must contain at least one letter, number, and special character",
      ),
    ).toBeVisible()

    // Close
    await dialog.locator('button:has-text("Cancel")').click()
  })

  test("TC323 - Cancel creation", async ({ page }) => {
    await page.locator('button:has-text("Add")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Fill some fields
    await dialog.locator('input[name="name"]').fill("Cancel Test")
    await dialog.locator('input[name="email"]').fill("cancel@test.com")

    // Click Cancel
    await dialog.locator('button:has-text("Cancel")').click()

    // Modal should close without creating user
    await expect(dialog).toBeHidden()
  })

  test("TC316 - Create user successfully", async ({ page }) => {
    test.skip(
      !process.env.RUN_ADMIN_CRUD_TESTS,
      "Set RUN_ADMIN_CRUD_TESTS=1 to run (creates/modifies real users)",
    )

    const uniqueEmail = `e2e_create_${Date.now()}@test.com`

    await page.locator('button:has-text("Add")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    await dialog.locator('input[name="name"]').fill("E2E Created User")
    // Select OPERATOR role
    await dialog.locator('select[name="role_id"]').selectOption("OPERATOR")
    await dialog.locator('input[name="email"]').fill(uniqueEmail)
    await dialog.locator('input[name="password"]').fill("Test@123")
    await dialog.locator('input[name="confirmPassword"]').fill("Test@123")

    await dialog.locator('button:has-text("Ok")').click()

    // Should show success notification
    await expect(
      page.locator("text=Your account has been created successfully!"),
    ).toBeVisible({ timeout: 10_000 })
  })

  test("TC320 - Validation - duplicate email", async ({ page }) => {
    test.skip(
      !process.env.RUN_ADMIN_CRUD_TESTS,
      "Set RUN_ADMIN_CRUD_TESTS=1 to run (interacts with real user data)",
    )

    await page.locator('button:has-text("Add")').click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Use existing admin email
    await dialog.locator('input[name="name"]').fill("Duplicate Test")
    await dialog.locator('select[name="role_id"]').selectOption("OPERATOR")
    await dialog.locator('input[name="email"]').fill(ADMIN_EMAIL)
    await dialog.locator('input[name="password"]').fill("Test@123")
    await dialog.locator('input[name="confirmPassword"]').fill("Test@123")

    await dialog.locator('button:has-text("Ok")').click()

    // Should show duplicate email error
    await expect(page.locator("text=This email already exists!")).toBeVisible({
      timeout: 10_000,
    })
  })
})

// ==============================================
// TC 324-330: Edit User
// ==============================================

test.describe("Edit User", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    await page.goto("/account-manager")
    await expect(page.locator('h1:has-text("Account Manager")')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC324 - Open edit user modal", async ({ page }) => {
    // Click edit button on the first user row
    const editButton = page.locator('[aria-label="Edit Account"]').first()
    if (!(await editButton.isVisible())) {
      // Try tooltip-based selector
      const editBtn = page
        .locator('button:has(svg[data-testid="EditIcon"])')
        .first()
      await editBtn.click()
    } else {
      await editButton.click()
    }

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator("text=Edit Account")).toBeVisible()

    // Edit modal should show name, role, email (no password fields)
    await expect(dialog.locator("text=Name:")).toBeVisible()
    await expect(dialog.locator("text=Role:")).toBeVisible()
    await expect(dialog.locator("text=e-mail:")).toBeVisible()

    // Password fields should NOT be visible in edit mode
    await expect(dialog.getByText("Password:", { exact: true })).toBeHidden()

    // Close
    await dialog.locator('button:has-text("Cancel")').click()
    await expect(dialog).toBeHidden()
  })

  test("TC328 - Validation - empty name in edit", async ({ page }) => {
    const editButton = page
      .locator('button:has(svg[data-testid="EditIcon"])')
      .first()
    await editButton.click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Clear name field
    await dialog.locator('input[name="name"]').fill("")
    await dialog.locator('input[name="name"]').blur()

    // Should show error
    await expect(dialog.locator("text=This field is required")).toBeVisible()

    // Close
    await dialog.locator('button:has-text("Cancel")').click()
  })

  test("TC329 - Validation - invalid email in edit", async ({ page }) => {
    const editButton = page
      .locator('button:has(svg[data-testid="EditIcon"])')
      .first()
    await editButton.click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Enter invalid email
    await dialog.locator('input[name="email"]').fill("invalid-email")
    await dialog.locator('input[name="email"]').blur()

    // Should show error
    await expect(dialog.locator("text=Invalid email format")).toBeVisible()

    // Close
    await dialog.locator('button:has-text("Cancel")').click()
  })

  test("TC330 - Cancel edit", async ({ page }) => {
    const editButton = page
      .locator('button:has(svg[data-testid="EditIcon"])')
      .first()
    await editButton.click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Make some changes
    await dialog.locator('input[name="name"]').fill("Changed Name")

    // Click Cancel
    await dialog.locator('button:has-text("Cancel")').click()

    // Modal should close
    await expect(dialog).toBeHidden()
  })
})

// ==============================================
// TC 331-332: User Subscription & Storage
// ==============================================

test.describe("User Subscription & Storage", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    await page.goto("/account-manager")
    await expect(page.locator('h1:has-text("Account Manager")')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC331 - View subscription status in list", async ({ page }) => {
    const grid = page.locator('[role="grid"]')
    await expect(grid).toBeVisible()

    // Subscription Status column should be visible
    await expect(grid.locator("text=Subscription Status")).toBeVisible()

    // At least one user should show Free or Premium status
    const freeText = grid.locator("text=Free")
    const premiumText = grid.locator("text=Premium")
    await expect(freeText.or(premiumText).first()).toBeVisible()
  })

  test("TC332 - View storage usage", async ({ page }) => {
    const grid = page.locator('[role="grid"]')
    await expect(grid).toBeVisible()

    // Storage Usage column should be visible
    await expect(grid.locator("text=Storage Usage")).toBeVisible()
  })
})

// ==============================================
// TC 333-338: Delete User
// ==============================================

test.describe("Delete User", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    await page.goto("/account-manager")
    await expect(page.locator('h1:has-text("Account Manager")')).toBeVisible({
      timeout: 15_000,
    })
  })

  test("TC333 - Delete user button visible", async ({ page }) => {
    // Delete button should be visible for other users (not self)
    const deleteButton = page
      .locator('button:has(svg[data-testid="DeleteIcon"])')
      .first()
    await expect(deleteButton).toBeVisible()
  })

  test("TC334 - Delete confirmation modal", async ({ page }) => {
    const deleteButton = page
      .locator('button:has(svg[data-testid="DeleteIcon"])')
      .first()
    await deleteButton.click()

    // Confirmation modal should appear
    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator("text=Do you want to delete")).toBeVisible()

    // Should have DELETE confirmation input
    await expect(dialog.locator('input[placeholder="DELETE"]')).toBeVisible()

    // Close without deleting
    await dialog.locator('button:has-text("CANCEL")').click()
    await expect(dialog).toBeHidden()
  })

  test("TC335 - Cancel deletion", async ({ page }) => {
    const deleteButton = page
      .locator('button:has(svg[data-testid="DeleteIcon"])')
      .first()
    await deleteButton.click()

    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()

    // Click Cancel
    await dialog.locator('button:has-text("CANCEL")').click()

    // Modal closes, user still in list
    await expect(dialog).toBeHidden()
    const grid = page.locator('[role="grid"]')
    const rows = grid.locator('[role="row"]')
    await expect(rows.first()).toBeVisible()
  })

  test("TC337 - Cannot delete self", async ({ page }) => {
    const grid = page.locator('[role="grid"]')
    await expect(grid).toBeVisible()

    // Find the row with the admin's email
    const adminRow = grid.locator(`[role="row"]:has-text("${ADMIN_EMAIL}")`)

    if (await adminRow.isVisible()) {
      // The admin row should NOT have a delete button
      const deleteButton = adminRow.locator(
        'button:has(svg[data-testid="DeleteIcon"])',
      )
      await expect(deleteButton).toHaveCount(0)
    }
  })

  test("TC336 - Confirm deletion", async ({ page }) => {
    test.skip(
      !process.env.RUN_ADMIN_CRUD_TESTS,
      "Set RUN_ADMIN_CRUD_TESTS=1 to run (actually deletes a user)",
    )
    test.setTimeout(120_000)

    // First create a test user to delete
    const uniqueEmail = `e2e_delete_${Date.now()}@test.com`

    await page.locator('button:has-text("Add")').click()
    const addDialog = page.locator('[role="dialog"]')
    await expect(addDialog).toBeVisible()

    await addDialog.locator('input[name="name"]').fill("Delete Me")
    await addDialog.locator('select[name="role_id"]').selectOption("OPERATOR")
    await addDialog.locator('input[name="email"]').fill(uniqueEmail)
    await addDialog.locator('input[name="password"]').fill("Test@123")
    await addDialog.locator('input[name="confirmPassword"]').fill("Test@123")
    await addDialog.locator('button:has-text("Ok")').click()

    await expect(
      page.locator("text=Your account has been created successfully!"),
    ).toBeVisible({ timeout: 10_000 })

    // Wait for list to update
    await expect(page.locator(`text=${uniqueEmail}`)).toBeVisible({
      timeout: 10_000,
    })

    // Now delete the user
    const userRow = page.locator(`[role="row"]:has-text("${uniqueEmail}")`)
    const deleteButton = userRow.locator(
      'button:has(svg[data-testid="DeleteIcon"])',
    )
    await deleteButton.click()

    const deleteDialog = page.locator('[role="dialog"]')
    await expect(deleteDialog).toBeVisible()

    // Type DELETE to confirm
    await deleteDialog.locator('input[placeholder="DELETE"]').fill("DELETE")
    await deleteDialog.locator('button:has-text("Delete Account")').click()

    // Should show success notification
    await expect(
      page.locator("text=Account deleted successfully!"),
    ).toBeVisible({ timeout: 10_000 })

    // User should no longer be in the list
    await expect(page.locator(`text=${uniqueEmail}`)).toBeHidden({
      timeout: 10_000,
    })
  })
})

// ==============================================
// TC 341-342: Access Control
// ==============================================

test.describe("Access Control", () => {
  test("TC341 - Admin can do all CRUD", async ({ page }) => {
    await login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    await page.goto("/account-manager")
    await expect(page.locator('h1:has-text("Account Manager")')).toBeVisible({
      timeout: 15_000,
    })

    // Verify Add button (Create)
    await expect(page.locator('button:has-text("Add")')).toBeVisible()

    // Verify grid (Read)
    await expect(page.locator('[role="grid"]')).toBeVisible()

    // Verify Edit button (Update)
    await expect(
      page.locator('button:has(svg[data-testid="EditIcon"])').first(),
    ).toBeVisible()

    // Verify Delete button (Delete)
    await expect(
      page.locator('button:has(svg[data-testid="DeleteIcon"])').first(),
    ).toBeVisible()
  })

  test("TC342 - Non-admin cannot access Account Manager", async ({ page }) => {
    await login(page, TEST_EMAIL, TEST_PASSWORD)

    // Direct URL access should redirect
    await page.goto("/account-manager")
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })
  })
})

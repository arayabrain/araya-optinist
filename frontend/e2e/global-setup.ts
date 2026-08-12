import * as fs from "fs"
import * as path from "path"

import { chromium } from "@playwright/test"

import { apiLogin, deleteE2eWorkspaces } from "./helpers"

// 1. Checks the credentials over the API, so a bad password fails here.
// 2. Deletes workspaces named e2e-* left behind by previous runs (leftover
//    rows push new ones out of the virtualized grid).
// 3. Logs in once via the UI and saves storage state for the authed specs,
//    keeping Firebase logins per run to a handful (rate limits).
export default async function globalSetup() {
  const email = process.env.TEST_USER_EMAIL
  const password = process.env.TEST_USER_PASSWORD
  if (!email || !password) return

  const baseURL = process.env.BASE_URL || "http://localhost:3000"

  // Before the browser login: bad credentials there are three silent 60s
  // waits for /dashboard, which reads as a hang rather than an auth error
  const { api, headers } = await apiLogin(email, password)
  try {
    await saveLoginState(baseURL, email, password)
    await deleteE2eWorkspaces(api, headers)
  } finally {
    await api.dispose()
  }
}

async function saveLoginState(
  baseURL: string,
  email: string,
  password: string,
) {
  const statePath = path.join(__dirname, ".auth", "free.json")
  fs.mkdirSync(path.dirname(statePath), { recursive: true })
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ baseURL })
    for (let attempt = 1; ; attempt++) {
      await page.goto("/login")
      await page.locator('[data-testid="email"]').fill(email)
      await page.locator('[data-testid="password"]').fill(password)
      await page.locator('[data-testid="button-submit"]').click()
      try {
        await page.waitForURL(/\/dashboard/, { timeout: 60_000 })
        break
      } catch (e) {
        if (attempt >= 3) throw e
      }
    }
    // Keeps the analytics consent notice from fronting the UI when the build
    // under test was compiled with a GTM container ID.
    await page.evaluate(() =>
      localStorage.setItem("analyticsConsent", "denied"),
    )
    await page.context().storageState({ path: statePath })
  } finally {
    await browser.close()
  }
}

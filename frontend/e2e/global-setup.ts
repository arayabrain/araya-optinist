import * as fs from "fs"
import * as path from "path"

import { chromium, request } from "@playwright/test"

import { authHeaders } from "./helpers"

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
  const apiURL = process.env.API_URL || baseURL.replace(/:\d+$/, ":8000")

  const api = await request.newContext({ baseURL: apiURL })
  try {
    // Before the browser login: bad credentials there are three silent 60s
    // waits for /dashboard, which reads as a hang rather than an auth error
    const loginRes = await api.post("/auth/login", {
      data: { email, password },
    })
    if (!loginRes.ok()) {
      throw new Error(
        `TEST_USER_EMAIL/TEST_USER_PASSWORD rejected by ${apiURL}/auth/login ` +
          `(${loginRes.status()}): ${await loginRes.text()}`,
      )
    }
    const { access_token, ex_token } = await loginRes.json()
    const auth = authHeaders(access_token, ex_token)

    await saveLoginState(baseURL, email, password)

    const listRes = await api.get("/workspaces?offset=0&limit=100", {
      headers: auth,
    })
    if (!listRes.ok()) {
      throw new Error(
        `Startup cleanup could not list workspaces ` +
          `(${listRes.status()}): ${await listRes.text()}`,
      )
    }
    const { items } = await listRes.json()
    for (const ws of items) {
      if (/^e2e-/.test(ws.name)) {
        await api
          .delete(`/workspace/${ws.id}`, { headers: auth })
          .catch(() => {})
      }
    }
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
    await page.context().storageState({ path: statePath })
  } finally {
    await browser.close()
  }
}

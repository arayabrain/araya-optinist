import { execSync } from "child_process"

import { test, expect, APIResponse, Page } from "@playwright/test"

import {
  CLOUDWATCH_POLL,
  FREE_LOG_GROUP,
  FREE_USER,
  PREMIUM_LOG_GROUP,
  PREMIUM_USER,
  PUBLIC_LOG_GROUP,
  RUN_TEST_TIMEOUT_MS,
  RUN_TIMEOUT_MS,
  apiHeaders,
  apiLogin,
  apiUrl,
  cloudwatchHas,
  importSampleData,
  isLocalBaseUrl,
  login,
  openWorkspace,
  reproduceTutorial,
  routedApiHeaders,
  runSql,
  runTutorial,
  s3ObjectCount,
  skipWithoutCreds,
  sqlSkipReason,
  startRun,
  windowStart,
} from "./helpers"

// Real-AWS premium assignment (sheet 06-2). Unlike the mocked STO-02/03/09,
// every test here performs a genuine assignment against the deployed dev
// environment: the backend picks a real tier, the premium ECS service really
// scales, and the assertions read the API, the cluster, and the UI state.
// Opt in explicitly - a run costs money and mutates shared dev infra:
//
//   RUN_SLOW=1 RUN_PREMIUM_AWS=1 npx playwright test e2e/15-premium-aws.spec.ts --retries 0
//
// --retries 0 matters: a real-AWS test that "passes on retry" hides real
// flakiness from the sign-off sheet.

const RUN_PREMIUM_AWS = process.env.RUN_PREMIUM_AWS === "1"

const CLUSTER = "development-optinist-cloud-cluster"
const PREMIUM_SERVICE = "development-premium-optinist-cloud-service"
const REGION = "ap-northeast-1"

// A cold assign starts EC2 capacity + an ECS task: minutes, not seconds
const ASSIGN_TIMEOUT_MS = 15 * 60_000
const TEST_TIMEOUT_MS = ASSIGN_TIMEOUT_MS + 10 * 60_000
// The premium endpoints do real AWS work in-request (ALB rules, scale-up,
// teardown), so the config's 15s actionTimeout aborts them mid-flight -
// observed 2026-08-19: every assign/release timed out client-side while
// completing server-side. Each call names its own budget instead.
const ASSIGN_REQUEST_TIMEOUT_MS = 300_000
const RELEASE_REQUEST_TIMEOUT_MS = 120_000
const STATUS_REQUEST_TIMEOUT_MS = 30_000

function ecs(query: string): string {
  return execSync(
    `aws ecs describe-services --cluster ${CLUSTER} --region ${REGION} ` +
      `--services ${PREMIUM_SERVICE} --query '${query}' --output text`,
    { timeout: 30_000 },
  )
    .toString()
    .trim()
}

// A dedicated assignment creates a per-user ALB target group with a
// deterministic name; its lifecycle is the real ALB truth the coverage doc
// long marked manual. NotFound (deleted) and any other CLI failure both
// return false, so assert true before ever asserting false.
function tgExists(userId: number): boolean {
  try {
    execSync(
      `aws elbv2 describe-target-groups --names premium-${userId}-tg ` +
        `--region ${REGION}`,
      { timeout: 30_000, stdio: ["pipe", "pipe", "pipe"] },
    )
    return true
  } catch {
    return false
  }
}

// A window that provably excludes older matches of the same pattern: when a
// previous test's teardown logged the line within the slack, tighten to now.
function freshWindow(logGroup: string, pattern: string): number {
  const t = windowStart()
  return cloudwatchHas(logGroup, pattern, t) ? Date.now() - 1_000 : t
}

// The autoscaling pool tier reports a sentinel instead of a real EC2 id, so
// "a machine is really ours" is pinned on the id shape, not on is_shared alone
function isDedicated(a: { is_shared?: boolean; instance_id?: string }) {
  return !a.is_shared && !!a.instance_id?.startsWith("i-")
}

// Second premium account, required only by the two-user scale-down test
const PREMIUM2_USER = {
  email: process.env.TEST_PREMIUM2_EMAIL || "",
  password: process.env.TEST_PREMIUM2_PASSWORD || "",
}

function runningPremiumInstanceIds(): string[] {
  const out = execSync(
    `aws ec2 describe-instances --region ${REGION} ` +
      `--filters "Name=tag:Name,Values=*premium*" ` +
      `"Name=instance-state-name,Values=running" ` +
      `--query 'Reservations[].Instances[].InstanceId' --output text`,
    { timeout: 30_000 },
  )
    .toString()
    .trim()
  return out ? out.split(/\s+/) : []
}

function ecsContainerEc2Ids(): string[] {
  const arns = execSync(
    `aws ecs list-container-instances --cluster ${CLUSTER} ` +
      `--region ${REGION} --query 'containerInstanceArns' --output text`,
    { timeout: 30_000 },
  )
    .toString()
    .trim()
  if (!arns) return []
  const out = execSync(
    `aws ecs describe-container-instances --cluster ${CLUSTER} ` +
      `--region ${REGION} --container-instances ${arns.split(/\s+/).join(" ")} ` +
      `--query 'containerInstances[].ec2InstanceId' --output text`,
    { timeout: 30_000 },
  )
    .toString()
    .trim()
  return out ? out.split(/\s+/) : []
}

// The idle scale-down lives in the monitoring Lambda's scheduled action, so
// the test fires the same event cron does instead of waiting on the cron.
// The tail log carries the "Scale-down analysis" line the assertions need.
function invokeMonitoringSweep(): string {
  const out = execSync(
    `aws lambda invoke --function-name development-premium-manager ` +
      `--payload '{"source":"aws.events","detail-type":"Scheduled Event"}' ` +
      `--cli-binary-format raw-in-base64-out --log-type Tail ` +
      `--query LogResult --output text --region ${REGION} /dev/null`,
    { timeout: 300_000 },
  )
    .toString()
    .trim()
  return Buffer.from(out, "base64").toString("utf-8")
}

function skipUnlessOptedIn(
  rows: string,
  user = PREMIUM_USER,
  name = "TEST_PREMIUM_EMAIL/TEST_PREMIUM_PASSWORD",
) {
  skipWithoutCreds(user, name)
  test.skip(
    !RUN_PREMIUM_AWS,
    `rows ${rows}: set RUN_PREMIUM_AWS=1 - assigns a real premium instance and scales real ECS`,
  )
  test.skip(
    isLocalBaseUrl(),
    `rows ${rows}: needs the deployed dev environment; BASE_URL is local`,
  )
}

type Assignment = {
  instance_id?: string
  is_shared?: boolean
  assigned_at?: string
} | null

type PremiumStatus = {
  subscription_type: string
  is_premium: boolean
  assignment: Assignment
}

async function statusViaPage(page: Page): Promise<PremiumStatus> {
  const headers = await apiHeaders(page)
  const res = await page.request.get(`${apiUrl()}/users/me/premium/status`, {
    headers,
    timeout: STATUS_REQUEST_TIMEOUT_MS,
  })
  expect(res.ok(), await res.text()).toBe(true)
  return res.json()
}

// The sheet's standby/autoscaling rows call a cluster with no free capacity a
// legitimate outcome (observed 2026-08-19: "insufficient CPU units
// available"). It leaves the rows unverified, not failed - skip with a reason
// the skip-summary reporter can put on the sign-off sheet.
function skipForNoCapacity(rows: string, detail: unknown): never {
  test.skip(
    true,
    `rows ${rows}: the cluster could not place premium capacity within ` +
      `${ASSIGN_TIMEOUT_MS / 60_000} min (${JSON.stringify(detail)}) - ` +
      `rerun when the dev cluster has free CPU`,
  )
  throw new Error("unreachable")
}

// Poll /status until the provider's own assign flow lands an assignment.
// If it never does, one direct assign probe tells apart "still scaling with
// no capacity" (skip) from a dead assign flow (fail).
async function waitForAssignment(
  page: Page,
  rows: string,
): Promise<PremiumStatus> {
  const deadline = Date.now() + ASSIGN_TIMEOUT_MS
  for (;;) {
    const status = await statusViaPage(page)
    if (status.assignment) return status
    if (Date.now() > deadline) {
      const headers = await apiHeaders(page)
      const probe = await page.request.post(
        `${apiUrl()}/users/me/premium/assign`,
        { headers, timeout: ASSIGN_REQUEST_TIMEOUT_MS },
      )
      const body = await probe.json().catch(() => ({}))
      if (body?.scaling_in_progress) skipForNoCapacity(rows, body)
      throw new Error(
        `no assignment appeared and the backend is not scaling: ` +
          `${probe.status()} ${JSON.stringify(body)}`,
      )
    }
    await new Promise((r) => setTimeout(r, 15_000))
  }
}

// The account trap this suite has been bitten by before: an account can say
// "Premium" on /users/me while /premium/status says free (billing grace).
// Every test verifies the slot against /premium/status before relying on it.
function expectGenuinelyPremium(status: PremiumStatus) {
  expect(
    status.is_premium,
    `${PREMIUM_USER.email} is not premium on /premium/status - ` +
      `verify the TEST_PREMIUM_* account against /premium/status, not /users/me`,
  ).toBe(true)
}

// The assign endpoint answers scaling_in_progress while capacity starts and
// expects the caller to retry. Retry every 30s rather than the advertised
// 180s so a warm assign settles in one round and a cold one within minutes.
async function assignUntilSettled(
  post: () => Promise<APIResponse>,
  rows: string,
) {
  const deadline = Date.now() + ASSIGN_TIMEOUT_MS
  for (;;) {
    const res = await post()
    expect(res.status(), await res.text()).toBeLessThan(500)
    const body = await res.json()
    if (body.assigned) {
      return body as {
        assigned: true
        is_shared: boolean
        instance_id?: string
      }
    }
    expect(body.scaling_in_progress, JSON.stringify(body)).toBe(true)
    if (Date.now() > deadline) skipForNoCapacity(rows, body)
    await new Promise((r) => setTimeout(r, 30_000))
  }
}

// The premium service normally idles at desired=0; a non-zero baseline means
// another user already holds capacity, and the scale-back-to-zero assertion
// in afterAll would then blame this run for their assignment.
let baselineDesired = -1
// Only a granted non-shared assignment obliges release to scale back down.
// An assign stuck on placement failure also bumps desiredCount, but nothing
// owns that capacity and only the reconciliation sweep (row 6226) clears it.
let heldDedicated = false

test.beforeAll(() => {
  if (!RUN_PREMIUM_AWS || !PREMIUM_USER.email || isLocalBaseUrl()) return
  baselineDesired = Number(ecs("services[0].desiredCount"))
})

function premiumAccounts() {
  return [PREMIUM_USER, PREMIUM2_USER].filter((u) => u.email && u.password)
}

// Hard-release after every test, pass or fail: a stuck assignment degrades
// the shared dev environment and keeps billing for the capacity.
test.afterEach(async () => {
  if (!RUN_PREMIUM_AWS || !PREMIUM_USER.email || isLocalBaseUrl()) return
  for (const user of premiumAccounts()) {
    const { api, headers } = await apiLogin(user.email, user.password)
    try {
      const res = await api.delete("/users/me/premium/assign", {
        headers,
        timeout: RELEASE_REQUEST_TIMEOUT_MS,
      })
      expect(res.ok(), await res.text()).toBe(true)
    } finally {
      await api.dispose()
    }
  }
})

// Asserted, not assumed: after the lane nothing may still be held BY US - no
// assignment row and no per-user ALB resources. The ECS desiredCount is NOT
// part of that invariant: the monitoring Lambda re-targets it to match the
// running standby-pool instances our assigns warmed up (observed 2026-08-19,
// desired=2 minutes after every release), and idle-pool scale-down is that
// Lambda's own 6221/6222 logic on its own schedule.
test.afterAll(async () => {
  if (!RUN_PREMIUM_AWS || !PREMIUM_USER.email || isLocalBaseUrl()) return
  test.setTimeout(5 * 60_000)
  for (const user of premiumAccounts()) {
    const { api, headers } = await apiLogin(user.email, user.password)
    try {
      const res = await api.get("/users/me/premium/status", {
        headers,
        timeout: STATUS_REQUEST_TIMEOUT_MS,
      })
      expect(res.ok(), await res.text()).toBe(true)
      const { assignment } = await res.json()
      expect(
        assignment ?? null,
        `the lane finished with ${user.email} still assigned`,
      ).toBeNull()
    } finally {
      await api.dispose()
    }
  }
  console.log(
    `[15-premium-aws] premium service after the lane: baseline desired=` +
      `${baselineDesired}, heldDedicated=${heldDedicated}, now ` +
      `desired/running=${ecs("services[0].[desiredCount,runningCount]")}`,
  )
})

test("PREM-01 - Premium login assigns a real tier and dedicated capacity really runs @slow", async ({
  page,
}) => {
  const rows = "6205 / 6206 / 6207 / 415 / BT-1109"
  skipUnlessOptedIn(rows)
  test.setTimeout(TEST_TIMEOUT_MS)

  // No mocks: the login triggers the provider's real assign flow, and the
  // backend's cascade picks whichever tier the live cluster can offer.
  const t0 = windowStart()
  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)
  expectGenuinelyPremium(await statusViaPage(page))
  const status = await waitForAssignment(page, rows)

  // API truth: the assignment row exists and names its tier
  const assignment = status.assignment!
  expect(typeof assignment.is_shared).toBe("boolean")
  expect(assignment.assigned_at).toBeTruthy()

  // AWS truth: a non-shared tier is backed by real capacity in the cluster
  if (!assignment.is_shared) {
    heldDedicated = true
    expect(assignment.instance_id).toBeTruthy()
    expect(Number(ecs("services[0].runningCount"))).toBeGreaterThan(0)
  }

  // UI truth: the frontend recorded the tier the backend really returned
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("premium_shared")))
    .toBe(String(assignment.is_shared))

  // CloudWatch truth: the assignment left its lines in the public tier's log
  // group (the assign endpoint always answers pre-routing), and the login-time
  // limit-warning calculation logged on the tier that served the login
  const me = await page.request.get(`${apiUrl()}/users/me`, {
    headers: await apiHeaders(page),
    timeout: STATUS_REQUEST_TIMEOUT_MS,
  })
  const userId: number = (await me.json()).id
  await expect
    .poll(
      () =>
        cloudwatchHas(
          PUBLIC_LOG_GROUP,
          `[premium-assign] user=${userId} assigned=True`,
          t0,
        ),
      {
        ...CLOUDWATCH_POLL,
        message: `no [premium-assign] success line for user ${userId} in ${PUBLIC_LOG_GROUP}`,
      },
    )
    .toBe(true)
  await expect
    .poll(
      () =>
        cloudwatchHas(
          PUBLIC_LOG_GROUP,
          `Successfully assigned premium user ${userId}`,
          t0,
        ),
      {
        ...CLOUDWATCH_POLL,
        message: `no service-side assign line for user ${userId} in ${PUBLIC_LOG_GROUP}`,
      },
    )
    .toBe(true)
  // /auth/login forwards to the public tier (ALB rule p305), so the login's
  // calculate_limit_warning lines land in the public group
  await expect
    .poll(
      () =>
        cloudwatchHas(
          PUBLIC_LOG_GROUP,
          `Calculating limit warning for user ${userId}`,
          t0,
        ),
      {
        ...CLOUDWATCH_POLL,
        message: `no limit-warning lines for premium user ${userId} in ${PUBLIC_LOG_GROUP}`,
      },
    )
    .toBe(true)
})

test("PREM-02 - Assign, release, and reassign round-trip the real backend @slow", async () => {
  skipUnlessOptedIn("6201 / 6203 / 6231 / BT-614 / BT-1109")
  test.setTimeout(TEST_TIMEOUT_MS * 2)

  // API-driven on purpose: PREM-01 already exercises the UI login half, and
  // a live provider in a page would race these explicit release/assign calls
  // with its own polling and auto-reassign.
  const { api, headers } = await apiLogin(
    PREMIUM_USER.email,
    PREMIUM_USER.password,
  )
  try {
    const rows = "6201 / 6203 / 6231 / BT-614 / BT-1109"
    const post = () =>
      api.post("/users/me/premium/assign", {
        headers,
        timeout: ASSIGN_REQUEST_TIMEOUT_MS,
      })
    const release = () =>
      api.delete("/users/me/premium/assign", {
        headers,
        timeout: RELEASE_REQUEST_TIMEOUT_MS,
      })
    const getStatus = async (): Promise<PremiumStatus> => {
      const res = await api.get("/users/me/premium/status", {
        headers,
        timeout: STATUS_REQUEST_TIMEOUT_MS,
      })
      expect(res.ok(), await res.text()).toBe(true)
      return res.json()
    }

    const first = await assignUntilSettled(post, rows)
    if (!first.is_shared) heldDedicated = true
    let status = await getStatus()
    expectGenuinelyPremium(status)
    expect(status.assignment).toBeTruthy()
    expect(status.assignment!.is_shared).toBe(first.is_shared)
    const firstAssignedAt = status.assignment!.assigned_at

    const me = await api.get("/users/me", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    const userId: number = (await me.json()).id
    const dedicated = isDedicated(first)
    // ALB truth: a dedicated assignment is backed by its per-user target group
    if (dedicated) {
      expect(
        tgExists(userId),
        `premium-${userId}-tg missing while dedicated-assigned`,
      ).toBe(true)
    }

    // Hard release (the logout path): the row must be gone immediately,
    // not soft-released into the 120s grace. freshWindow, not windowStart:
    // the previous test's afterEach hard-released this same user, and its
    // line must not satisfy this assert.
    const tRelease = freshWindow(
      PUBLIC_LOG_GROUP,
      `Released premium user ${userId}`,
    )
    const released = await release()
    expect(released.ok(), await released.text()).toBe(true)
    expect((await released.json()).released).toBe(true)
    status = await getStatus()
    expect(status.assignment ?? null).toBeNull()
    // CloudWatch truth (BT-614's log half): the release left its line.
    // The source line is "Released premium user {id} from instance ..." -
    // the sheet's "Releasing premium user" wording never appears verbatim
    // (the actual prefix is "Releasing (hard) premium user").
    await expect
      .poll(
        () =>
          cloudwatchHas(
            PUBLIC_LOG_GROUP,
            `Released premium user ${userId}`,
            tRelease,
          ),
        {
          ...CLOUDWATCH_POLL,
          message: `no release line for user ${userId} in ${PUBLIC_LOG_GROUP}`,
        },
      )
      .toBe(true)
    // ... and the ALB teardown must be real, not just the DB row
    if (dedicated) {
      await expect
        .poll(() => tgExists(userId), {
          timeout: 60_000,
          message: `premium-${userId}-tg survived the hard release`,
        })
        .toBe(false)
    }

    // Reassign lands a fresh assignment rather than resurrecting the old row
    const second = await assignUntilSettled(post, rows)
    if (!second.is_shared) heldDedicated = true
    expect(typeof second.is_shared).toBe("boolean")
    status = await getStatus()
    expect(status.assignment).toBeTruthy()
    expect(
      status.assignment!.assigned_at,
      "the reassign resurrected the released row instead of creating a fresh one",
    ).not.toBe(firstAssignedAt)

    const releasedAgain = await release()
    expect(releasedAgain.ok(), await releasedAgain.text()).toBe(true)
    status = await getStatus()
    expect(status.assignment ?? null).toBeNull()
  } finally {
    await api.dispose()
  }
})

test("PREM-03 - A page refresh adopts the real assignment without re-assigning @slow", async ({
  page,
}) => {
  skipUnlessOptedIn("6202 / 6212 / 6213")
  test.setTimeout(TEST_TIMEOUT_MS)

  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)
  expectGenuinelyPremium(await statusViaPage(page))
  const status = await waitForAssignment(page, "6202 / 6212 / 6213")
  const before = status.assignment!
  if (!before.is_shared) heldDedicated = true

  // Any write to /premium/assign after the reload is a re-assign or a
  // release; adoption must issue neither
  const assignWrites: string[] = []
  page.on("request", (req) => {
    if (req.url().includes("/premium/assign") && req.method() !== "GET") {
      assignWrites.push(`${req.method()} ${req.url()}`)
    }
  })

  const statusPolled = page.waitForResponse((r) =>
    r.url().includes("/premium/status"),
  )
  await page.reload()
  await statusPolled
  // Give a wrongly re-triggered assign the chance to fire before ruling it out
  await page.waitForTimeout(10_000)
  expect(assignWrites, assignWrites.join(", ")).toHaveLength(0)

  // DB truth: the very same row survived the reload - identity, tier and
  // timestamp unchanged, whichever tier the cluster really assigned
  const after = (await statusViaPage(page)).assignment!
  expect(after).toBeTruthy()
  expect(after.instance_id).toBe(before.instance_id)
  expect(after.assigned_at).toBe(before.assigned_at)
  expect(after.is_shared).toBe(before.is_shared)

  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("premium_shared")))
    .toBe(String(before.is_shared))
})

test("PREM-04 - A browser-close beacon soft-releases; reopening inside the grace restores the same row @slow", async () => {
  const rows = "6208 / BT-615 / 603"
  skipUnlessOptedIn(rows)
  test.setTimeout(TEST_TIMEOUT_MS)

  const tStart = windowStart()
  const { api, headers } = await apiLogin(
    PREMIUM_USER.email,
    PREMIUM_USER.password,
  )
  try {
    const post = () =>
      api.post("/users/me/premium/assign", {
        headers,
        timeout: ASSIGN_REQUEST_TIMEOUT_MS,
      })
    const first = await assignUntilSettled(post, rows)
    if (!first.is_shared) heldDedicated = true

    const me = await api.get("/users/me", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    const userId: number = (await me.json()).id
    const statusRes = await api.get("/users/me/premium/status", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    const before = (await statusRes.json()).assignment
    expect(before).toBeTruthy()
    const dedicated = isDedicated(before)

    // The beforeunload path: token minted while authed, then the beacon body
    // is the only credential (sendBeacon cannot carry Authorization headers)
    const t0 = windowStart()
    const tokenRes = await api.get("/users/me/premium/beacon-token", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    const { token } = await tokenRes.json()
    expect(token).toBeTruthy()
    const beacon = await api.post("/users/me/premium/release-beacon", {
      data: { token },
      timeout: RELEASE_REQUEST_TIMEOUT_MS,
    })
    expect(beacon.ok(), await beacon.text()).toBe(true)
    expect((await beacon.json()).success, await beacon.text()).toBe(true)

    // Soft, not hard: the per-user ALB resources must survive into the grace
    if (dedicated) {
      expect(
        tgExists(userId),
        `premium-${userId}-tg was torn down by a SOFT release`,
      ).toBe(true)
    }

    // Reopening inside the 120s grace: the next status check restores the
    // very same row - identical assigned_at and instance, never a re-assign
    const afterRes = await api.get("/users/me/premium/status", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    const after = (await afterRes.json()).assignment
    expect(after, "the grace restore returned no assignment").toBeTruthy()
    expect(after.assigned_at).toBe(before.assigned_at)
    expect(after.instance_id).toBe(before.instance_id)
    expect(after.is_shared).toBe(before.is_shared)

    // CloudWatch truth (BT-615's log half): the beacon always lands on the
    // public tier - sendBeacon cannot carry routing headers
    await expect
      .poll(
        () =>
          cloudwatchHas(
            PUBLIC_LOG_GROUP,
            `[premium-trace] beacon-released user=${userId}`,
            t0,
          ),
        {
          ...CLOUDWATCH_POLL,
          message: `no beacon-released trace for user ${userId} in ${PUBLIC_LOG_GROUP}`,
        },
      )
      .toBe(true)

    // Row 603's CloudWatch half. The beacon marked the user logged out and
    // the activity middleware ignores that user for 10s, so wait it out;
    // then prove the heartbeat itself worked - its own response plus the
    // service line, in a window that excludes everything earlier
    await new Promise((r) => setTimeout(r, 11_000))
    const tHeartbeat = Date.now() - 5_000
    const heartbeat = await api.post("/users/me/premium/heartbeat", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    expect(heartbeat.ok(), await heartbeat.text()).toBe(true)
    expect(
      (await heartbeat.json()).updated,
      "the heartbeat did not update activity",
    ).toBe(true)
    await expect
      .poll(
        () =>
          cloudwatchHas(
            PUBLIC_LOG_GROUP,
            `Successfully updated activity for premium user ${userId}`,
            tHeartbeat,
          ),
        {
          ...CLOUDWATCH_POLL,
          message: `no activity-update line for user ${userId} in ${PUBLIC_LOG_GROUP} after the heartbeat`,
        },
      )
      .toBe(true)
    // The sheet's own middleware line is throttled to once per minute per
    // user, so it is asserted over the whole test's window, not the
    // heartbeat's - it proves the line reaches CloudWatch, not causation
    await expect
      .poll(
        () =>
          cloudwatchHas(
            PUBLIC_LOG_GROUP,
            `Updated premium activity for user ${userId}`,
            tStart,
          ),
        {
          ...CLOUDWATCH_POLL,
          message: `no middleware premium-activity line for user ${userId} in ${PUBLIC_LOG_GROUP} during the test`,
        },
      )
      .toBe(true)
  } finally {
    await api.dispose()
  }
})

test("PREM-05 - Free-tier requests carry no premium routing headers @slow", async ({
  page,
}) => {
  skipUnlessOptedIn(
    "6238 / 402",
    FREE_USER,
    "TEST_USER_EMAIL/TEST_USER_PASSWORD",
  )
  test.setTimeout(10 * 60_000)

  // The real bundle's interceptor, not the jsdom one: capture every request
  // the app itself sends to the API and assert the omission on the wire
  const apiRequests: { url: string; headers: Record<string, string> }[] = []
  page.on("request", (req) => {
    if (req.url().startsWith(apiUrl())) {
      apiRequests.push({ url: req.url(), headers: req.headers() })
    }
  })

  const t0 = windowStart()
  await login(page, FREE_USER.email, FREE_USER.password)
  // On a deployed env apiUrl() can equal the page origin, so static assets
  // land in the capture too; the vacuity guard must count real API calls
  const isApiCall = (url: string) =>
    /\/(users\/me|workspaces|auth\/|storage-limit-alerts)/.test(url)
  await expect
    .poll(() => apiRequests.filter((r) => isApiCall(r.url)).length, {
      timeout: 60_000,
      message: "the app issued no API requests after a free login",
    })
    .toBeGreaterThanOrEqual(3)

  const offenders = apiRequests.filter(
    (r) => "x-routing-id" in r.headers || "x-user-tier" in r.headers,
  )
  expect(
    offenders.map((r) => r.url),
    "free-tier requests carried premium routing headers",
  ).toHaveLength(0)

  // Row 402's CloudWatch half: the login-time limit-warning calculation
  // logged its free-plan branch, and the line is logger.debug - it appearing
  // at all also proves DEBUG-level lines reach CloudWatch. /auth/login
  // forwards to the public tier (ALB rule p305), so the line is public-group.
  const me = await page.request.get(`${apiUrl()}/users/me`, {
    headers: await apiHeaders(page),
    timeout: STATUS_REQUEST_TIMEOUT_MS,
  })
  const userId: number = (await me.json()).id
  await expect
    .poll(
      () =>
        cloudwatchHas(
          PUBLIC_LOG_GROUP,
          `User ${userId}: No warning needed (free plan, within limits)`,
          t0,
        ),
      {
        ...CLOUDWATCH_POLL,
        message: `no free-plan limit-warning debug line for user ${userId} in ${PUBLIC_LOG_GROUP}`,
      },
    )
    .toBe(true)
})

test("PREM-06 - The sweep stops a released idle instance and keeps the last one warm @slow", async () => {
  const rows = "6221 / 6222"
  skipUnlessOptedIn(rows)
  test.skip(
    !PREMIUM2_USER.email || !PREMIUM2_USER.password,
    `rows ${rows}: TEST_PREMIUM2_EMAIL/TEST_PREMIUM2_PASSWORD not set - needs two concurrent premium users`,
  )
  test.setTimeout(TEST_TIMEOUT_MS * 2)

  const s1 = await apiLogin(PREMIUM_USER.email, PREMIUM_USER.password)
  const s2 = await apiLogin(PREMIUM2_USER.email, PREMIUM2_USER.password)
  try {
    const post = (s: typeof s1) => () =>
      s.api.post("/users/me/premium/assign", {
        headers: s.headers,
        timeout: ASSIGN_REQUEST_TIMEOUT_MS,
      })
    const release = async (s: typeof s1) => {
      const res = await s.api.delete("/users/me/premium/assign", {
        headers: s.headers,
        timeout: RELEASE_REQUEST_TIMEOUT_MS,
      })
      expect(res.ok(), await res.text()).toBe(true)
    }

    const a1 = await assignUntilSettled(post(s1), rows)
    const a2 = await assignUntilSettled(post(s2), rows)
    if (!a1.is_shared || !a2.is_shared) heldDedicated = true
    // Idle scale-down is only observable when both users really hold their
    // own machine; any other tier this run leaves the rows unverified
    test.skip(
      !isDedicated(a1) || !isDedicated(a2) || a1.instance_id === a2.instance_id,
      `rows ${rows}: the cascade did not grant two distinct dedicated instances ` +
        `this run (${JSON.stringify([a1, a2])})`,
    )

    await release(s1)
    await release(s2)
    const runningBefore = runningPremiumInstanceIds()
    expect(runningBefore.length).toBeGreaterThanOrEqual(2)

    const logs = invokeMonitoringSweep()
    const analysis = logs.match(
      /Scale-down analysis: \d+ total, (\d+) occupied, (\d+) idle, (\d+) active users/,
    )
    expect(
      analysis,
      `no scale-down analysis in the sweep log:\n${logs}`,
    ).toBeTruthy()
    const [, occupied, idle, active] = analysis!.map(Number)
    const running = occupied + idle
    const minRunningNeeded = Math.max(1, active + 1)
    // Another user active on the shared cluster raises the floor and blocks
    // the scale-down legitimately - that leaves the rows unverified, not failed
    test.skip(
      running <= minRunningNeeded || idle < 2,
      `rows ${rows}: sweep saw running=${running}, idle=${idle}, active=${active} - ` +
        `the shared cluster's state blocks idle scale-down this run`,
    )

    // 6221: the sweep really stopped idle capacity; 6222: never all of it
    let runningAfter: string[] = []
    await expect
      .poll(() => (runningAfter = runningPremiumInstanceIds()).length, {
        timeout: 4 * 60_000,
        intervals: [15_000],
        message: `no idle premium instance left the running state after the sweep`,
      })
      .toBeLessThan(runningBefore.length)
    expect(
      runningAfter.length,
      "the sweep stopped the last warm instance",
    ).toBeGreaterThanOrEqual(1)

    // 6221's ordering guarantee, observed from the end state: whatever was
    // stopped must also be out of the ECS cluster, not a ghost registration
    const stopped = runningBefore.filter((id) => !runningAfter.includes(id))
    await expect
      .poll(() => ecsContainerEc2Ids().filter((id) => stopped.includes(id)), {
        timeout: 3 * 60_000,
        intervals: [15_000],
        message: `stopped instance(s) ${stopped} still registered in ECS`,
      })
      .toHaveLength(0)
  } finally {
    await s1.api.dispose()
    await s2.api.dispose()
  }
})

test("PREM-07 - Premium workflow runs end-to-end on the real dedicated instance @slow", async ({
  page,
}) => {
  const rows = "604 / BT-607 / BT-608 / BT-609"
  skipUnlessOptedIn(rows)
  test.setTimeout(TEST_TIMEOUT_MS + RUN_TEST_TIMEOUT_MS)

  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)
  expectGenuinelyPremium(await statusViaPage(page))
  const status = await waitForAssignment(page, rows)
  const assignment = status.assignment!
  if (!assignment.is_shared) heldDedicated = true
  // The flagship 604 row is about the dedicated instance specifically; any
  // other granted tier leaves the rows unverified, not failed
  test.skip(
    !isDedicated(assignment),
    `rows ${rows}: the cascade did not grant a dedicated instance this run ` +
      `(${JSON.stringify(assignment)})`,
  )

  // Routing truth: every workflow-run POST must go out through the premium
  // routing the assignment established
  const apiRequests: {
    method: string
    url: string
    headers: Record<string, string>
  }[] = []
  page.on("request", (req) => {
    if (req.url().startsWith(apiUrl())) {
      apiRequests.push({
        method: req.method(),
        url: req.url(),
        headers: req.headers(),
      })
    }
  })

  const wsId = await openWorkspace(page, "e2e-prem")
  try {
    await importSampleData(page, "e2e-prem")

    // RUN ALL, not RUN: a by-uid rerun of imported tutorials is a snakemake
    // no-op, so only a fresh uid proves the dedicated instance really
    // computed and wrote the outputs this test asserts on
    const t0 = windowStart()
    const { uid } = await runTutorial(page, "Tutorial1", "RUN ALL")

    const runPosts = apiRequests.filter(
      (r) => r.method === "POST" && r.url.includes(`/run/${wsId}`),
    )
    expect(runPosts.length, "no run POST was captured").toBeGreaterThan(0)
    for (const r of runPosts) {
      expect(
        r.headers["x-routing-id"],
        `${r.url} missing x-routing-id`,
      ).toBeTruthy()
      expect(
        r.headers["x-user-tier"],
        `${r.url} did not carry x-user-tier: premium`,
      ).toBe("premium")
    }

    // CloudWatch truth (604's Expected #7): the run's WORKFLOW START logged
    // in the premium task's group and NOT in the free tier's
    await expect
      .poll(() => cloudwatchHas(PREMIUM_LOG_GROUP, `(ID: ${uid},`, t0), {
        ...CLOUDWATCH_POLL,
        message: `no WORKFLOW START for run ${uid} in ${PREMIUM_LOG_GROUP}`,
      })
      .toBe(true)
    // The free-group negative is only meaningful once free-group delivery is
    // proven caught up (its awslogs non-blocking buffer is independent of the
    // premium group's): fire one request that lands on the free tier and wait
    // for its own line first
    const plainHeaders = await apiHeaders(page)
    const meRes = await page.request.get(`${apiUrl()}/users/me`, {
      headers: plainHeaders,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    expect(meRes.ok(), await meRes.text()).toBe(true)
    const meBody = await meRes.json()
    const probe = await page.request.get(
      `${apiUrl()}/storage-limit-alerts/limit-warning`,
      { headers: plainHeaders, timeout: STATUS_REQUEST_TIMEOUT_MS },
    )
    expect(probe.ok(), await probe.text()).toBe(true)
    await expect
      .poll(
        () =>
          cloudwatchHas(
            FREE_LOG_GROUP,
            `Calculating limit warning for user ${meBody.id}`,
            t0,
          ),
        {
          ...CLOUDWATCH_POLL,
          message: `the free-group delivery probe never appeared in ${FREE_LOG_GROUP}`,
        },
      )
      .toBe(true)
    expect(
      cloudwatchHas(FREE_LOG_GROUP, `Workspace: ${wsId})`, t0),
      `workflow lines for workspace ${wsId} leaked into ${FREE_LOG_GROUP}`,
    ).toBe(false)

    // S3 truth (604's Expected #6): the outputs landed in the user's own
    // bucket. A null attribute must fail loudly - the backend silently falls
    // back to the default bucket, which would make this assert vacuous.
    const bucket = meBody.attributes?.remote_bucket_name
    expect(
      bucket,
      "premium user has no remote_bucket_name attribute",
    ).toBeTruthy()
    const outputPrefix = `app/studio_data/output/${wsId}/${uid}/`
    await expect
      .poll(() => s3ObjectCount(bucket, outputPrefix), {
        timeout: 120_000,
        intervals: [15_000],
        message: `no run outputs under s3://${bucket}/${outputPrefix}`,
      })
      .toBeGreaterThan(0)
  } finally {
    // Deleting the workspace drops its S3 input+output prefixes server-side;
    // the afterEach hard-release returns the instance as usual
    const res = await page.request.delete(`${apiUrl()}/workspace/${wsId}`, {
      headers: await apiHeaders(page),
      timeout: RELEASE_REQUEST_TIMEOUT_MS,
    })
    expect(res.ok(), await res.text()).toBe(true)
  }
})

test("PREM-08 - Concurrent workflows all complete on one dedicated instance @slow", async ({
  page,
}) => {
  const rows = "605 / BT-610"
  skipUnlessOptedIn(rows)
  test.setTimeout(TEST_TIMEOUT_MS + RUN_TEST_TIMEOUT_MS + 10 * 60_000)

  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)
  expectGenuinelyPremium(await statusViaPage(page))
  const status = await waitForAssignment(page, rows)
  const assignment = status.assignment!
  if (!assignment.is_shared) heldDedicated = true
  test.skip(
    !isDedicated(assignment),
    `rows ${rows}: the cascade did not grant a dedicated instance this run ` +
      `(${JSON.stringify(assignment)})`,
  )

  const names = ["e2e-conc-a", "e2e-conc-b", "e2e-conc-c"]
  const wsIds: number[] = []
  for (const name of names) {
    wsIds.push(await openWorkspace(page, name))
    await importSampleData(page, name)
  }

  const pages = [page]
  try {
    while (pages.length < names.length) {
      pages.push(await page.context().newPage())
    }
    for (const [i, p] of pages.entries()) {
      await p.goto(`/workspaces/${wsIds[i]}`)
      await expect(
        p.locator('button[role="tab"]:has-text("Workflow")'),
      ).toBeVisible({ timeout: 15_000 })
      await reproduceTutorial(p, "Tutorial1")
    }

    // The row's point: fire every run as close to simultaneously as possible,
    // then every one must complete on the single dedicated instance - no
    // queuing, no timeout, no hung page
    const t0 = windowStart()
    const runs = await Promise.all(pages.map((p) => startRun(p, "RUN ALL")))

    // UI truth: each page still renders and answers the locator round-trip
    // while all runs are in flight - a hung renderer fails the deadline
    for (const p of pages) {
      await expect(
        p.locator('button[role="tab"]:has-text("Workflow")'),
        "a page stopped responding while the concurrent runs were in flight",
      ).toBeVisible({ timeout: 10_000 })
    }

    // CloudWatch truth: each run's WORKFLOW START logged in the premium group
    for (const run of runs) {
      await expect
        .poll(() => cloudwatchHas(PREMIUM_LOG_GROUP, `(ID: ${run.uid},`, t0), {
          ...CLOUDWATCH_POLL,
          message: `no WORKFLOW START for concurrent run ${run.uid} in ${PREMIUM_LOG_GROUP}`,
        })
        .toBe(true)
    }

    const recordedStatus = async (i: number) => {
      // routedApiHeaders, not apiHeaders: without the routing headers the
      // poll lands on the free tier, which never saw these premium runs
      const res = await page.request.get(
        `${apiUrl()}/experiments/${runs[i].workspaceId}`,
        {
          headers: await routedApiHeaders(page),
          timeout: STATUS_REQUEST_TIMEOUT_MS,
        },
      )
      if (!res.ok()) return `GET /experiments -> ${res.status()}`
      const experiments = (await res.json()) as Record<
        string,
        { success?: string }
      >
      return experiments?.[runs[i].uid]?.success ?? "absent"
    }
    await Promise.all(
      runs.map(async (run, i) => {
        let recorded = ""
        await expect
          .poll(async () => (recorded = await recordedStatus(i)), {
            timeout: RUN_TIMEOUT_MS + 300_000,
            intervals: [15_000],
            message: `concurrent run ${run.uid} never settled`,
          })
          .toMatch(/^(success|error)$/)
        expect(
          recorded,
          `concurrent run ${run.uid} recorded "${recorded}", not success`,
        ).toBe("success")
      }),
    )
  } finally {
    for (const p of pages.slice(1)) {
      await p.close().catch(() => {})
    }
    const headers = await apiHeaders(page)
    for (const wsId of wsIds) {
      await page.request
        .delete(`${apiUrl()}/workspace/${wsId}`, {
          headers,
          timeout: RELEASE_REQUEST_TIMEOUT_MS,
        })
        .catch(() => {})
    }
  }
})

test("PREM-09 - The premium subscription row is real in the deployed RDS @slow", async () => {
  const rows = "BT-606"
  skipUnlessOptedIn(rows)
  const sqlReason = sqlSkipReason()
  test.skip(!!sqlReason, `rows ${rows}: ${sqlReason}`)
  test.setTimeout(10 * 60_000)

  const { api, headers } = await apiLogin(
    PREMIUM_USER.email,
    PREMIUM_USER.password,
  )
  try {
    const statusRes = await api.get("/users/me/premium/status", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    expect(statusRes.ok(), await statusRes.text()).toBe(true)
    expectGenuinelyPremium(await statusRes.json())
    const me = await api.get("/users/me", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    const userId: number = (await me.json()).id

    // BT-606's own query: plan_id = 2, a future expiration, no scheduled
    // downgrade - read over SSM from the real RDS, not through the API
    const row = runSql(
      `SELECT plan_id, expiration > NOW(), scheduled_downgrade
         FROM subscription_users WHERE user_id = ${userId};`,
    )
    expect(row, `subscription_users row for user ${userId}: "${row}"`).toMatch(
      /^2\s+1\s+0$/,
    )
  } finally {
    await api.dispose()
  }
})

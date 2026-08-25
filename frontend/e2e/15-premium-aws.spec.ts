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
  awaitRunFinished,
  awsJson,
  cloudwatchHas,
  importSampleData,
  isLocalBaseUrl,
  login,
  openWorkspace,
  premiumTargetHealth,
  reproduceTutorial,
  routedApiHeaders,
  runSql,
  runTutorial,
  s3ObjectCount,
  skipWithoutCreds,
  sqlLiteral,
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

// The cascade's transient warming tier, reported by /premium/assign but
// deliberately 404'd by /premium/status.
const AUTOSCALING_POOL = "autoscaling-pool"

const CLUSTER = "development-optinist-cloud-cluster"
const PREMIUM_SERVICE = "development-premium-optinist-cloud-service"
const PREMIUM_MANAGER_LOG_GROUP = "/aws/lambda/development-premium-manager"
const PREMIUM_CLEANUP_LOG_GROUP = "/aws/lambda/development-premium-cleanup"
const REGION = "ap-northeast-1"

// A cold assign starts EC2 capacity + an ECS task: minutes, not seconds
const ASSIGN_TIMEOUT_MS = 15 * 60_000
const TEST_TIMEOUT_MS = ASSIGN_TIMEOUT_MS + 10 * 60_000
// The premium endpoints do real AWS work in-request (ALB rules, scale-up,
// teardown), so the config's 15s actionTimeout aborts them mid-flight -
// observed 2026-08-19: every assign/release timed out client-side while
// completing server-side. Each call names its own budget instead.
const ASSIGN_REQUEST_TIMEOUT_MS = 300_000
// The assign call blocks for the whole cold standby start, and the assignment
// row becomes visible to /premium/status DURING it - so the row can be asserted
// minutes before the call returns and logs its line. These polls need the
// assign budget, not the generic CloudWatch one.
const ASSIGN_LOG_POLL = { timeout: 8 * 60_000, intervals: [15_000] }
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

// The per-user target group's health states. A dedicated assignment goes live
// (DB row, ALB rule, target group) before the premium ECS task on that instance
// serves traffic, so a workflow driven through the ALB too early answers 502.
// The premium service's task on a given instance, as the console's Tasks tab
// shows it. Rows 1203/1204 are written against that view.
function premiumTaskOn(
  instanceId: string,
): { taskArn: string; lastStatus: string } | undefined {
  const arns = awsJson<{ taskArns: string[] }>(
    `ecs list-tasks --cluster ${CLUSTER} --service-name ${PREMIUM_SERVICE}`,
  ).taskArns
  if (!arns.length) return undefined
  const tasks = awsJson<{
    tasks: {
      taskArn: string
      lastStatus: string
      containerInstanceArn?: string
    }[]
  }>(`ecs describe-tasks --cluster ${CLUSTER} --tasks ${arns.join(" ")}`).tasks
  const cis = tasks
    .map((t) => t.containerInstanceArn)
    .filter((a): a is string => !!a)
  if (!cis.length) return undefined
  const owners = awsJson<{
    containerInstances: {
      containerInstanceArn: string
      ec2InstanceId: string
    }[]
  }>(
    `ecs describe-container-instances --cluster ${CLUSTER} ` +
      `--container-instances ${cis.join(" ")}`,
  ).containerInstances
  const mine = owners.find((c) => c.ec2InstanceId === instanceId)
  if (!mine) return undefined
  return tasks.find((t) => t.containerInstanceArn === mine.containerInstanceArn)
}

function premiumTaskStatusOn(instanceId: string): string {
  return premiumTaskOn(instanceId)?.lastStatus ?? "none"
}

// Waiting out the task placement is not the row under test: a cluster that
// never gets a premium target serving leaves the workflow rows unverified,
// exactly as skipForNoCapacity treats a failed placement.
async function skipUnlessPremiumTargetHealthy(rows: string, userId: number) {
  const deadline = Date.now() + 5 * 60_000
  let states: string[] = []
  for (;;) {
    states = premiumTargetHealth(userId)
    if (states.includes("healthy")) return
    if (Date.now() > deadline) break
    await new Promise((r) => setTimeout(r, 15_000))
  }
  test.skip(
    true,
    `rows ${rows}: premium-${userId}-tg never reported a healthy target ` +
      `(states: ${states.join(",") || "none"}) - the dev cluster could not ` +
      `keep a premium task serving; rerun when it has free CPU`,
  )
}

// Premium instances not yet at rest: an assign racing a still-stopping
// instance can land on it, so cooling waits for none of these.
function transitioningPremiumInstanceCount(): number {
  return Number(
    execSync(
      `aws ec2 describe-instances --region ${REGION} ` +
        `--filters "Name=tag:Name,Values=development-premium-*" ` +
        `"Name=instance-state-name,Values=pending,running,stopping,shutting-down" ` +
        `--query 'length(Reservations[].Instances[])' --output text`,
      { timeout: 30_000 },
    )
      .toString()
      .trim(),
  )
}

// The cascade grants the shared tier instantly while a warm shared-marked
// instance runs, which starves the dedicated-tier rows whenever an earlier
// test warmed the pool. A shared grant with no DB owner of premium capacity
// is pool temperature, not a capacity limit: release it, park the pool cold
// (desired 0, instances stopped), and let one re-assign take the cold-start
// migrate path to a dedicated grant. A shared grant that survives the cooled
// re-assign, or capacity someone else owns, still skips with its reason.
async function dedicatedAssignmentCoolingIfNeeded(
  page: Page,
  rows: string,
): Promise<NonNullable<PremiumStatus["assignment"]>> {
  let assignment = (await waitForAssignment(page, rows)).assignment!
  if (isDedicated(assignment)) return assignment

  const release = await page.request.delete(
    `${apiUrl()}/users/me/premium/assign`,
    { headers: await apiHeaders(page), timeout: RELEASE_REQUEST_TIMEOUT_MS },
  )
  expect(release.ok(), await release.text()).toBe(true)

  const sqlReason = sqlSkipReason()
  const owners = sqlReason
    ? `unknown (${sqlReason})`
    : runSql(
        "SELECT COUNT(*) FROM premium_user_assignments WHERE is_standby = 0" +
          " AND status IN ('active', 'migrating', 'terminating');",
      ).trim()
  test.skip(
    owners !== "0",
    `rows ${rows}: the cascade granted ${JSON.stringify(assignment)} and the ` +
      `pool cannot be cooled: premium capacity owners=${owners}`,
  )

  const running = runningPremiumInstanceIds()
  if (running.length) {
    execSync(
      `aws ecs update-service --cluster ${CLUSTER} ` +
        `--service ${PREMIUM_SERVICE} --desired-count 0 --region ${REGION}`,
      { timeout: 30_000 },
    )
    execSync(
      `aws ec2 stop-instances --instance-ids ${running.join(" ")} ` +
        `--region ${REGION}`,
      { timeout: 30_000 },
    )
  }
  await expect
    .poll(() => transitioningPremiumInstanceCount(), {
      timeout: 5 * 60_000,
      intervals: [15_000],
      message: `premium instances never finished stopping while cooling for rows ${rows}`,
    })
    .toBe(0)

  // A mount with no assignment re-runs the provider's assign flow
  await page.reload()
  assignment = (await waitForAssignment(page, rows)).assignment!
  test.skip(
    !isDedicated(assignment),
    `rows ${rows}: the cascade did not grant a dedicated instance even from ` +
      `a cold pool (${JSON.stringify(assignment)})`,
  )
  return assignment
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
      `--filters "Name=tag:Name,Values=development-premium-*" ` +
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

// The latest matching line since sinceMs, from the manager Lambda's own log
// group; empty string until log propagation delivers it.
function latestManagerLine(pattern: string, sinceMs: number): string {
  const out = execSync(
    `aws logs filter-log-events --log-group-name ${PREMIUM_MANAGER_LOG_GROUP} ` +
      `--start-time ${sinceMs} --filter-pattern '"${pattern}"' ` +
      `--query 'events[-1].message' --output text --region ${REGION}`,
    { timeout: 30_000, stdio: ["pipe", "pipe", "pipe"] },
  )
    .toString()
    .trim()
  return out === "None" ? "" : out
}

// The idle scale-down lives in the monitoring Lambda's scheduled action, so
// the test fires the same event cron does instead of waiting on the cron.
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
  // The AWS side (ECS, Lambda, EC2 stops in the cooling path) is hardcoded to
  // development, so a non-development BASE_URL would mutate one environment's
  // accounts while cooling another's infrastructure.
  expect(
    process.env.BASE_URL || "",
    "this lane only runs against the development environment",
  ).toContain("development-optinist")
  // A pass on retry hides real-AWS flakiness from the sign-off sheet
  expect(test.info().project.retries, "run this lane with --retries 0").toBe(0)
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

// The app rewrites premium_shared from its own /premium/status poll, whose
// interval is 30s, and the cascade can migrate a pool assignment to dedicated
// mid-test - so compare the stored tier against a live status read rather than
// an earlier snapshot, over a window wider than that poll.
async function expectStoredTierMatchesStatus(page: Page) {
  await expect
    .poll(
      async () => {
        const shared = (await statusViaPage(page)).assignment?.is_shared
        if (typeof shared !== "boolean") return "no assignment on /status"
        const stored = await page.evaluate(() =>
          localStorage.getItem("premium_shared"),
        )
        return stored === String(shared)
          ? "match"
          : `premium_shared=${stored}, status is_shared=${shared}`
      },
      { timeout: 90_000, intervals: [5_000] },
    )
    .toBe("match")
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
    if (body.assigned && body.instance_id !== AUTOSCALING_POOL) {
      return body as {
        assigned: true
        is_shared: boolean
        instance_id?: string
      }
    }
    // Either still scaling, or parked on the pool tier - which /premium/status
    // deliberately 404s so the client keeps assigning until a real instance is
    // free. Settling here would assert against a tier status never reports.
    expect(
      body.scaling_in_progress || body.instance_id === AUTOSCALING_POOL,
      JSON.stringify(body),
    ).toBeTruthy()
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
      // The ALB half of the invariant: a hard release can delete the row and
      // rule but fail the TG deletion, stranding a rule-less TG no sweep can
      // find (issue #814) - so only a genuine NotFound counts as absence.
      const me = await api.get("/users/me", {
        headers,
        timeout: STATUS_REQUEST_TIMEOUT_MS,
      })
      const userId: number = (await me.json()).id
      try {
        execSync(
          `aws elbv2 describe-target-groups --names premium-${userId}-tg ` +
            `--region ${REGION}`,
          { timeout: 30_000, stdio: ["pipe", "pipe", "pipe"] },
        )
        throw new Error(
          `the lane finished with premium-${userId}-tg still existing`,
        )
      } catch (e) {
        const msg =
          (e as Error).message + String((e as { stderr?: Buffer }).stderr || "")
        if (!msg.includes("TargetGroupNotFound")) throw e
      }
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
  await expectStoredTierMatchesStatus(page)

  // CloudWatch truth: the assignment left its lines in the public tier's log
  // group (the assign endpoint always answers pre-routing), and the login-time
  // limit-warning calculation logged on the tier that served the login
  const me = await page.request.get(`${apiUrl()}/users/me`, {
    headers: await apiHeaders(page),
    timeout: STATUS_REQUEST_TIMEOUT_MS,
  })
  const userId: number = (await me.json()).id
  // Two racers can complete a cold-start assignment: a client /assign call
  // that returns assigned=True logs in the public group, but when the manager
  // Lambda's sweep migrates the user off the warming pool before any client
  // call completes, no service-side line is ever emitted and the only
  // CloudWatch evidence is the Lambda's own migration line (observed both
  // ways on 2026-08-20).
  const sweepMigrated = () =>
    cloudwatchHas(
      PREMIUM_MANAGER_LOG_GROUP,
      `Migrated user ${userId} from autoscaling-pool`,
      t0,
    )
  await expect
    .poll(
      () =>
        cloudwatchHas(
          PUBLIC_LOG_GROUP,
          `[premium-assign] user=${userId} assigned=True`,
          t0,
        ) || sweepMigrated(),
      {
        ...ASSIGN_LOG_POLL,
        message: `no [premium-assign] success line for user ${userId} in ${PUBLIC_LOG_GROUP} and no migration line in ${PREMIUM_MANAGER_LOG_GROUP}`,
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
        ) || sweepMigrated(),
      {
        ...ASSIGN_LOG_POLL,
        message: `no service-side assign line for user ${userId} in ${PUBLIC_LOG_GROUP} and no migration line in ${PREMIUM_MANAGER_LOG_GROUP}`,
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

    // Row 1203: the grant is backed by a premium ECS task really RUNNING on the
    // instance it named, within the sheet's 8 minutes. A warm pool means the
    // task predates the assignment rather than being created by it, which is
    // why the assertion is on the task's state and not on its age.
    const grantedInstance = status.assignment!.instance_id
    if (grantedInstance?.startsWith("i-")) {
      await expect
        .poll(() => premiumTaskStatusOn(grantedInstance), {
          timeout: 480_000,
          intervals: [15_000],
          message:
            `no RUNNING premium task on ${grantedInstance} within 8 minutes ` +
            `of the assignment`,
        })
        .toBe("RUNNING")
    }

    // Hard release (the logout path): the row must be gone immediately,
    // not soft-released into the 120s grace. freshWindow, not windowStart:
    // the previous test's afterEach hard-released this same user, and its
    // line must not satisfy this assert.
    const tRelease = freshWindow(
      PUBLIC_LOG_GROUP,
      `Released premium user ${userId}`,
    )
    const tReleasing = freshWindow(
      PUBLIC_LOG_GROUP,
      `Releasing (hard) premium user ${userId}`,
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
    // Row 1207 wants both halves: the request going out as well as the instance
    // coming back. This is the "Releasing (hard) premium user {id} (uid: ...)
    // from assigned instance" line that precedes it.
    await expect
      .poll(
        () =>
          cloudwatchHas(
            PUBLIC_LOG_GROUP,
            `Releasing (hard) premium user ${userId}`,
            tReleasing,
          ),
        {
          ...CLOUDWATCH_POLL,
          message: `no "Releasing (hard)" line for user ${userId} in ${PUBLIC_LOG_GROUP}`,
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

  await expectStoredTierMatchesStatus(page)
})

test("PREM-04 - A browser-close beacon soft-releases; reopening inside the grace restores the same row @slow", async () => {
  const rows = "6208 / BT-615 / 603"
  skipUnlessOptedIn(rows)
  test.setTimeout(TEST_TIMEOUT_MS)

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

    // The sheet's middleware line, asserted causally and BEFORE the beacon:
    // the update is throttled to once per user per minute in a per-worker
    // cache, and the beacon's logged-out mark suppresses it entirely, so wait
    // the throttle out and then let one request produce the line.
    await new Promise((r) => setTimeout(r, 61_000))
    const tActivity = Date.now() - 5_000
    const probe = await api.get("/users/me", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    expect(probe.ok(), await probe.text()).toBe(true)
    await expect
      .poll(
        () =>
          cloudwatchHas(
            PUBLIC_LOG_GROUP,
            `Updated premium activity for user ${userId}`,
            tActivity,
          ),
        {
          ...CLOUDWATCH_POLL,
          message: `no middleware premium-activity line for user ${userId} in ${PUBLIC_LOG_GROUP} after an unthrottled request`,
        },
      )
      .toBe(true)

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

test("PREM-07 - Premium workflow runs end-to-end on the real dedicated instance @slow", async ({
  page,
}) => {
  const rows = "604 / BT-607 / BT-608 / BT-609"
  skipUnlessOptedIn(rows)
  test.setTimeout(TEST_TIMEOUT_MS + RUN_TEST_TIMEOUT_MS)

  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)
  expectGenuinelyPremium(await statusViaPage(page))
  // The flagship 604 row is about the dedicated instance specifically: cool
  // the pool for a dedicated grant when a warm shared one stands in the way
  const assignment = await dedicatedAssignmentCoolingIfNeeded(page, rows)
  heldDedicated = true

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

  // 604's premise is a dedicated instance that really serves: gate on its
  // target group being healthy before driving any workflow through the ALB.
  const idRes = await page.request.get(`${apiUrl()}/users/me`, {
    headers: await apiHeaders(page),
    timeout: STATUS_REQUEST_TIMEOUT_MS,
  })
  expect(idRes.ok(), await idRes.text()).toBe(true)
  const premiumUserId = (await idRes.json()).id
  await skipUnlessPremiumTargetHealthy(rows, premiumUserId)

  // Row 540: the fresh assignment's own baseline in the PREMIUM table
  const countSql =
    `SELECT active_workflow_count FROM premium_user_assignments ` +
    `WHERE user_id = ${premiumUserId};`
  expect(runSql(countSql), "row 540: fresh-assignment baseline").toBe("0")

  const wsId = await openWorkspace(page, "e2e-prem")
  try {
    await importSampleData(page, "e2e-prem")

    // RUN ALL, not RUN: a by-uid rerun of imported tutorials is a snakemake
    // no-op, so only a fresh uid proves the dedicated instance really
    // computed and wrote the outputs this test asserts on
    const t0 = windowStart()
    await reproduceTutorial(page, "Tutorial1")
    const { workspaceId: runWs, uid } = await startRun(page, "RUN ALL")

    // Rows 542/543's live half: the run really holds a slot in the premium
    // table while it executes, and releases it when it completes
    await expect
      .poll(() => runSql(countSql), {
        timeout: 180_000,
        intervals: [10_000],
        message: "active_workflow_count never reached 1 during the run",
      })
      .toBe("1")
    await awaitRunFinished(page, "Tutorial1", runWs, uid)
    await expect
      .poll(() => runSql(countSql), {
        timeout: 120_000,
        intervals: [10_000],
        message: "active_workflow_count did not return to 0 after the run",
      })
      .toBe("0")

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
  const rows = "605 / BT-610 / 723"
  skipUnlessOptedIn(rows)
  // Row 723 rides along at the end, so its database reads are a precondition
  // of the whole test rather than a surprise twenty minutes in.
  const sqlReason = sqlSkipReason()
  test.skip(!!sqlReason, `rows ${rows}: ${sqlReason}`)
  test.setTimeout(TEST_TIMEOUT_MS + RUN_TEST_TIMEOUT_MS + 22 * 60_000)

  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)
  expectGenuinelyPremium(await statusViaPage(page))
  // 605 needs the dedicated tier: cool the pool when a warm shared grant
  // stands in the way
  const assignment = await dedicatedAssignmentCoolingIfNeeded(page, rows)
  heldDedicated = true

  // 605's premise is the same dedicated instance serving three runs: gate on its
  // target group being healthy before driving any workflow through the ALB.
  const idRes = await page.request.get(`${apiUrl()}/users/me`, {
    headers: await apiHeaders(page),
    timeout: STATUS_REQUEST_TIMEOUT_MS,
  })
  expect(idRes.ok(), await idRes.text()).toBe(true)
  await skipUnlessPremiumTargetHealthy(rows, (await idRes.json()).id)

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
      // poll lands on the free tier, which never saw these premium runs.
      // A slow answer is not a verdict: three runs computing on one t3.large
      // can outlast the request budget, so let the poll retry instead of
      // failing the row - the poll's own deadline still bounds it.
      let res
      try {
        res = await page.request.get(
          `${apiUrl()}/experiments/${runs[i].workspaceId}`,
          {
            headers: await routedApiHeaders(page),
            timeout: STATUS_REQUEST_TIMEOUT_MS,
          },
        )
      } catch (e) {
        return `GET /experiments threw: ${(e as Error).message.split("\n")[0]}`
      }
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

    // Row 723 rides on the back of these three runs because this is the only
    // place three FINISHED experiments exist at once. The sync job only ever
    // looks at records with success = 1 (_get_pending_experiments), imported
    // sample data is success = 0, and every lane that produces a real one
    // deletes its workspace on the way out - so publishing three of those,
    // here, before the teardown below, is the batch the row is about.
    const routed = await routedApiHeaders(page)
    const owned = runs
      .map(
        (r) =>
          `(workspace_id = ${r.workspaceId} AND uid = '${sqlLiteral(r.uid)}')`,
      )
      .join(" OR ")
    const recordIds = runSql(
      `SELECT id FROM experiment_records
         WHERE success = 1 AND publish_status = 0 AND (${owned});`,
    )
      .split(/\s+/)
      .filter(Boolean)
    // Not a setup step: a finished run that the sync job would not pick up is
    // itself the failure, and this is the only assertion that says so.
    expect(
      recordIds.length,
      `only ${recordIds.length} of the ${runs.length} finished runs left a ` +
        `record the sync job would accept (success = 1, unpublished)`,
    ).toBe(runs.length)
    const pendingCount = () =>
      runSql(
        `SELECT COUNT(*) FROM experiment_records
           WHERE id IN (${recordIds.join(",")})
             AND local_sync_status = 'pending';`,
      )

    const published = await page.request.post(
      `${apiUrl()}/api/dataview/multiple/publish/on`,
      {
        headers: routed,
        data: recordIds.map(Number),
        timeout: RELEASE_REQUEST_TIMEOUT_MS,
      },
    )
    expect(published.ok(), await published.text()).toBe(true)
    try {
      expect(
        pendingCount(),
        `publishing ${recordIds.length} records together marks every one of ` +
          `them pending, which is the state the sync job drains`,
      ).toBe(String(recordIds.length))

      // The job is on a 5-minute interval, so waiting for it to come round is
      // slow; waiting for it to FINISH the batch is not. A batch needing a
      // second pass would sit part-drained until the next interval, which is
      // the failure row 723 is really about (the cap is 50 per run, 10
      // concurrent, so three must never split).
      await expect
        .poll(() => pendingCount(), {
          timeout: 12 * 60_000,
          intervals: [10_000],
          message: `no validation run touched the ${recordIds.length} published records`,
        })
        .not.toBe(String(recordIds.length))
      await expect
        .poll(() => pendingCount(), {
          timeout: 60_000,
          intervals: [5_000],
          message:
            `the batch drained partially and stalled - one run must clear all ` +
            `${recordIds.length}`,
        })
        .toBe("0")
    } finally {
      // The workspaces go below, which would take these records with them, but
      // unpublish first so nothing is briefly public on the way out.
      const reverted = await page.request.post(
        `${apiUrl()}/api/dataview/multiple/publish/off`,
        {
          headers: routed,
          data: recordIds.map(Number),
          timeout: RELEASE_REQUEST_TIMEOUT_MS,
        },
      )
      expect(reverted.ok(), await reverted.text()).toBe(true)
    }
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

    let a1: { is_shared?: boolean; instance_id?: string } =
      await assignUntilSettled(post(s1), rows)
    let a2: { is_shared?: boolean; instance_id?: string } =
      await assignUntilSettled(post(s2), rows)
    const distinct = () =>
      isDedicated(a1) && isDedicated(a2) && a1.instance_id !== a2.instance_id

    // The immediate cascade grants at most one dedicated - the second user
    // lands shared. A shared user alone on their own instance is flipped to
    // dedicated in place by the sweep's fix_incorrect_is_shared_flags, so
    // when the users hold distinct instances, run that reconciliation and
    // re-read instead of skipping a state that is one sweep away.
    if (!distinct() && a1.instance_id !== a2.instance_id) {
      invokeMonitoringSweep()
      const statusAssignment = async (s: typeof s1) => {
        const res = await s.api.get("/users/me/premium/status", {
          headers: s.headers,
          timeout: STATUS_REQUEST_TIMEOUT_MS,
        })
        return res.ok() ? ((await res.json()).assignment ?? null) : null
      }
      const deadline = Date.now() + 2 * 60_000
      for (;;) {
        a1 = (await statusAssignment(s1)) ?? a1
        a2 = (await statusAssignment(s2)) ?? a2
        if (distinct() || Date.now() > deadline) break
        await new Promise((r) => setTimeout(r, 15_000))
      }
    }
    if (!a1.is_shared || !a2.is_shared) heldDedicated = true
    const distinctDedicated = distinct()

    // Sampled while the users still hold them. A hard release runs
    // scale_down_if_possible() inline and then, with no premium user left,
    // converts every remaining idle instance to standby - so both releases
    // return with the pool already cold and an after-the-fact sample reads 0.
    const runningBefore = runningPremiumInstanceIds()

    const tRelease = Date.now() - 5_000
    await release(s1)
    await release(s2)

    // The release's own scale-down prints its analysis, so this verifies every
    // run - including one where the cascade could not grant two distinct
    // instances and the outcome half below has to skip.
    let analysisLine = ""
    await expect
      .poll(
        () =>
          (analysisLine = latestManagerLine("Scale-down analysis:", tRelease)),
        {
          ...CLOUDWATCH_POLL,
          message: `no Scale-down analysis line in ${PREMIUM_MANAGER_LOG_GROUP} after the releases`,
        },
      )
      .toContain("Scale-down analysis:")
    expect(
      analysisLine.match(
        /Scale-down analysis: \d+ total, (\d+) occupied, (\d+) idle, (\d+) active users/,
      ),
      analysisLine,
    ).toBeTruthy()

    test.skip(
      !distinctDedicated,
      `rows ${rows}: no two distinct dedicated instances this run, even after ` +
        `the solo-shared reconciliation sweep (${JSON.stringify([a1, a2])}) - ` +
        `sweep analysis verified; pre-stage a second instance to verify manually`,
    )
    expect(runningBefore.length).toBeGreaterThanOrEqual(2)

    // 6221: the decision named real instances, and they were the ones the two
    // users had been holding - not some unrelated capacity.
    let stopLine = ""
    await expect
      .poll(() => (stopLine = latestManagerLine("idle instances:", tRelease)), {
        ...CLOUDWATCH_POLL,
        message:
          `no scale-down stop decision in ${PREMIUM_MANAGER_LOG_GROUP} after ` +
          `releasing both dedicated instances; analysis was: ${analysisLine}`,
      })
      .toContain("idle instances:")
    const stoppedIds = [...stopLine.matchAll(/i-[0-9a-f]+/g)].map((m) => m[0])
    expect(stoppedIds.length, stopLine).toBeGreaterThan(0)
    expect(
      runningBefore,
      `${stopLine} stopped an instance the released users never held`,
    ).toEqual(expect.arrayContaining(stoppedIds))

    // 6222: the same decision spared one. The pool still ends cold, because a
    // hard release with no premium users left additionally converts the
    // remainder to standby - but that is the logout path, not scale-down
    // stripping every idle instance in a single decision.
    expect(
      stoppedIds.length,
      `scale-down stopped every idle instance at once: ${stopLine}`,
    ).toBeLessThan(runningBefore.length)

    // The decision is not the outcome: the named instances really left the
    // running state, and left the ECS cluster with them rather than lingering
    // as ghost registrations.
    await expect
      .poll(
        () =>
          runningPremiumInstanceIds().filter((id) => stoppedIds.includes(id)),
        {
          timeout: 5 * 60_000,
          intervals: [15_000],
          message: `scale-down named ${stoppedIds} but they are still running`,
        },
      )
      .toHaveLength(0)
    await expect
      .poll(
        () => ecsContainerEc2Ids().filter((id) => stoppedIds.includes(id)),
        {
          timeout: 3 * 60_000,
          intervals: [15_000],
          message: `stopped instance(s) ${stoppedIds} still registered in ECS`,
        },
      )
      .toHaveLength(0)
  } finally {
    await s1.api.dispose()
    await s2.api.dispose()
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

// Row 6226: the database's picture of the premium fleet has to follow AWS.
// TestReconcileInstanceStates drives the reconcile transaction directly, so
// what stays unproven is everything around it - that terminating a real EC2
// reaches the Cleanup Lambda through the EventBridge rule at all, and that the
// row really disappears. The standby is the only premium instance owned by
// nobody, so it is the only one this may destroy; the sweep at the end buys
// back the one it consumed.
test("PREM-10 - Terminating a premium instance reconciles its database row away @slow", async () => {
  const rows = "6226"
  skipUnlessOptedIn(rows)
  const sqlReason = sqlSkipReason()
  test.skip(!!sqlReason, `rows ${rows}: ${sqlReason}`)
  test.setTimeout(20 * 60_000)

  const instanceId = runSql(
    `SELECT instance_id FROM premium_user_assignments
       WHERE user_id IS NULL AND is_standby = 1 AND status = 'active'
       ORDER BY id DESC LIMIT 1;`,
  )
  test.skip(
    !/^i-[0-9a-f]+$/.test(instanceId),
    `rows ${rows}: no unowned standby row to reconcile ("${instanceId}") - ` +
      `terminating an instance a user holds is not this test's to do`,
  )
  const rowsFor = () =>
    runSql(
      `SELECT COUNT(*) FROM premium_user_assignments
         WHERE instance_id = '${instanceId}';`,
    )
  expect(
    rowsFor(),
    `the database must start out holding a row for standby ${instanceId}, ` +
      `or its disappearance proves nothing`,
  ).toBe("1")

  const since = Date.now() - 5_000
  awsJson(`ec2 terminate-instances --instance-ids ${instanceId}`)
  try {
    // The event-driven path, not the hourly walk: this line only comes from
    // the reconcile_instance action the EventBridge rule delivers.
    await expect
      .poll(
        () =>
          cloudwatchHas(
            PREMIUM_CLEANUP_LOG_GROUP,
            `Targeted instance reconciliation for ${instanceId}`,
            since,
          ),
        {
          timeout: 10 * 60_000,
          intervals: [15_000],
          message:
            `no targeted reconciliation for ${instanceId} in ` +
            `${PREMIUM_CLEANUP_LOG_GROUP} after terminating it`,
        },
      )
      .toBe(true)
    await expect
      .poll(() => rowsFor(), {
        timeout: 5 * 60_000,
        intervals: [15_000],
        message: `the row for terminated ${instanceId} was never reconciled away`,
      })
      .toBe("0")
  } finally {
    invokeMonitoringSweep()
  }
})

// Row 6204: two premium users assigning at the same moment must not corrupt the
// pool. TestConcurrentAssignLock and the GET_LOCK integration lane pin the lock
// itself; what is unproven is the end state on the real cascade, where every
// contended assign now falls through to a tier the row's literal "same instance,
// one shared" outcome never describes (the sheet's own [FLAG: codebase]). So
// this asserts the invariant instead of the wording: one row per user, and no
// two users holding the same instance as dedicated.
test("PREM-11 - Two users assigning at once never double-assign the pool @slow", async () => {
  const rows = "6204"
  skipUnlessOptedIn(rows)
  test.skip(
    !PREMIUM2_USER.email || !PREMIUM2_USER.password,
    `rows ${rows}: TEST_PREMIUM2_EMAIL/TEST_PREMIUM2_PASSWORD not set - a race needs two users`,
  )
  const sqlReason = sqlSkipReason()
  test.skip(!!sqlReason, `rows ${rows}: ${sqlReason}`)
  test.setTimeout(TEST_TIMEOUT_MS)

  const realRows = (where: string) =>
    runSql(
      `SELECT COUNT(*) FROM premium_user_assignments
         WHERE is_standby = 0 AND status IN ('active', 'pending_release')
           AND ${where};`,
    )
  // Someone else's assignment would make every count below ambiguous.
  expect(
    realRows("1 = 1"),
    "the pool must start with no real user assignments - another tester or a " +
      "lane that did not clean up still holds premium capacity",
  ).toBe("0")

  const s1 = await apiLogin(PREMIUM_USER.email, PREMIUM_USER.password)
  const s2 = await apiLogin(PREMIUM2_USER.email, PREMIUM2_USER.password)
  try {
    const userId = async (s: typeof s1) => {
      const me = await s.api.get("/users/me", {
        headers: s.headers,
        timeout: STATUS_REQUEST_TIMEOUT_MS,
      })
      expect(me.ok(), await me.text()).toBe(true)
      return (await me.json()).id as number
    }
    const [u1, u2] = [await userId(s1), await userId(s2)]
    expect(u1, "the two premium accounts must be different users").not.toBe(u2)

    const post = (s: typeof s1) => () =>
      s.api.post("/users/me/premium/assign", {
        headers: s.headers,
        timeout: ASSIGN_REQUEST_TIMEOUT_MS,
      })

    // The race itself: both in flight before either has returned. Anything
    // sequential here would exercise the uncontended path and prove nothing.
    const [first, second] = await Promise.all([post(s1)(), post(s2)()])
    for (const res of [first, second]) {
      expect(res.status(), await res.text()).toBeLessThan(500)
    }

    // The row's own step 2: let both invocations settle, then read the
    // database. Re-assigning to settle would only meet the endpoint's
    // 20-second per-user throttle ("Assignment request too frequent"), and a
    // fresh uncontended assign proves nothing about the race anyway.
    await expect
      .poll(() => realRows(`user_id IN (${u1}, ${u2})`), {
        timeout: ASSIGN_TIMEOUT_MS,
        intervals: [15_000],
        message: `the two racing users never settled into assignments`,
      })
      .toBe("2")
    if (
      runSql(
        `SELECT COUNT(*) FROM premium_user_assignments
           WHERE user_id IN (${u1}, ${u2}) AND is_standby = 0 AND is_shared = 0
             AND status IN ('active', 'pending_release')
             AND instance_id <> '${AUTOSCALING_POOL}';`,
      ) !== "0"
    ) {
      heldDedicated = true
    }

    // Both users really hold capacity, one row each: a race that dropped one
    // user, or gave one of them two rows, lands here.
    expect(realRows(`user_id = ${u1}`), `user ${u1} holds one assignment`).toBe(
      "1",
    )
    expect(realRows(`user_id = ${u2}`), `user ${u2} holds one assignment`).toBe(
      "1",
    )
    expect(
      realRows(`user_id IS NULL`),
      "the race left an ownerless real assignment row behind",
    ).toBe("0")

    // The corruption the lock exists to prevent: two users each told an
    // instance is theirs alone.
    expect(
      runSql(
        `SELECT COUNT(*) FROM (
           SELECT instance_id FROM premium_user_assignments
             WHERE is_standby = 0 AND is_shared = 0
               AND status IN ('active', 'pending_release')
               AND instance_id <> '${AUTOSCALING_POOL}'
             GROUP BY instance_id HAVING COUNT(*) > 1
         ) AS doubled;`,
      ),
      `instances held as dedicated by more than one user after the race ` +
        `(${runSql(
          `SELECT user_id, instance_id, is_shared FROM premium_user_assignments
             WHERE user_id IN (${u1}, ${u2}) AND is_standby = 0;`,
        ).replace(/\s+/g, " ")})`,
    ).toBe("0")
  } finally {
    for (const s of [s1, s2]) {
      await s.api
        .delete("/users/me/premium/assign", {
          headers: s.headers,
          timeout: RELEASE_REQUEST_TIMEOUT_MS,
        })
        .catch(() => {})
      await s.api.dispose()
    }
  }
})

// Row 6216: the premium tier's own recovery. PremiumRetriggerAssign.test.tsx
// covers the frontend half against mocks - a 502 flips the routing state to
// DEGRADED and a later 200 from the same instance heals it without a re-assign.
// What stayed manual is the ECS half: stopping the task the user is really
// routed to and waiting for the replacement to serve. It sits in the @prem lane
// rather than the disruptive one because it only ever touches the premium test
// user's own dedicated instance, and skips outright if the grant is shared.
test("PREM-12 - Stopping a user's premium task brings a replacement back to healthy @slow", async () => {
  const rows = "6216"
  skipUnlessOptedIn(rows)
  test.setTimeout(TEST_TIMEOUT_MS)

  const { api, headers } = await apiLogin(
    PREMIUM_USER.email,
    PREMIUM_USER.password,
  )
  try {
    const me = await api.get("/users/me", {
      headers,
      timeout: STATUS_REQUEST_TIMEOUT_MS,
    })
    expect(me.ok(), await me.text()).toBe(true)
    const userId: number = (await me.json()).id

    const assignment = await assignUntilSettled(
      () =>
        api.post("/users/me/premium/assign", {
          headers,
          timeout: ASSIGN_REQUEST_TIMEOUT_MS,
        }),
      rows,
    )
    test.skip(
      assignment.is_shared || !assignment.instance_id,
      `rows ${rows}: the cascade granted ${JSON.stringify(assignment)} - ` +
        `stopping the task on a shared instance would take its other tenants ` +
        `down with it`,
    )
    heldDedicated = true
    const instanceId = assignment.instance_id!
    await skipUnlessPremiumTargetHealthy(rows, userId)

    const before = premiumTaskOn(instanceId)
    expect(
      before?.taskArn,
      `no premium task on ${instanceId} to stop`,
    ).toBeTruthy()

    awsJson(
      `ecs stop-task --cluster ${CLUSTER} --task ${before!.taskArn} ` +
        `--reason e2e-row-6216`,
    )

    // ECS replaces it with no re-assign from the user: recovery is the
    // scheduler's job, not the client's. The target's health is sampled all
    // the way through rather than asserted to dip - the per-user group health
    // checks every 30s and needs three consecutive failures, so a replacement
    // that lands inside a minute is invisible to the ALB by construction
    // (measured 2026-08-25: stopped 12:24:37, replacement RUNNING 12:25:09).
    // Whether a window opens at all is placement latency, so it is reported,
    // not required; the 502-to-DEGRADED half of the row belongs to
    // PremiumRetriggerAssign.test.tsx, which owns what the client does if one
    // does slip through.
    const health: string[] = []
    let after: ReturnType<typeof premiumTaskOn>
    await expect
      .poll(
        () => {
          health.push(premiumTargetHealth(userId).join("+") || "none")
          after = premiumTaskOn(instanceId)
          return (
            !!after &&
            after.taskArn !== before!.taskArn &&
            after.lastStatus === "RUNNING"
          )
        },
        {
          timeout: 15 * 60_000,
          intervals: [10_000],
          message:
            `ECS never placed a running replacement on ${instanceId} after ` +
            `${before!.taskArn} was stopped (target health seen: ` +
            `${health.join(", ")})`,
        },
      )
      .toBe(true)
    console.log(
      `[15-premium-aws] PREM-12 premium-${userId}-tg across the replacement: ` +
        health.join(", "),
    )

    // The replacement is only meaningful if the original really went away by
    // this test's hand, rather than the poll having watched an unrelated
    // redeployment roll past.
    expect(
      awsJson<{ tasks: { lastStatus: string; stoppedReason?: string }[] }>(
        `ecs describe-tasks --cluster ${CLUSTER} --tasks ${before!.taskArn}`,
      ).tasks[0],
      "the task this test stopped really stopped, carrying its own reason",
    ).toMatchObject({ lastStatus: "STOPPED", stoppedReason: "e2e-row-6216" })

    // And the user is served again at the end, without having re-assigned.
    await expect
      .poll(() => premiumTargetHealth(userId).includes("healthy"), {
        timeout: 5 * 60_000,
        intervals: [15_000],
        message: `premium-${userId}-tg never came back healthy after the replacement`,
      })
      .toBe(true)
  } finally {
    await api
      .delete("/users/me/premium/assign", {
        headers,
        timeout: RELEASE_REQUEST_TIMEOUT_MS,
      })
      .catch(() => {})
    await api.dispose()
  }
})

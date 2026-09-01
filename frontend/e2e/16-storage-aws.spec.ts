import { execSync } from "child_process"
import * as fs from "fs"
import * as os from "os"
import * as path from "path"

import {
  test,
  expect,
  APIRequestContext,
  Browser,
  Page,
} from "@playwright/test"

import {
  AWS_REGION,
  FREE_USER,
  PREMIUM_USER,
  PUBLIC_LOG_GROUP,
  RUN_TEST_TIMEOUT_MS,
  apiHeaders,
  apiLogin,
  apiUrl,
  awaitRunFinished,
  awsJson,
  cloudwatchHas,
  filterWorkspace,
  freeStorageState,
  gotoDashboard,
  importSampleData,
  isLocalBaseUrl,
  logTail,
  login,
  openWorkspace,
  premiumTargetHealth,
  reproduceTutorial,
  runShellOverSsm,
  runSql,
  runTutorial,
  s3ObjectCount,
  skipWithoutCreds,
  sqlLiteral,
  startRun,
  windowStart,
} from "./helpers"

// Real-S3 truth for the storage rows (sheets 04 / 10 / System 607). The API
// answers 200 even when its S3 write or delete failed (upload_input_data and
// the workspace-delete cleanup both swallow errors to a logged return), so
// only an S3-side read proves the object really landed or really went away.
// Opt in explicitly - the lane reads and writes the real per-user buckets on
// the deployed dev environment:
//
//   RUN_SLOW=1 RUN_S3_AWS=1 npx playwright test e2e/16-storage-aws.spec.ts --retries 0
//
// A row whose S3 truth is the same for either account is registered once per
// tier from one body - the sheets ask it of both, and the variants differ
// only in the Tier they are handed. Each variant spells out its own case ID,
// free S3-0x and premium S3-2x: the skip-summary reporter keys on the leading
// `S3-\d+` of the title, so one shared ID with a `[premium]` suffix would let
// a run that forgot RUN_PREMIUM_AWS tick the premium row off the free pass.
//
// The premium half spends real premium capacity, so it rides RUN_PREMIUM_AWS
// on top, exactly as `15-premium-aws` does:
//
//   RUN_SLOW=1 RUN_S3_AWS=1 RUN_PREMIUM_AWS=1 npx playwright test \
//     e2e/16-storage-aws.spec.ts --retries 0
//
// A sign-off run should add E2E_FAIL_ON_SKIP=1, so an unrun premium variant
// fails the run rather than leaving a silent gap.
//
// Rows whose subject is free-tier-specific stay free-only; each says why
// where it is defined.

const RUN_S3_AWS = process.env.RUN_S3_AWS === "1"
const RUN_PREMIUM_AWS = process.env.RUN_PREMIUM_AWS === "1"

const IMAGE_FIXTURE = path.join(
  __dirname,
  "..",
  "..",
  "sample_data",
  "dev_mouse2p_short_image.tiff",
)
const HDF5_FIXTURE = path.join(
  __dirname,
  "..",
  "..",
  "sample_data",
  "tutorial",
  "input",
  "sample_hdf5.h5",
)

const REQUEST_TIMEOUT_MS = 30_000
const UPLOAD_TIMEOUT_MS = 120_000

// A cold premium assign starts EC2 capacity and an ECS task: minutes, not
// seconds. The assignment row then becomes visible before the instance serves
// traffic, so waiting for it to serve gets its own budget on top.
const PREMIUM_ASSIGN_TIMEOUT_MS = 15 * 60_000
const PREMIUM_SERVING_TIMEOUT_MS = 5 * 60_000
const PREMIUM_RELEASE_TIMEOUT_MS = 120_000
// Reported by /premium/assign while capacity warms, and deliberately absent
// from /premium/status - so the caller keeps assigning until a real instance
// frees up rather than settling on a tier status never reports.
const AUTOSCALING_POOL = "autoscaling-pool"

// Everything the storage rows need to know about which account they run as.
// The bodies below read only these fields, which is what keeps one test
// serving both sheets' variants of the same row.
type Tier = {
  key: "free" | "premium"
  user: { email: string; password: string }
  credsName: string
  // Both halves of the opt-in, as the skip message should spell them
  optedIn: boolean
  optInHint: string
  // Undefined means "log in during the test": the premium account has no
  // saved state, because its assignment is established by the login itself.
  storageState: () => string | undefined
  // active_workflow_count lives in a different table per tier
  assignmentTable: string
  signIn: (page: Page, rows: string) => Promise<void>
  // What signIn may spend before the test's own work starts
  setupBudgetMs: number
}

const FREE_TIER: Tier = {
  key: "free",
  user: FREE_USER,
  credsName: "TEST_USER_EMAIL/TEST_USER_PASSWORD",
  optedIn: RUN_S3_AWS,
  optInHint:
    "set RUN_S3_AWS=1 - reads and writes real S3 through the deployed env",
  storageState: freeStorageState,
  assignmentTable: "free_user_assignments",
  signIn: async (page) => {
    await gotoDashboard(page)
  },
  setupBudgetMs: 0,
}

const PREMIUM_TIER: Tier = {
  key: "premium",
  user: PREMIUM_USER,
  credsName: "TEST_PREMIUM_EMAIL/TEST_PREMIUM_PASSWORD",
  optedIn: RUN_S3_AWS && RUN_PREMIUM_AWS,
  optInHint:
    "set RUN_S3_AWS=1 RUN_PREMIUM_AWS=1 - real S3 plus a real premium " +
    "assignment on the shared dev pool",
  storageState: () => undefined,
  assignmentTable: "premium_user_assignments",
  signIn: signInPremium,
  setupBudgetMs: PREMIUM_ASSIGN_TIMEOUT_MS + PREMIUM_SERVING_TIMEOUT_MS,
}

// Free before premium, so each row's free variant runs first and a premium
// capacity skip can never stand in for an unrun free regression.
const TIERS: Tier[] = [FREE_TIER, PREMIUM_TIER]

// Set the moment the lane really holds premium capacity, so the release in
// afterEach spends a Firebase login (rate-limited) only when there is
// something to give back.
let premiumHeld = false

type PremiumStatus = {
  is_premium: boolean
  assignment: { instance_id?: string; is_shared?: boolean } | null
}

async function premiumStatus(page: Page): Promise<PremiumStatus> {
  const res = await page.request.get(`${apiUrl()}/users/me/premium/status`, {
    headers: await apiHeaders(page),
    timeout: REQUEST_TIMEOUT_MS,
  })
  expect(res.ok(), await res.text()).toBe(true)
  const body = await res.json()
  return { is_premium: !!body.is_premium, assignment: body.assignment ?? null }
}

async function userIdOf(page: Page): Promise<number> {
  const res = await page.request.get(`${apiUrl()}/users/me`, {
    headers: await apiHeaders(page),
    timeout: REQUEST_TIMEOUT_MS,
  })
  expect(res.ok(), await res.text()).toBe(true)
  return (await res.json()).id
}

// A real premium login, held until the account genuinely owns capacity that
// really serves: the assignment row is where active_workflow_count lives, and
// the app drives its workflow run through the ALB.
async function signInPremium(page: Page, rows: string) {
  await login(page, PREMIUM_USER.email, PREMIUM_USER.password)

  // The account trap this suite has been bitten by before: an account can say
  // "Premium" on /users/me while /premium/status says free (billing grace).
  const status = await premiumStatus(page)
  expect(
    status.is_premium,
    `${PREMIUM_USER.email} is not premium on /premium/status - verify the ` +
      `TEST_PREMIUM_* account against /premium/status, not /users/me`,
  ).toBe(true)
  // Owed from the login on: the app's provider assigns on mount and the probe
  // below assigns too, so capacity can be held before /premium/status ever
  // reports a row.
  premiumHeld = true

  // Poll the provider's own result rather than racing it with a second assign.
  let assignment = status.assignment
  const assignDeadline = Date.now() + PREMIUM_ASSIGN_TIMEOUT_MS
  while (!assignment) {
    if (Date.now() > assignDeadline) {
      // One direct probe tells "still scaling with no capacity" (skip) apart
      // from a dead assign flow (fail).
      const probe = await page.request.post(
        `${apiUrl()}/users/me/premium/assign`,
        { headers: await apiHeaders(page), timeout: PREMIUM_ASSIGN_TIMEOUT_MS },
      )
      const body = await probe.json().catch(() => ({}))
      test.skip(
        !!body?.scaling_in_progress || body?.instance_id === AUTOSCALING_POOL,
        `rows ${rows} [premium]: the dev pool could not place premium ` +
          `capacity within ${PREMIUM_ASSIGN_TIMEOUT_MS / 60_000} min ` +
          `(${JSON.stringify(body)}) - rerun when the cluster has free CPU`,
      )
      throw new Error(
        `no premium assignment appeared and the backend is not scaling: ` +
          `${probe.status()} ${JSON.stringify(body)}`,
      )
    }
    await new Promise((r) => setTimeout(r, 15_000))
    assignment = (await premiumStatus(page)).assignment
  }
  // The granted tier decides whether the gate below can read anything, and is
  // the first thing to know when a premium row fails on a 502.
  console.log(
    `[16-storage-aws] premium assignment: instance=` +
      `${assignment.instance_id ?? "none"}, shared=${assignment.is_shared}`,
  )
  // A dedicated assignment goes live - row, ALB rule, target group - before its
  // ECS task serves, so work driven through the ALB too early answers 502;
  // every ALB-driving row in `15-premium-aws` waits this out too. A cluster
  // that never gets a target serving leaves the row unverified, not failed. A
  // shared grant has no per-user target group and is already serving, so it
  // passes unchecked.
  if (assignment.is_shared || !assignment.instance_id?.startsWith("i-")) return
  const userId = await userIdOf(page)
  let states: string[] = []
  const servingDeadline = Date.now() + PREMIUM_SERVING_TIMEOUT_MS
  for (;;) {
    states = premiumTargetHealth(userId)
    if (states.includes("healthy")) return
    if (Date.now() > servingDeadline) break
    await new Promise((r) => setTimeout(r, 15_000))
  }
  test.skip(
    true,
    `rows ${rows} [premium]: premium-${userId}-tg never reported a healthy ` +
      `target (states: ${states.join(",") || "none"}) - the dev cluster could ` +
      `not keep a premium task serving; rerun when it has free CPU`,
  )
}

// Never leave the shared premium account holding an instance: a stuck
// assignment degrades the dev environment for everyone and keeps billing for
// the capacity. Asserted rather than best-effort, per the lane's own rule.
test.afterEach(async () => {
  if (!premiumHeld) return
  premiumHeld = false
  const { api, headers } = await apiLogin(
    PREMIUM_USER.email,
    PREMIUM_USER.password,
  )
  try {
    const res = await api.delete("/users/me/premium/assign", {
      headers,
      timeout: PREMIUM_RELEASE_TIMEOUT_MS,
    })
    expect(res.ok(), await res.text()).toBe(true)
  } finally {
    await api.dispose()
  }
})

// Sign in as the tier's account and read back the two facts every row below
// needs of it: the id the assignment tables are keyed on, and the per-user
// bucket. A null bucket attribute must fail loudly - the backend silently
// falls back to the default bucket, which would make every S3 assert vacuous.
async function enterAs(
  page: Page,
  tier: Tier,
  rows: string,
): Promise<{
  userId: number
  bucket: string
  headers: Record<string, string>
}> {
  await tier.signIn(page, rows)
  // Unrouted on both tiers, deliberately, as PREM-07's own workspace delete is:
  // nothing this lane asks of the API needs the premium instance (the DB, an S3
  // repair, a DB-plus-S3 delete), while routing it there would expose every
  // call to the 502 a premium task answers whenever it is not serving. Only the
  // workflow run has to land on the instance, and the app routes that itself.
  const headers = await apiHeaders(page)
  const res = await page.request.get(`${apiUrl()}/users/me`, {
    headers,
    timeout: REQUEST_TIMEOUT_MS,
  })
  expect(res.ok(), await res.text()).toBe(true)
  const body = await res.json()
  const bucket: string | undefined = body.attributes?.remote_bucket_name
  expect(
    bucket,
    `the ${tier.key} user has no remote_bucket_name attribute`,
  ).toBeTruthy()
  return { userId: body.id, bucket: bucket!, headers }
}

function skipUnlessOptedIn(rows: string, tier: Tier = FREE_TIER) {
  skipWithoutCreds(tier.user, tier.credsName)
  test.skip(!tier.optedIn, `rows ${rows} [${tier.key}]: ${tier.optInHint}`)
  test.skip(
    isLocalBaseUrl(),
    `rows ${rows} [${tier.key}]: needs the deployed dev environment (remote storage is S3 there, the local stack runs none); BASE_URL is local`,
  )
  // The rows here delete workspaces and their real bucket prefixes, and the
  // premium variants assign real capacity: never point this lane anywhere but
  // the development environment.
  expect(
    process.env.BASE_URL || "",
    "this lane only runs against the development environment",
  ).toContain("development-optinist")
  // A pass on retry hides real-AWS flakiness from the sign-off sheet
  expect(test.info().project.retries, "run this lane with --retries 0").toBe(0)
}

function bucketExists(bucket: string): boolean {
  try {
    execSync(
      `aws s3api head-bucket --bucket ${bucket} --region ${AWS_REGION}`,
      {
        timeout: 30_000,
        stdio: ["pipe", "pipe", "pipe"],
      },
    )
    return true
  } catch {
    return false
  }
}

function objectExists(bucket: string, key: string): boolean {
  try {
    execSync(
      `aws s3api head-object --bucket ${bucket} --key ${key} ` +
        `--region ${AWS_REGION}`,
      { timeout: 30_000, stdio: ["pipe", "pipe", "pipe"] },
    )
    return true
  } catch {
    return false
  }
}

async function apiEnsureWorkspaceId(
  api: APIRequestContext,
  headers: Record<string, string>,
  name: string,
): Promise<number> {
  const list = await api.get("/workspaces?offset=0&limit=100", {
    headers,
    timeout: REQUEST_TIMEOUT_MS,
  })
  expect(list.ok(), await list.text()).toBe(true)
  const { items } = await list.json()
  const found = items.find((w: { name: string }) => w.name === name)
  if (found) return found.id
  const created = await api.post("/workspace", {
    headers,
    data: { name },
    timeout: REQUEST_TIMEOUT_MS,
  })
  expect(created.ok(), await created.text()).toBe(true)
  return (await created.json()).id
}

type TreeNode = {
  path: string
  name: string
  isdir: boolean
  nodes: TreeNode[]
  sync_status?: string
}

function findNode(nodes: TreeNode[], name: string): TreeNode | undefined {
  for (const node of nodes) {
    if (node.name === name) return node
    const hit = findNode(node.nodes ?? [], name)
    if (hit) return hit
  }
  return undefined
}

// The bucket, the upload and the sync round-trip are the account's own, so
// this row needs no premium assignment - only the premium account.
for (const tier of TIERS) {
  const id = { free: "S3-01", premium: "S3-21" }[tier.key]
  test(`${id} - The per-user bucket is real and an upload really lands its object @slow`, async () => {
    const rows = "403 / 528 / BT-1002 / BT-1003 / BT-1111"
    skipUnlessOptedIn(rows, tier)
    test.setTimeout(5 * 60_000)

    const { api, headers } = await apiLogin(tier.user.email, tier.user.password)
    try {
      const me = await api.get("/users/me", {
        headers,
        timeout: REQUEST_TIMEOUT_MS,
      })
      expect(me.ok(), await me.text()).toBe(true)
      const meBody = await me.json()
      const userId: number = meBody.id
      const bucket: string | undefined = meBody.attributes?.remote_bucket_name
      // A null attribute must fail loudly - the backend silently falls back to
      // the default bucket, which would make every assert below vacuous
      expect(
        bucket,
        `${tier.user.email} has no remote_bucket_name attribute`,
      ).toBeTruthy()
      // The sheets' naming contract: {env}-optinist-user-{id}-{unique}
      expect(bucket).toMatch(
        new RegExp(`^development-optinist-user-${userId}-[0-9a-f]{10}$`),
      )
      expect(
        bucketExists(bucket!),
        `bucket ${bucket} not reachable via head-bucket`,
      ).toBe(true)

      const wsId = await apiEnsureWorkspaceId(api, headers, "e2e-s3")
      const uniqueName = `e2e_upload_${Date.now()}.tiff`
      try {
        const uploaded = await api.post(`/files/${wsId}/upload/${uniqueName}`, {
          headers,
          multipart: {
            file: {
              name: uniqueName,
              mimeType: "image/tiff",
              buffer: fs.readFileSync(IMAGE_FIXTURE),
            },
          },
          timeout: UPLOAD_TIMEOUT_MS,
        })
        expect(uploaded.ok(), await uploaded.text()).toBe(true)
        expect((await uploaded.json()).file_path).toBe(uniqueName)

        // The S3-side read is the test: the 200 above is answered even when
        // the inline S3 PUT failed
        const key = `app/studio_data/input/${wsId}/${uniqueName}`
        await expect
          .poll(() => objectExists(bucket!, key), {
            timeout: 30_000,
            message: `s3://${bucket}/${key} missing after a 200 upload`,
          })
          .toBe(true)

        // Row 528's automatable slice: the merged listing labels the file
        // synced (local AND in S3) and the on-demand sync endpoint round-trips
        // it. The genuinely-remote branch (S3 copy with no local file) has no
        // API to set up - it stays with the pytest coverage.
        // file_type is required: without it get_files returns [] and every
        // file would come back remote-labeled from the S3 side alone.
        const merged = await api.get(`/files/${wsId}/merged?file_type=image`, {
          headers,
          timeout: REQUEST_TIMEOUT_MS,
        })
        expect(merged.ok(), await merged.text()).toBe(true)
        const node = findNode(await merged.json(), uniqueName)
        expect(
          node,
          `${uniqueName} absent from the merged listing`,
        ).toBeTruthy()
        expect(node!.sync_status).toBe("synced")

        const synced = await api.post(`/files/${wsId}/sync/${uniqueName}`, {
          headers,
          timeout: UPLOAD_TIMEOUT_MS,
        })
        expect(synced.ok(), await synced.text()).toBe(true)
        expect((await synced.json()).file_path).toBe(uniqueName)

        // BT-1006's S3 half: an HDF5 upload lands its object the same way
        const h5Name = `e2e_upload_${Date.now()}.h5`
        const h5 = await api.post(`/files/${wsId}/upload/${h5Name}`, {
          headers,
          multipart: {
            file: {
              name: h5Name,
              mimeType: "application/x-hdf",
              buffer: fs.readFileSync(HDF5_FIXTURE),
            },
          },
          timeout: UPLOAD_TIMEOUT_MS,
        })
        expect(h5.ok(), await h5.text()).toBe(true)
        const h5Key = `app/studio_data/input/${wsId}/${h5Name}`
        await expect
          .poll(() => objectExists(bucket!, h5Key), {
            timeout: 30_000,
            message: `s3://${bucket}/${h5Key} missing after a 200 upload`,
          })
          .toBe(true)
      } finally {
        const res = await api.delete(`/workspace/${wsId}`, {
          headers,
          timeout: UPLOAD_TIMEOUT_MS,
        })
        expect(res.ok(), await res.text()).toBe(true)
      }
    } finally {
      await api.dispose()
    }
  })
}

for (const tier of TIERS) {
  const id = { free: "S3-02", premium: "S3-22" }[tier.key]
  test.describe(`Import and delete round-trip the real bucket (${tier.key})`, () => {
    test.use({ storageState: tier.storageState() })

    test(`${id} - Sample import lands input objects; workspace delete empties the prefixes @slow`, async ({
      page,
    }) => {
      const rows = "406 / BT-1003 / BT-1111"
      skipUnlessOptedIn(rows, tier)
      test.setTimeout(10 * 60_000 + tier.setupBudgetMs)

      const { bucket, headers } = await enterAs(page, tier, rows)
      const wsId = await openWorkspace(page, "e2e-s3import")
      const inputPrefix = `app/studio_data/input/${wsId}/`
      const outputPrefix = `app/studio_data/output/${wsId}/`

      let deleted = false
      const deleteWorkspace = () =>
        page.request.delete(`${apiUrl()}/workspace/${wsId}`, {
          headers,
          timeout: UPLOAD_TIMEOUT_MS,
        })
      try {
        await importSampleData(page, "e2e-s3import")
        await expect
          .poll(() => s3ObjectCount(bucket, inputPrefix), {
            timeout: 120_000,
            intervals: [10_000],
            message: `no imported input objects under s3://${bucket}/${inputPrefix}`,
          })
          .toBeGreaterThan(0)

        // DELETE /workspace answers 200 even when its S3 cleanup threw (the
        // server swallows the error and soft-deletes anyway), so the empty
        // prefix is the assertion, not the status code. s3ObjectCount throws on
        // a failed CLI call rather than reporting a vacuous empty result.
        const res = await deleteWorkspace()
        deleted = true
        expect(res.ok(), await res.text()).toBe(true)
        await expect
          .poll(() => s3ObjectCount(bucket, inputPrefix), {
            timeout: 60_000,
            intervals: [10_000],
            message: `input objects survived the workspace delete under s3://${bucket}/${inputPrefix}`,
          })
          .toBe(0)
        expect(
          s3ObjectCount(bucket, outputPrefix),
          `output objects survived the workspace delete under s3://${bucket}/${outputPrefix}`,
        ).toBe(0)
      } finally {
        // A failure above must not strand the workspace and its real objects
        if (!deleted) await deleteWorkspace().catch(() => {})
      }
    })
  })
}

for (const tier of TIERS) {
  const id = { free: "S3-03", premium: "S3-23" }[tier.key]
  test.describe(`Published experiment via the public instance (${tier.key})`, () => {
    test.use({ storageState: tier.storageState() })

    test(`${id} - An anonymous public read reproduces the published experiment with lazy S3 @slow`, async ({
      page,
      request,
    }) => {
      const rows = "607"
      skipUnlessOptedIn(rows, tier)
      test.setTimeout(RUN_TEST_TIMEOUT_MS + 20 * 60_000 + tier.setupBudgetMs)

      const {
        userId,
        bucket: bucketName,
        headers,
      } = await enterAs(page, tier, rows)
      const wsName = "e2e-s3pub"
      const wsId = await openWorkspace(page, wsName)
      let recordId = 0
      try {
        await importSampleData(page, wsName)

        // Rows 538 (free) / 543 (premium), live half: the run really holds a
        // slot in its own tier's assignment table while it executes and
        // releases it on completion; the failure-path decrement stays with
        // the unit suite
        const countSql =
          `SELECT active_workflow_count FROM ${tier.assignmentTable} ` +
          `WHERE user_id = ${userId};`
        expect(runSql(countSql), "rows 538 / 543: pre-run baseline").toBe("0")
        await reproduceTutorial(page, "Tutorial1")
        const { workspaceId: runWs, uid } = await startRun(page, "RUN ALL")
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

        // Rows 407 / BT-1004: the run's outputs really landed in the user's
        // own bucket - the direct S3 read, before any publish
        await expect
          .poll(
            () =>
              s3ObjectCount(
                bucketName,
                `app/studio_data/output/${wsId}/${uid}/`,
              ),
            {
              timeout: 120_000,
              intervals: [10_000],
              message: `no run outputs under s3://${bucketName}/app/studio_data/output/${wsId}/${uid}/`,
            },
          )
          .toBeGreaterThan(0)

        // Row 1217: not merely "some objects" - the run's own NWB output is
        // there, and nothing landed as a zero-byte stub
        const outputs = JSON.parse(
          execSync(
            `aws s3api list-objects-v2 --bucket ${bucketName} ` +
              `--prefix app/studio_data/output/${wsId}/${uid}/ ` +
              "--query 'Contents[].{k:Key,s:Size}' " +
              `--region ${AWS_REGION} --output json`,
            { timeout: 30_000 },
          ).toString() || "[]",
        ) as { k: string; s: number }[]
        expect(
          outputs.some((o) => o.k.endsWith(".nwb")),
          `no NWB among the run's S3 outputs: ${outputs.map((o) => o.k).join(", ")}`,
        ).toBe(true)
        // error.log is legitimately empty on a clean run
        for (const o of outputs.filter((out) => !out.k.endsWith(".log"))) {
          expect(o.s, `${o.k} landed as a zero-byte object`).toBeGreaterThan(0)
        }

        // Find the record BEFORE the negative, so the 404 below can only mean
        // the published_only gate, never a record that does not exist yet.
        // Polled, not read once: the executor writes the experiment record
        // asynchronously after the last node finishes, so it can land after
        // the run reports success and the outputs are already in S3.
        await expect
          .poll(
            async () => {
              const listRes = await page.request.get(
                `${apiUrl()}/api/dataview?limit=100&offset=0&workspace_id=${wsId}`,
                { headers, timeout: REQUEST_TIMEOUT_MS },
              )
              if (!listRes.ok()) return `HTTP ${listRes.status()}`
              const { items } = await listRes.json()
              const record = (items as { id: number; uid?: string }[]).find(
                (r) => r.uid === uid,
              )
              if (!record) return "absent"
              recordId = record.id
              return "found"
            },
            {
              timeout: 120_000,
              intervals: [10_000],
              message: `no dataview record for run ${uid}`,
            },
          )
          .toBe("found")

        // The published_only gate: anonymous reproduce of the existing but
        // not-yet-published experiment is a 404
        const before = await request.get(
          `${apiUrl()}/api/public/dataview/workflow/reproduce/${wsId}/${uid}`,
          { timeout: REQUEST_TIMEOUT_MS },
        )
        expect(before.status(), await before.text()).toBe(404)

        const t0 = windowStart()
        const published = await page.request.post(
          `${apiUrl()}/api/dataview/publish/${recordId}/on`,
          { headers, timeout: UPLOAD_TIMEOUT_MS },
        )
        expect(published.ok(), await published.text()).toBe(true)

        // Anonymous listing shows it only because publish_status flipped on
        await expect
          .poll(
            async () => {
              const res = await request.get(
                `${apiUrl()}/api/public/dataview?limit=100&offset=0`,
                { timeout: REQUEST_TIMEOUT_MS },
              )
              if (!res.ok()) return `HTTP ${res.status()}`
              const body = await res.json()
              const records = (
                Array.isArray(body) ? body : (body.items ?? [])
              ) as { uid?: string }[]
              return records.some((r) => r.uid === uid) ? "listed" : "absent"
            },
            {
              timeout: 120_000,
              intervals: [10_000],
              message: `published run ${uid} never appeared in /api/public/dataview`,
            },
          )
          .toBe("listed")

        // The sheet's core: reproduce answers 202 pending_sync until the
        // publish sync lands, then 200 - S3 is the source of truth and the
        // public instance lazily fetches from the publisher's bucket. A 503 is
        // tolerated only as a transient download retry, never as the outcome.
        let lastStatus = 0
        await expect
          .poll(
            async () => {
              const res = await request.get(
                `${apiUrl()}/api/public/dataview/workflow/reproduce/${wsId}/${uid}`,
                { timeout: UPLOAD_TIMEOUT_MS },
              )
              lastStatus = res.status()
              expect(
                [200, 202, 503],
                `reproduce answered ${lastStatus}: ${await res.text()}`,
              ).toContain(lastStatus)
              return lastStatus
            },
            {
              timeout: 10 * 60_000,
              intervals: [20_000],
              message: `reproduce never reached 200 (last status ${lastStatus})`,
            },
          )
          .toBe(200)

        // Lazy-fetch evidence is conditional by design: a pre-warmed public
        // cache leaves no download line, which the sheet calls moot
        const downloaded = cloudwatchHas(
          PUBLIC_LOG_GROUP,
          `Download data from S3 [${bucketName}]`,
          t0,
        )
        console.log(
          `[16-storage-aws] ${id} lazy-fetch line in ${PUBLIC_LOG_GROUP}: ` +
            (downloaded
              ? "found"
              : "not found (pre-warmed cache - moot per the sheet)"),
        )
      } finally {
        if (recordId) {
          await page.request
            .post(`${apiUrl()}/api/dataview/publish/${recordId}/off`, {
              headers,
              timeout: UPLOAD_TIMEOUT_MS,
            })
            .catch(() => {})
        }
        await page.request
          .delete(`${apiUrl()}/workspace/${wsId}`, {
            headers,
            timeout: UPLOAD_TIMEOUT_MS,
          })
          .catch(() => {})
      }
    })
  })
}

test.describe("Published sync error and recovery on real S3", () => {
  test.use({ storageState: freeStorageState() })

  // Rows 717 / 718 / BT-719 / 2031's live half. The public cache warms lazily
  // on the first reproduce (S3-03), so removing experiment.yaml from the
  // owner's bucket between publish and first view is a REAL missing-data
  // state: the visitor's first open must surface the error state with Retry,
  // and Retry must recover once the file is back. Only the test account's own
  // object is touched, and it is put back in a finally.
  //
  // Free-only, unlike the per-tier rows above: what this row asks about is
  // the anonymous visitor's dialog, and the object it reads is the same
  // per-user S3 copy whichever tier published it - a premium variant would
  // spend a real assignment to observe identical public-instance behaviour.
  test("S3-04 - A missing S3 config surfaces the public error state; Retry recovers it @slow", async ({
    page,
    browser,
  }) => {
    skipUnlessOptedIn("717 / 718 / BT-719 / 2031")
    test.setTimeout(RUN_TEST_TIMEOUT_MS + 20 * 60_000)

    await gotoDashboard(page)
    const wsName = "e2e-s3err"
    const wsId = await openWorkspace(page, wsName)
    const headers = await apiHeaders(page)
    const aside = path.join(os.tmpdir(), `e2e-s3err-${Date.now()}.yaml`)
    let recordId = 0
    let yamlMovedAside = false
    let bucket = ""
    let key = ""
    const restoreYaml = () => {
      execSync(
        `aws s3 cp ${aside} s3://${bucket}/${key} --region ${AWS_REGION}`,
        { timeout: 60_000, stdio: ["pipe", "pipe", "pipe"] },
      )
      yamlMovedAside = false
    }
    try {
      await importSampleData(page, wsName)
      const { uid } = await runTutorial(page, "Tutorial1", "RUN ALL")
      const me = await page.request.get(`${apiUrl()}/users/me`, {
        headers,
        timeout: REQUEST_TIMEOUT_MS,
      })
      bucket = (await me.json()).attributes?.remote_bucket_name
      expect(
        bucket,
        "free user has no remote_bucket_name attribute",
      ).toBeTruthy()
      key = `app/studio_data/output/${wsId}/${uid}/experiment.yaml`

      await expect
        .poll(
          async () => {
            const listRes = await page.request.get(
              `${apiUrl()}/api/dataview?limit=100&offset=0&workspace_id=${wsId}`,
              { headers, timeout: REQUEST_TIMEOUT_MS },
            )
            if (!listRes.ok()) return `HTTP ${listRes.status()}`
            const { items } = await listRes.json()
            const record = (items as { id: number; uid?: string }[]).find(
              (r) => r.uid === uid,
            )
            if (!record) return "absent"
            recordId = record.id
            return "found"
          },
          {
            timeout: 120_000,
            intervals: [10_000],
            message: `no dataview record for run ${uid}`,
          },
        )
        .toBe("found")

      const published = await page.request.post(
        `${apiUrl()}/api/dataview/publish/${recordId}/on`,
        { headers, timeout: UPLOAD_TIMEOUT_MS },
      )
      expect(published.ok(), await published.text()).toBe(true)

      // Take the config away before anything warms the public cache
      execSync(
        `aws s3 cp s3://${bucket}/${key} ${aside} --region ${AWS_REGION}`,
        { timeout: 60_000, stdio: ["pipe", "pipe", "pipe"] },
      )
      execSync(`aws s3 rm s3://${bucket}/${key} --region ${AWS_REGION}`, {
        timeout: 60_000,
        stdio: ["pipe", "pipe", "pipe"],
      })
      yamlMovedAside = true

      // Really anonymous, like the visitor rows 717/718 describe
      const ctx = await browser.newContext({
        baseURL: process.env.BASE_URL,
        storageState: undefined,
      })
      const viewer = await ctx.newPage()
      try {
        // The anonymous listing is eventually consistent with the publish;
        // open the UI only once the record is really listed (same poll as
        // S3-03, and the listing reads the DB, not the deleted yaml)
        await expect
          .poll(
            async () => {
              const res = await viewer.request.get(
                `${apiUrl()}/api/public/dataview?limit=100&offset=0`,
                { timeout: REQUEST_TIMEOUT_MS },
              )
              if (!res.ok()) return `HTTP ${res.status()}`
              const body = await res.json()
              const records = (
                Array.isArray(body) ? body : (body.items ?? [])
              ) as { uid?: string }[]
              return records.some((r) => r.uid === uid) ? "listed" : "absent"
            },
            {
              timeout: 120_000,
              intervals: [10_000],
              message: `published run ${uid} never appeared in /api/public/dataview`,
            },
          )
          .toBe("listed")
        await viewer.goto("/public")
        await filterWorkspace(viewer, wsName)
        // The run's own row, by the uid the grid's ID column renders - the
        // record's display name belongs to the run helper, not this test
        const row = viewer
          .locator(".MuiDataGrid-row")
          .filter({ has: viewer.getByText(uid.slice(0, 8)) })
          .first()
        await expect(row).toBeVisible({ timeout: 30_000 })
        await row
          .locator('[data-field="details"] [data-testid="InsightsIcon"]')
          .click()
        const dialog = viewer.locator('[role="dialog"]')
        await expect(dialog).toBeVisible({ timeout: 15_000 })

        // Row 717: the error state - icon, alert, and a Retry button. A 503
        // renders it at once; a 202 rides the auto-retry ladder out first
        // (30 x 10s), so the budget covers the ladder's ceiling.
        await expect(
          dialog.locator('[data-testid="ErrorOutlineIcon"]').first(),
        ).toBeVisible({ timeout: 420_000 })
        await expect(dialog.locator(".MuiAlert-root").first()).toBeVisible()
        const retry = dialog.getByRole("button", { name: "Retry" })
        await expect(retry).toBeVisible()

        // Put the file back; row 718 / BT-719: Retry re-syncs and recovers.
        // The interim pending state is not asserted - a fast re-sync renders
        // the details before the 202 branch ever paints.
        restoreYaml()
        await retry.click()

        // Recovery, by the route's own verdict rather than pixel state
        await expect
          .poll(
            async () =>
              (
                await viewer.request.get(
                  `${apiUrl()}/api/public/dataview/workflow/reproduce/${wsId}/${uid}`,
                  { timeout: UPLOAD_TIMEOUT_MS },
                )
              ).status(),
            {
              timeout: 10 * 60_000,
              intervals: [20_000],
              message: "reproduce never recovered to 200 after the retry",
            },
          )
          .toBe(200)
        // ...and the dialog left its error state. The single Retry above can
        // land before the re-sync completes, so keep clicking it until the
        // error state clears (the PREM-13 reload-in-poll pattern).
        const errorIcon = dialog
          .locator('[data-testid="ErrorOutlineIcon"]')
          .first()
        await expect
          .poll(
            async () => {
              if (await errorIcon.isVisible()) {
                await dialog
                  .getByRole("button", { name: "Retry" })
                  .click()
                  .catch(() => {})
                return "error state"
              }
              return "recovered"
            },
            {
              timeout: 120_000,
              intervals: [15_000],
              message: "the dialog never left its error state after Retry",
            },
          )
          .toBe("recovered")
      } finally {
        await ctx.close()
      }
    } finally {
      if (yamlMovedAside) {
        try {
          restoreYaml()
        } catch {
          // the workspace delete below removes the prefix anyway
        }
      }
      if (recordId) {
        await page.request
          .post(`${apiUrl()}/api/dataview/publish/${recordId}/off`, {
            headers,
            timeout: UPLOAD_TIMEOUT_MS,
          })
          .catch(() => {})
      }
      await page.request
        .delete(`${apiUrl()}/workspace/${wsId}`, {
          headers,
          timeout: UPLOAD_TIMEOUT_MS,
        })
        .catch(() => {})
      fs.rmSync(aside, { force: true })
    }
  })
})

// ---------------------------------------------------------------------------
// Rows 727 + 723: the publish-time S3 repair of a broken local config, then a
// five-record batch draining in a single background sync run. One test because
// both rows need the same expensive setup - a finished run plus four real
// copies of it - and the batch publish is the natural second act of the repair.
//
// Free-only, unlike the per-tier rows above: the repair is asserted on the
// filesystem of the task that serves the publish, and freeTierExec below
// reaches into the free tier's single ECS task by name. A premium variant
// would have to exec on whichever instance the assignment landed on, which is
// `15-premium-aws`'s machinery rather than this lane's.
// ---------------------------------------------------------------------------

const FREE_CLUSTER = "development-optinist-cloud-cluster"
const FREE_SERVICE = "development-optinist-cloud-service"
const FREE_CONTAINER_FILTER = "ecs-development-optinist-cloud-taskdef"
const BACKGROUND_LOG = "/ecs/development-background-optinist-cloud-taskdef"
const COPY_TIMEOUT_MS = 300_000

// The EC2 host running the free tier's single task (desired=1 on development,
// asserted below), so a file broken there is broken on the task serving the
// publish request.
function freeTierHostId(): string {
  const arns = awsJson<{ taskArns: string[] }>(
    `ecs list-tasks --cluster ${FREE_CLUSTER} --service-name ${FREE_SERVICE}`,
  ).taskArns
  expect(arns.length, "the free service has no running task").toBe(1)
  const ci = awsJson<{ tasks: { containerInstanceArn?: string }[] }>(
    `ecs describe-tasks --cluster ${FREE_CLUSTER} --tasks ${arns[0]}`,
  ).tasks[0].containerInstanceArn
  expect(ci, "the free task reports no container instance").toBeTruthy()
  return awsJson<{ containerInstances: { ec2InstanceId: string }[] }>(
    `ecs describe-container-instances --cluster ${FREE_CLUSTER} ` +
      `--container-instances ${ci}`,
  ).containerInstances[0].ec2InstanceId
}

let freeHost = ""
// Run one command inside the free tier's app container over SSM. cmd must not
// contain double quotes - it is interpolated into a double-quoted sh -c.
function freeTierExec(cmd: string): string {
  if (/"/.test(cmd)) throw new Error(`freeTierExec cmd has a quote: ${cmd}`)
  if (!freeHost) freeHost = freeTierHostId()
  return runShellOverSsm(
    freeHost,
    [
      "set -e",
      `CID=$(sudo docker ps -q --filter name=${FREE_CONTAINER_FILTER} | head -1)`,
      '[ -n "$CID" ]',
      `sudo docker exec "$CID" sh -c "${cmd}"`,
    ],
    "free-tier exec",
  )
}

test.describe("Publish repair and batch sync on the real free tier", () => {
  test.use({ storageState: freeStorageState() })

  test("S3-05 - Publish repairs broken local configs from S3, and five rapid publishes drain in one sync run @slow", async ({
    page,
  }) => {
    skipUnlessOptedIn("723 / 727")
    test.setTimeout(RUN_TEST_TIMEOUT_MS + 35 * 60_000)

    await gotoDashboard(page)
    const wsName = "e2e-s3batch"
    const wsId = await openWorkspace(page, wsName)
    const headers = await apiHeaders(page)
    const recordIds: number[] = []
    try {
      await importSampleData(page, wsName)
      const { uid } = await runTutorial(page, "Tutorial1", "RUN ALL")

      // The sync job's own precondition (success = 1) is written by an async
      // record write after the run reports finished; a copy taken before it
      // lands would clone success = 0 and the job would skip the whole batch.
      await expect
        .poll(
          () =>
            runSql(
              `SELECT success FROM experiment_records ` +
                `WHERE workspace_id = ${wsId} AND uid = '${sqlLiteral(uid)}';`,
            ),
          {
            timeout: 180_000,
            intervals: [10_000],
            message: `experiment_records.success never reached 1 for ${wsId}/${uid}`,
          },
        )
        .toBe("1")

      // Four real copies: copy_data re-uploads each new uid to S3 and the
      // record copy keeps success = 1, so five publishable experiments cost
      // one pipeline run.
      for (let i = 0; i < 4; i++) {
        const copied = await page.request.post(
          `${apiUrl()}/experiments/copy/${wsId}`,
          { headers, data: { uidList: [uid] }, timeout: COPY_TIMEOUT_MS },
        )
        expect(copied.ok(), await copied.text()).toBe(true)
      }

      const uids: string[] = []
      await expect
        .poll(
          async () => {
            const res = await page.request.get(
              `${apiUrl()}/api/dataview?limit=100&offset=0&workspace_id=${wsId}`,
              { headers, timeout: REQUEST_TIMEOUT_MS },
            )
            if (!res.ok()) return `HTTP ${res.status()}`
            const { items } = await res.json()
            recordIds.length = 0
            uids.length = 0
            for (const r of items as { id: number; uid: string }[]) {
              recordIds.push(r.id)
              uids.push(r.uid)
            }
            return uids.length
          },
          {
            timeout: 60_000,
            intervals: [5_000],
            message: "the workspace never listed the run plus its four copies",
          },
        )
        .toBe(5)
      expect(
        runSql(
          `SELECT COUNT(*) FROM experiment_records ` +
            `WHERE workspace_id = ${wsId} AND success = 1;`,
        ),
        "a copy landed without success = 1 - the sync job would skip it",
      ).toBe("5")

      const yamlPath = (u: string) =>
        `/app/studio_data/output/${wsId}/${u}/experiment.yaml`

      // Row 727, single half: an empty {} stub - the state a migrated
      // instance leaves - on the task that will serve the publish.
      freeTierExec(`echo {} > ${yamlPath(uid)}`)
      expect(
        freeTierExec(`cat ${yamlPath(uid)}`),
        "the stub write did not land on the serving task",
      ).toBe("{}")
      const singlePub = await page.request.post(
        `${apiUrl()}/api/dataview/publish/${recordIds[uids.indexOf(uid)]}/on`,
        { headers, timeout: COPY_TIMEOUT_MS },
      )
      // The publish succeeding IS the row: without the pre-sync repair,
      // PublishValidator reads the stub and answers 400 unpublishable.
      expect(singlePub.ok(), await singlePub.text()).toBe(true)
      const repaired = freeTierExec(`cat ${yamlPath(uid)}`)
      expect(
        repaired,
        "publish answered 200 but the local config is still the stub",
      ).not.toBe("{}")
      expect(repaired).toContain("success: success")
      expect(repaired).toContain(uid)

      // Row 727, bulk half: one stubbed and one deleted outright (the
      // "absent" case), repaired by the same bulk publish.
      const copies = uids.filter((u) => u !== uid)
      freeTierExec(`echo {} > ${yamlPath(copies[0])}`)
      freeTierExec(`rm ${yamlPath(copies[1])}`)

      // Row 723 needs the five pending rows to be observable before a sync
      // tick eats them, so wait for a fresh tick and publish right after it -
      // the batch then sits pending for most of a 5-minute window.
      const tickMarker = "Starting published experiment validation job"
      const alignStart = Date.now()
      await expect
        .poll(
          () =>
            logTail(BACKGROUND_LOG, 100).events.some(
              (e) =>
                e.ingestionTime > alignStart && e.message.includes(tickMarker),
            ),
          {
            timeout: 7 * 60_000,
            intervals: [15_000],
            message: `no "${tickMarker}" tick in ${BACKGROUND_LOG} within 7 minutes`,
          },
        )
        .toBe(true)

      // All five in one atomic flip (the already-published original is
      // re-pended by the same update), which is also what makes "one sync run
      // drains them" falsifiable: every tick after this sees all five.
      const bulk = await page.request.post(
        `${apiUrl()}/api/dataview/multiple/publish/on`,
        { headers, data: recordIds, timeout: COPY_TIMEOUT_MS },
      )
      expect(bulk.ok(), await bulk.text()).toBe(true)

      for (const u of [copies[0], copies[1]]) {
        const out = freeTierExec(`cat ${yamlPath(u)}`)
        expect(
          out,
          `bulk publish did not repair ${wsId}/${u} from S3`,
        ).toContain("success: success")
        expect(out).toContain(u)
      }

      // Row 723's own query: all five pending, then drained to zero.
      const pendingSql =
        `SELECT COUNT(*) FROM experiment_records ` +
        `WHERE workspace_id = ${wsId} AND local_sync_status = 'pending';`
      const syncedSql =
        `SELECT COUNT(*) FROM experiment_records ` +
        `WHERE workspace_id = ${wsId} AND publish_status = 1 ` +
        `AND local_sync_status = 'synced';`
      expect(
        runSql(pendingSql),
        "all five rows must be pending right after the bulk publish",
      ).toBe("5")

      // Two ticks plus margin: the job has been observed to log "No pending
      // experiments" on the first tick after a bulk publish and only see the
      // rows one tick later.
      await expect
        .poll(() => Number(runSql(syncedSql)), {
          timeout: 13 * 60_000,
          intervals: [15_000],
          message: "no row was validated within two sync ticks plus margin",
        })
        .toBeGreaterThan(0)
      // Single-run proof by timing: ticks are 5 minutes apart, so stragglers
      // waiting on a second run would stay pending far longer than this.
      await expect
        .poll(() => runSql(syncedSql), {
          timeout: 150_000,
          intervals: [10_000],
          message:
            "the batch did not drain in a single sync run - the leftovers " +
            "are waiting on a second 5-minute tick",
        })
        .toBe("5")
      expect(runSql(pendingSql), "pending fell to zero").toBe("0")

      // The job's own account of the batch. The background group's event
      // timestamps are broken (awslogs multiline pattern mismatch), so this
      // reads the newest stream's tail rather than a time-filtered query.
      const { text } = logTail(BACKGROUND_LOG, 500)
      for (const u of uids) {
        expect(
          text,
          `no "Successfully validated" line for ${wsId}/${u}`,
        ).toContain(`Successfully validated ${wsId}/${u}`)
      }
      const found = [...text.matchAll(/Found (\d+) experiments to validate/g)]
        .map((m) => Number(m[1]))
        .filter((n) => Number.isFinite(n))
      expect(
        Math.max(0, ...found),
        `no "Found N experiments to validate" line covering the batch`,
      ).toBeGreaterThanOrEqual(5)
      expect(text).toMatch(
        /Validation job completed: \d+ synced, \d+ errors \(max 10 concurrent\)/,
      )
    } finally {
      if (recordIds.length) {
        await page.request
          .post(`${apiUrl()}/api/dataview/multiple/publish/off`, {
            headers,
            data: recordIds,
            timeout: COPY_TIMEOUT_MS,
          })
          .catch(() => {})
      }
      await page.request
        .delete(`${apiUrl()}/workspace/${wsId}`, {
          headers,
          timeout: COPY_TIMEOUT_MS,
        })
        .catch(() => {})
    }
  })
})

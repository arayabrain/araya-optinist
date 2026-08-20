import { execSync } from "child_process"
import * as fs from "fs"
import * as path from "path"

import { test, expect, APIRequestContext } from "@playwright/test"

import {
  AWS_REGION,
  FREE_USER,
  PUBLIC_LOG_GROUP,
  RUN_TEST_TIMEOUT_MS,
  apiHeaders,
  apiLogin,
  apiUrl,
  cloudwatchHas,
  freeStorageState,
  gotoDashboard,
  importSampleData,
  isLocalBaseUrl,
  openWorkspace,
  runTutorial,
  s3ObjectCount,
  skipWithoutCreds,
  windowStart,
} from "./helpers"

// Real-S3 truth for the storage rows (sheets 04 / 10 / System 607). The API
// answers 200 even when its S3 write or delete failed (upload_input_data and
// the workspace-delete cleanup both swallow errors to a logged return), so
// only an S3-side read proves the object really landed or really went away.
// Opt in explicitly - the lane reads and writes the real per-user buckets on
// the deployed dev environment (no premium capacity involved):
//
//   RUN_SLOW=1 RUN_S3_AWS=1 npx playwright test e2e/16-storage-aws.spec.ts --retries 0

const RUN_S3_AWS = process.env.RUN_S3_AWS === "1"

const IMAGE_FIXTURE = path.join(
  __dirname,
  "..",
  "..",
  "sample_data",
  "dev_mouse2p_short_image.tiff",
)

const REQUEST_TIMEOUT_MS = 30_000
const UPLOAD_TIMEOUT_MS = 120_000

function skipUnlessOptedIn(rows: string) {
  skipWithoutCreds()
  test.skip(
    !RUN_S3_AWS,
    `rows ${rows}: set RUN_S3_AWS=1 - reads and writes real S3 through the deployed env`,
  )
  test.skip(
    isLocalBaseUrl(),
    `rows ${rows}: needs the deployed dev environment (remote storage is S3 there, the local stack runs none); BASE_URL is local`,
  )
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

test("S3-01 - The per-user bucket is real and an upload really lands its object @slow", async () => {
  const rows = "403 / 528 / BT-1002 / BT-1003 / BT-1111"
  skipUnlessOptedIn(rows)
  test.setTimeout(5 * 60_000)

  const { api, headers } = await apiLogin(FREE_USER.email, FREE_USER.password)
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
      `${FREE_USER.email} has no remote_bucket_name attribute`,
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
      expect(node, `${uniqueName} absent from the merged listing`).toBeTruthy()
      expect(node!.sync_status).toBe("synced")

      const synced = await api.post(`/files/${wsId}/sync/${uniqueName}`, {
        headers,
        timeout: UPLOAD_TIMEOUT_MS,
      })
      expect(synced.ok(), await synced.text()).toBe(true)
      expect((await synced.json()).file_path).toBe(uniqueName)
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

test.describe("Import and delete round-trip the real bucket", () => {
  test.use({ storageState: freeStorageState() })

  test("S3-02 - Sample import lands input objects; workspace delete empties the prefixes @slow", async ({
    page,
  }) => {
    const rows = "406 / BT-1003 / BT-1111"
    skipUnlessOptedIn(rows)
    test.setTimeout(10 * 60_000)

    await gotoDashboard(page)
    const wsId = await openWorkspace(page, "e2e-s3import")
    const headers = await apiHeaders(page)
    const me = await page.request.get(`${apiUrl()}/users/me`, {
      headers,
      timeout: REQUEST_TIMEOUT_MS,
    })
    const bucket = (await me.json()).attributes?.remote_bucket_name
    expect(bucket, "free user has no remote_bucket_name attribute").toBeTruthy()
    const inputPrefix = `app/studio_data/input/${wsId}/`
    const outputPrefix = `app/studio_data/output/${wsId}/`

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
    const res = await page.request.delete(`${apiUrl()}/workspace/${wsId}`, {
      headers,
      timeout: UPLOAD_TIMEOUT_MS,
    })
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
  })
})

test.describe("Published experiment via the public instance", () => {
  test.use({ storageState: freeStorageState() })

  test("S3-03 - An anonymous public read reproduces the published experiment with lazy S3 @slow", async ({
    page,
    request,
  }) => {
    const rows = "607"
    skipUnlessOptedIn(rows)
    test.setTimeout(RUN_TEST_TIMEOUT_MS + 20 * 60_000)

    await gotoDashboard(page)
    const wsName = "e2e-s3pub"
    const wsId = await openWorkspace(page, wsName)
    const headers = await apiHeaders(page)
    let recordId = 0
    try {
      await importSampleData(page, wsName)
      const { uid } = await runTutorial(page, "Tutorial1", "RUN ALL")

      // Rows 407 / BT-1004: the free run's outputs really landed in the
      // user's own bucket - the direct S3 read, before any publish
      const me = await page.request.get(`${apiUrl()}/users/me`, {
        headers,
        timeout: REQUEST_TIMEOUT_MS,
      })
      const bucketName = (await me.json()).attributes?.remote_bucket_name
      expect(
        bucketName,
        "free user has no remote_bucket_name attribute",
      ).toBeTruthy()
      await expect
        .poll(
          () =>
            s3ObjectCount(bucketName, `app/studio_data/output/${wsId}/${uid}/`),
          {
            timeout: 120_000,
            intervals: [10_000],
            message: `no run outputs under s3://${bucketName}/app/studio_data/output/${wsId}/${uid}/`,
          },
        )
        .toBeGreaterThan(0)

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
        `[16-storage-aws] S3-03 lazy-fetch line in ${PUBLIC_LOG_GROUP}: ` +
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

import { test, expect, request } from "@playwright/test"

import {
  AWS_REGION,
  CLOUDWATCH_POLL,
  PUBLIC_LOG_GROUP,
  awsJson,
  cloudwatchHas,
  disruptiveSkipReason,
} from "./helpers"

// Tests that have to break the environment to observe anything. Every one is
// tagged @disruptive, so it is filtered out unless RUN_DISRUPTIVE=1, and
// disruptiveSkipReason() additionally refuses to start when the database shows
// somebody else active in the last 30 minutes.
//
//   RUN_DISRUPTIVE=1 npx playwright test e2e/22-disruptive.spec.ts --retries 0
//
// Each restores what it disturbed, and asserts the restore: leaving the free
// tier scaled to zero is worse than a red test. The restore is in a finally
// block so a failed assertion mid-outage still puts the tier back.

const CLUSTER = "development-optinist-cloud-cluster"
const FREE_SERVICE = "development-optinist-cloud-service"
const PUBLIC_SERVICE = "development-public-optinist-cloud-service"

type Service = {
  desiredCount: number
  runningCount: number
  status: string
  deployments: { id: string; status: string; rolloutState?: string }[]
}

function describeService(service: string): Service {
  const out = awsJson<{ services: Service[] }>(
    `ecs describe-services --cluster ${CLUSTER} --services ${service} ` +
      `--region ${AWS_REGION}`,
  )
  expect(out.services.length, `ECS knows service ${service}`).toBe(1)
  return out.services[0]
}

function scaleService(service: string, desired: number): void {
  awsJson(
    `ecs update-service --cluster ${CLUSTER} --service ${service} ` +
      `--desired-count ${desired} --region ${AWS_REGION}`,
  )
}

async function pollService(
  service: string,
  want: (s: Service) => boolean,
  what: string,
): Promise<void> {
  await expect
    .poll(() => want(describeService(service)), {
      timeout: 300_000,
      intervals: [10_000],
      message: `${service} never reached: ${what}`,
    })
    .toBe(true)
}

test.describe("Disruptive: the free tier goes away @disruptive", () => {
  // Computed in a hook rather than at collection: the check reads the database,
  // and collection happens for every run whether this lane is included or not.
  test.beforeAll(() => {
    const reason = disruptiveSkipReason()
    test.skip(!!reason, reason)
    // This lane scales real services. Never point it at production.
    expect(
      process.env.BASE_URL || "",
      "this lane only runs against the development environment",
    ).toContain("development-optinist")
  })

  // Rows 809 / 810 / 812: the public tier serves the shell and /auth/login off
  // its own target group, so a free tier at zero tasks must not take the front
  // door down with it. Asserted by actually taking it down.
  test("OUT-01 - With the free service at zero, the public tier still serves", async () => {
    test.setTimeout(900_000)
    const before = describeService(FREE_SERVICE)
    expect(
      before.desiredCount,
      "the free service must be up before this test takes it down",
    ).toBeGreaterThan(0)

    const anon = await request.newContext()
    const start = Date.now()
    try {
      scaleService(FREE_SERVICE, 0)
      await pollService(FREE_SERVICE, (s) => s.runningCount === 0, "0 tasks")

      // Row 809: the shell, unauthenticated, through the same ALB
      const shell = await anon.get(`${process.env.BASE_URL}/`)
      expect(shell.status(), "the public shell during the free outage").toBe(
        200,
      )

      // Row 810: the login route, which is a public-tier rule rather than the
      // Bearer catch-all. A wrong 503 here is the rule ordering being wrong.
      // A syntactically valid address on purpose: UserAuth.email is an EmailStr,
      // so an .invalid address is refused 422 by Pydantic before the handler
      // runs, which proves only that some FastAPI app is mounted. This reaches
      // the auth path and comes back 400 INVALID_LOGIN_CREDENTIALS.
      const login = await anon.post(`${process.env.BASE_URL}/auth/login`, {
        data: { email: "nobody@example.com", password: "x" },
        failOnStatusCode: false,
      })
      expect(
        login.status(),
        `/auth/login answered ${login.status()} during the outage - a 5xx means ` +
          `the route followed the free tier down, a 404 that it is unmounted`,
      ).toBe(400)

      // Row 812: the public tier is still logging, so it is serving rather than
      // merely answering from a cache
      await expect
        .poll(() => cloudwatchHas(PUBLIC_LOG_GROUP, "HTTP/1.1", start), {
          ...CLOUDWATCH_POLL,
          message: "the public tier logged nothing during the outage",
        })
        .toBe(true)
    } finally {
      scaleService(FREE_SERVICE, before.desiredCount)
      await pollService(
        FREE_SERVICE,
        (s) => s.runningCount === before.desiredCount,
        `back to ${before.desiredCount} task(s)`,
      )
      await anon.dispose()
    }

    // Restored, and serving authenticated traffic again
    const after = describeService(FREE_SERVICE)
    expect(after.desiredCount, "the free service is back").toBe(
      before.desiredCount,
    )
    expect(after.status, "the free service is ACTIVE again").toBe("ACTIVE")
  })

  // Row 819: a rolling deployment of the public tier must not interrupt what it
  // serves. force-new-deployment is the same action a release performs.
  test("OUT-02 - A rolling public-tier deployment keeps serving throughout", async () => {
    test.setTimeout(900_000)
    const before = describeService(PUBLIC_SERVICE)
    expect(
      before.desiredCount,
      "the public service must be up",
    ).toBeGreaterThan(0)

    const anon = await request.newContext()
    const statuses: number[] = []
    try {
      // describe-services is eventually consistent, so without pinning the
      // deployment id the first probe can read the PREVIOUS deployment as a
      // completed PRIMARY and settle before ECS has placed anything.
      const priorDeployment = describeService(PUBLIC_SERVICE).deployments.find(
        (d) => d.status === "PRIMARY",
      )?.id
      awsJson(
        `ecs update-service --cluster ${CLUSTER} --service ${PUBLIC_SERVICE} ` +
          `--force-new-deployment --region ${AWS_REGION}`,
      )
      // Poll the front door while the deployment rolls; a rolling update keeps
      // the old task in service until the new one is healthy, so every one of
      // these must answer
      const deadline = Date.now() + 300_000
      let settled = false
      while (Date.now() < deadline) {
        const res = await anon.get(`${process.env.BASE_URL}/`, {
          failOnStatusCode: false,
        })
        statuses.push(res.status())
        // runningCount already equals desiredCount before ECS starts placing
        // the new task, so the rollout's own state is what says it finished.
        const s = describeService(PUBLIC_SERVICE)
        const primary = s.deployments.find((d) => d.status === "PRIMARY")
        expect(
          primary,
          "the service reported no PRIMARY deployment",
        ).toBeTruthy()
        expect(
          primary!.rolloutState,
          "the public deployment failed to roll out",
        ).not.toBe("FAILED")
        if (
          primary!.id !== priorDeployment &&
          s.deployments.length === 1 &&
          primary!.rolloutState === "COMPLETED" &&
          s.runningCount === before.desiredCount
        ) {
          settled = true
          break
        }
        await new Promise((r) => setTimeout(r, 10_000))
      }
      expect(settled, "the public deployment never settled").toBe(true)
    } finally {
      await anon.dispose()
    }

    // A handful of probes would satisfy the filter below without the
    // deployment having rolled under any of them.
    expect(
      statuses.length,
      "too few probes to claim the tier kept serving throughout",
    ).toBeGreaterThan(5)
    expect(
      statuses.filter((s) => s !== 200),
      `non-200 responses during the rolling deployment (${statuses.length} probes)`,
    ).toEqual([])
  })
})

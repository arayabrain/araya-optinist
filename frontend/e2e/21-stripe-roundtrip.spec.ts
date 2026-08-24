import { test, expect } from "@playwright/test"

import {
  STRIPE_USER,
  apiLogin,
  isLocalBaseUrl,
  runSql,
  stripeAccountSkipReason,
  sqlLiteral,
  sqlSkipReason,
  stripeGet,
  stripeSubscriptionFor,
} from "./helpers"

// The reversible half of the subscription lifecycle, against the real Stripe
// test account: cancel at period end, then undo it. Every other subscription
// spec mocks this, which proves our gesture but not that Stripe recorded it -
// and the rows this closes are specifically about what Stripe holds afterwards.
//
//   RUN_STRIPE_WRITE=1 npx playwright test e2e/21-stripe-roundtrip.spec.ts --retries 0
//
// Opt-in because it writes: it flips the premium test account's
// cancel_at_period_end and back. Nothing else may be reading that account while
// it runs - a scheduled cancellation changes what the subscription page renders,
// so SUB-04/05 would see the cancelled banner mid-flight. The undo is in a
// finally block and is asserted, because leaving the shared account cancelled
// is the one outcome worse than the test failing.
//
// Rows: 267 / 268 (cancel_at_period_end and the cancel timestamps), 269 / 274
// (the customer.subscription.updated events), 273 (the reactivation flips it
// back), BT-919 / BT-921. BT-920's Continue-Plan button on real state needs a
// premium UI login, which assigns a premium instance, so it stays with @prem.

const premiumUserId = `(SELECT id FROM users WHERE email = '${sqlLiteral(
  STRIPE_USER.email || "",
)}')`

function scheduledDowngrade(): string {
  return runSql(
    `SELECT scheduled_downgrade FROM subscription_users
       WHERE user_id = ${premiumUserId};`,
  )
}

// The end of the current billing period. Newer Stripe API versions carry this
// on the subscription item, not on the subscription, so reading it off the top
// level silently yields undefined.
function periodEnd(subscription: Record<string, any>): number {
  const items = (subscription.items?.data ?? []) as Record<string, any>[]
  const ends = items
    .map((item) => item.current_period_end as number)
    .filter((end) => typeof end === "number")
  expect(
    ends.length,
    "the subscription has no item with a period end",
  ).toBeGreaterThan(0)
  return Math.max(...ends)
}

// Update events for this subscription raised at or after `since` (epoch
// seconds). Scoped by time rather than counted inside a fixed page: the
// environment bills daily across several accounts, so a new event of ours can
// push an older one of ours out of any fixed window and leave a count
// unchanged.
function updateEventsSince(
  subscriptionId: string,
  since: number,
): Record<string, any>[] {
  const events = stripeGet("/v1/events", {
    type: "customer.subscription.updated",
    "created[gte]": since,
    limit: 100,
  }).data as Record<string, any>[]
  return events.filter((e) => e.data?.object?.id === subscriptionId)
}

test.describe("Stripe cancel / reactivate round-trip", () => {
  test.skip(
    isLocalBaseUrl(),
    "reads and writes the deployed environment's Stripe account; BASE_URL is local",
  )
  test.skip(
    !process.env.RUN_STRIPE_WRITE,
    "writes to a real Stripe subscription; opt in with RUN_STRIPE_WRITE=1",
  )
  // Cancelling runs through the deployed backend with that environment's own
  // Stripe key, which the repo's sk_live refusal does not cover.
  test.beforeAll(() => {
    expect(
      process.env.BASE_URL || "",
      "this lane only runs against the development environment",
    ).toContain("development-optinist")
  })

  test("STRIPE-01 - Cancelling schedules it in Stripe and reactivating clears it", async () => {
    const accountReason = stripeAccountSkipReason()
    test.skip(!!accountReason, accountReason)
    const sqlReason = sqlSkipReason()
    expect(
      sqlReason,
      `the deployed database must be readable: ${sqlReason}`,
    ).toBe("")
    test.setTimeout(180_000)

    const before = stripeSubscriptionFor(STRIPE_USER.email)
    // A subscription already scheduled for cancellation makes every assertion
    // below vacuous, and the undo would not be an undo.
    expect(
      before.cancel_at_period_end,
      "the account must start with no scheduled cancellation",
    ).toBe(false)
    expect(
      scheduledDowngrade(),
      "the database agrees it is not cancelled",
    ).toBe("0")
    // A second early, so an event raised in the same second still counts
    const cancelFrom = Math.floor(Date.now() / 1000) - 1

    const { api, headers } = await apiLogin(
      STRIPE_USER.email,
      STRIPE_USER.password,
    )
    const me = await api.get("/users/me", { headers })
    expect(me.ok()).toBeTruthy()
    const userId = (await me.json()).id

    try {
      const cancel = await api.delete("/api/subsc/mgmts/cancel", { headers })
      expect(cancel.ok(), `DELETE cancel: ${await cancel.text()}`).toBeTruthy()

      // Stripe's own record, not ours: the row is about what Stripe holds
      const cancelled = stripeSubscriptionFor(STRIPE_USER.email)
      expect(cancelled.id, "the same subscription, not a new one").toBe(
        before.id,
      )
      expect(
        cancelled.cancel_at_period_end,
        "Stripe has the cancellation scheduled",
      ).toBe(true)
      // cancel_at is when access ends; canceled_at is when the request landed
      expect(cancelled.cancel_at, "cancel_at is set").toBeTruthy()
      expect(cancelled.canceled_at, "canceled_at is set").toBeTruthy()
      // Stripe moved current_period_end off the subscription and onto its
      // items, so the period end is read from the item rather than the top
      // level, where it is simply absent.
      expect(
        cancelled.cancel_at,
        "access ends at the period end, not immediately",
      ).toBe(periodEnd(cancelled))
      expect(
        cancelled.status,
        "the subscription is still active until the period ends",
      ).toBe("active")

      // Our side agrees, and the plan is not rewritten to Free
      expect(scheduledDowngrade(), "the database recorded the schedule").toBe(
        "1",
      )
      expect(
        runSql(`SELECT plan_id FROM subscription_users
                  WHERE user_id = ${premiumUserId};`),
        "a scheduled cancellation must not downgrade the row",
      ).toBe("2")

      await expect
        .poll(() => updateEventsSince(before.id, cancelFrom).length, {
          timeout: 60_000,
          intervals: [5_000],
          message:
            "Stripe logged no customer.subscription.updated event for this " +
            "subscription after the cancel",
        })
        .toBeGreaterThan(0)
    } finally {
      const undo = await api.post(`/api/subsc/mgmts/reactivate/${userId}`, {
        headers,
      })
      // Asserted, not best-effort: a silent failure here leaves the shared
      // premium account scheduled for cancellation.
      expect(undo.ok(), `POST reactivate: ${await undo.text()}`).toBeTruthy()
      await api.dispose()
    }

    const restored = stripeSubscriptionFor(STRIPE_USER.email)
    expect(
      restored.cancel_at_period_end,
      "reactivating cleared the schedule in Stripe",
    ).toBe(false)
    expect(restored.cancel_at, "cancel_at was cleared").toBeNull()
    expect(scheduledDowngrade(), "and in the database").toBe("0")
  })
})

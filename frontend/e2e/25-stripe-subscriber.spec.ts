import { test, expect, request } from "@playwright/test"

import {
  apiLogin,
  apiUrl,
  firebaseAdminSkipReason,
  forceEmailVerified,
  liveStripeSkipReason,
  periodEnd,
  runSql,
  runSqlWriteOnDev,
  sqlLiteral,
  sqlSkipReason,
  stripeGet,
  stripePost,
} from "./helpers"

// Stripe rows that need a subscriber of their own: hosted checkout is behind
// hCaptcha, and the one shared Stripe-backed fixture (TEST_STRIPE_*) cannot be
// deleted or given a failing card. Each case registers a throwaway account and
// subscribes it through the Stripe API in test mode instead.
//
//   RUN_STRIPE_WRITE=1 npx playwright test e2e/25-stripe-subscriber.spec.ts
//
// Deployed development only: webhooks reach dev, not the local stack, and only
// dev has the tiered ALB routing SUBR-01 is about. SUBR-02 and SUBR-03 need
// customer.subscription.created to activate the account, which needs the API
// basil period-end fix (CheckoutService.subscription_period_end) deployed.
// SUBR-01 needs #1145 and its ALB rule p304 instead.
//
// Rows: SUBR-01 305 (+ #1142), SUBR-02 238-242 / 245, SUBR-03 288 / 292.

const PREMIUM = 2
const PASSWORD = "E2e-Stripe-Subscriber-1"
const DAY = 86_400
const FREE_QUOTA = 5 * 1073741824

type Subscriber = {
  email: string
  userId: string
  customerId: string
  subscriptionId: string
  paymentMethodId: string
}

const subscribers: Subscriber[] = []
const clocks: string[] = []
let skipReason = ""

function laneSkipReason(): string {
  if (!process.env.RUN_STRIPE_WRITE) {
    return "opt-in: set RUN_STRIPE_WRITE=1 (writes Stripe test mode and the dev RDS)"
  }
  if (!(process.env.BASE_URL || "").includes("development-optinist")) {
    return "deployed development only: Stripe webhooks never reach any other BASE_URL"
  }
  return liveStripeSkipReason() || firebaseAdminSkipReason() || sqlSkipReason()
}

function premiumPrice(): string {
  return runSql(
    `SELECT stripe_price_id FROM subscription_plans WHERE id = ${PREMIUM};`,
  )
}

function storedSubscription(userId: string) {
  const [plan, expiration, quota] = runSql(
    `SELECT s.plan_id, DATE_FORMAT(s.expiration, '%Y-%m-%dT%H:%i:%sZ'),
            u.storage_quota_bytes
       FROM subscription_users s
       LEFT JOIN user_storage_usage u ON u.user_id = s.user_id
       WHERE s.user_id = ${userId};`,
  ).split("\t")
  return {
    plan: Number(plan),
    expiration: Date.parse(expiration) / 1000,
    quota: Number(quota),
  }
}

async function waitForClock(clockId: string) {
  await expect
    .poll(() => stripeGet(`/v1/test_helpers/test_clocks/${clockId}`).status, {
      timeout: 180_000,
      intervals: [3_000],
    })
    .toBe("ready")
}

async function advanceClock(clockId: string, to: number) {
  stripePost(`/v1/test_helpers/test_clocks/${clockId}/advance`, {
    frozen_time: to,
  })
  await waitForClock(clockId)
}

// Registered, verified, linked to its own Stripe customer by the account row
// checkout would have written, and subscribed on pm_card_visa. Returns once
// the app has activated it from customer.subscription.created, unless asked not
// to wait.
async function newSubscriber(
  tag: string,
  { clockId, activate = true }: { clockId?: string; activate?: boolean } = {},
) {
  const email = `e2e_stripe_${tag}_${Date.now()}@test.com`
  const api = await request.newContext({ baseURL: apiUrl() })
  try {
    const reg = await api.post("/api/register", {
      data: {
        name: "E2E Stripe Subscriber",
        role_id: 20,
        email,
        password: PASSWORD,
      },
    })
    expect(reg.ok(), `POST /api/register: ${await reg.text()}`).toBeTruthy()
  } finally {
    await api.dispose()
  }
  forceEmailVerified(email)
  const userId = runSql(
    `SELECT id FROM users WHERE email = '${sqlLiteral(email)}';`,
  )

  const customer = stripePost("/v1/customers", {
    email,
    "metadata[user_id]": userId,
    ...(clockId ? { test_clock: clockId } : {}),
  })
  const subscriber: Subscriber = {
    email,
    userId,
    customerId: customer.id,
    subscriptionId: "",
    paymentMethodId: "",
  }
  subscribers.push(subscriber)
  runSqlWriteOnDev(
    `INSERT INTO subscription_user_accounts
       (user_id, provider_id, provider_customer_id)
       SELECT ${userId}, id, '${customer.id}' FROM subscription_providers
       WHERE name = 'stripe'`,
  )
  const card = stripePost("/v1/payment_methods/pm_card_visa/attach", {
    customer: customer.id,
  })
  stripePost(`/v1/customers/${customer.id}`, {
    "invoice_settings[default_payment_method]": card.id,
  })
  const subscription = stripePost("/v1/subscriptions", {
    customer: customer.id,
    "items[0][price]": premiumPrice(),
    "metadata[plan_id]": PREMIUM,
  })
  subscriber.subscriptionId = subscription.id
  subscriber.paymentMethodId = card.id
  if (!activate) return subscriber

  // Row 241: expiration is Stripe's period end, within webhook latency
  const end = periodEnd(subscription)
  await expect
    .poll(
      () => {
        const stored = storedSubscription(userId)
        return (
          stored.plan === PREMIUM && Math.abs(stored.expiration - end) <= 60
        )
      },
      {
        message:
          "customer.subscription.created never activated the account with " +
          "Stripe's period end; is the API basil period-end fix deployed?",
        timeout: 90_000,
        intervals: [5_000],
      },
    )
    .toBe(true)
  return subscriber
}

test.describe("Stripe throwaway subscriber", () => {
  test.beforeAll(() => {
    skipReason = laneSkipReason()
  })

  // Through the app's own deletion while the customer still exists, so its
  // cancel path finds that customer instead of creating one; then Stripe, so
  // nothing a crashed case left behind keeps billing daily.
  test.afterAll(async () => {
    for (const s of subscribers) {
      if (runSql(`SELECT active FROM users WHERE id = ${s.userId};`) !== "1") {
        continue
      }
      try {
        const { api, headers } = await apiLogin(s.email, PASSWORD)
        const res = await api.delete("/users/me", { headers })
        if (!res.ok()) {
          console.warn(
            `${s.email} not deleted: ${res.status()} ${await res.text()}`,
          )
        }
        await api.dispose()
      } catch (e) {
        console.warn(`${s.email} not deleted: ${(e as Error).message}`)
      }
    }
    for (const s of subscribers) {
      try {
        stripePost(`/v1/customers/${s.customerId}`, {}, "DELETE")
      } catch (e) {
        console.warn(`customer ${s.customerId}: ${(e as Error).message}`)
      }
    }
    for (const clock of clocks) {
      try {
        stripePost(`/v1/test_helpers/test_clocks/${clock}`, {}, "DELETE")
      } catch (e) {
        console.warn(`test clock ${clock}: ${(e as Error).message}`)
      }
    }
  })

  test("SUBR-01 - deleting a subscriber with only a Bearer token cancels its Stripe subscription", async () => {
    test.skip(!!skipReason, `row 305: ${skipReason}`)
    test.setTimeout(300_000)
    // Deletion cancels what Stripe bills whether or not the app activated it
    const s = await newSubscriber("delete", { activate: false })

    // No premium routing headers, as curl or a stale client would send. Until
    // the #1142 ALB rule (p304) is applied this lands on the public tier, which
    // has no Stripe key: it answers 503 with #1145's guard, or 200 without
    // cancelling before it. Expected to FAIL on dev until both are deployed.
    const { api, headers } = await apiLogin(s.email, PASSWORD)
    try {
      const res = await api.delete("/users/me", {
        headers: { Authorization: headers.Authorization },
      })
      expect(res.ok(), `DELETE /users/me: ${await res.text()}`).toBeTruthy()
    } finally {
      await api.dispose()
    }

    await expect
      .poll(() => stripeGet(`/v1/subscriptions/${s.subscriptionId}`).status, {
        message:
          "the deleted account's Stripe subscription is still billing " +
          "(issue #1142: DELETE /users/me served by the public tier)",
        timeout: 120_000,
        intervals: [5_000],
      })
      .toBe("canceled")
    const customer = stripeGet(`/v1/customers/${s.customerId}`)
    expect(customer.deleted, "the customer record is retained").toBeFalsy()
    const invoices = stripeGet("/v1/invoices", {
      customer: s.customerId,
      limit: 10,
    }).data as Record<string, any>[]
    expect(
      invoices.some((i) => i.status === "paid"),
      "the paid invoice is still readable",
    ).toBe(true)
  })

  test("SUBR-02 - a fresh subscriber reads back the same in Stripe and in the app", async () => {
    test.skip(!!skipReason, `rows 238-242 / 245: ${skipReason}`)
    test.setTimeout(300_000)
    const s = await newSubscriber("fresh")
    const sub = stripeGet(`/v1/subscriptions/${s.subscriptionId}`, {
      "expand[]": "latest_invoice",
    }) as Record<string, any>
    const invoice = sub.latest_invoice as Record<string, any>
    const price = sub.items.data[0].price as Record<string, any>

    // 238
    expect(sub.status).toBe("active")
    expect(price.id).toBe(premiumPrice())
    expect(invoice.status).toBe("paid")
    expect(periodEnd(sub)).toBeGreaterThan(Date.now() / 1000)

    // 239
    const customers = stripeGet("/v1/customers", {
      email: s.email,
      limit: 10,
    }).data as Record<string, any>[]
    expect(customers.map((c) => c.id)).toEqual([s.customerId])
    expect(Date.now() / 1000 - customers[0].created).toBeLessThan(900)

    // 240
    expect(
      runSql(
        `SELECT a.provider_customer_id FROM subscription_user_accounts a
           JOIN subscription_providers p ON a.provider_id = p.id
           WHERE a.user_id = ${s.userId} AND p.name = 'stripe';`,
      ),
    ).toBe(s.customerId)

    // 242
    const intents = stripeGet("/v1/payment_intents", {
      customer: s.customerId,
      limit: 10,
    }).data as Record<string, any>[]
    expect(intents.length, "payment intents for the initial payment").toBe(1)
    const intent = intents[0]
    expect(intent.status).toBe("succeeded")
    expect(intent.amount).toBe(invoice.amount_paid)
    expect(intent.currency).toBe(invoice.currency)
    expect(intent.payment_method).toBe(s.paymentMethodId)
    const charge = stripeGet(`/v1/charges/${intent.latest_charge}`)
    expect(charge.captured).toBe(true)
    expect(charge.failure_code).toBeNull()
    expect(charge.receipt_url, "a receipt was generated").toBeTruthy()

    // 245, through the API the invoice page renders
    const { api, headers } = await apiLogin(s.email, PASSWORD)
    let ours: Record<string, any>[]
    try {
      const res = await api.get(`/api/subsc/invoices/${s.userId}`, { headers })
      expect(res.ok(), `GET invoices: ${await res.text()}`).toBeTruthy()
      ours = await res.json()
    } finally {
      await api.dispose()
    }
    expect(ours.map((i) => i.id)).toEqual([invoice.id])
    expect(ours[0].status.toLowerCase()).toBe("paid")
    expect(ours[0].amount_paid).toBe(invoice.amount_paid)
    expect(invoice.subtotal, "the plan price").toBe(price.unit_amount)
    expect(new Date(ours[0].date).toISOString().slice(0, 10)).toBe(
      new Date(sub.start_date * 1000).toISOString().slice(0, 10),
    )
  })

  test("SUBR-03 - a failing renewal goes past due, then canceled, and the app follows", async () => {
    test.skip(!!skipReason, `rows 288 / 292: ${skipReason}`)
    test.setTimeout(1_200_000)
    const t0 = Math.floor(Date.now() / 1000)
    const clock = stripePost("/v1/test_helpers/test_clocks", {
      frozen_time: t0,
      name: "e2e-subr-03",
    })
    clocks.push(clock.id)
    const s = await newSubscriber("renewal_fail", { clockId: clock.id })

    // Attaches, then declines every charge
    const failing = stripePost(
      "/v1/payment_methods/pm_card_chargeCustomerFail/attach",
      { customer: s.customerId },
    )
    stripePost(`/v1/customers/${s.customerId}`, {
      "invoice_settings[default_payment_method]": failing.id,
    })
    const firstEnd = periodEnd(
      stripeGet(`/v1/subscriptions/${s.subscriptionId}`),
    )
    await advanceClock(clock.id, firstEnd + 3600)

    // 288
    const pastDue = stripeGet(`/v1/subscriptions/${s.subscriptionId}`, {
      "expand[]": "latest_invoice",
    }) as Record<string, any>
    expect(["past_due", "unpaid"]).toContain(pastDue.status)
    const renewal = pastDue.latest_invoice as Record<string, any>
    expect(renewal.billing_reason).toBe("subscription_cycle")
    expect(renewal.status).toBe("open")
    expect(renewal.attempt_count).toBeGreaterThan(0)
    const intent = (
      stripeGet("/v1/payment_intents", { customer: s.customerId, limit: 1 })
        .data as Record<string, any>[]
    )[0]
    expect(intent.status).toBe("requires_payment_method")
    expect(
      intent.last_payment_error,
      "the renewal charge was declined",
    ).toBeTruthy()

    // Grace while Stripe retries: the failed renewal's period is mirrored, and
    // the account keeps premium on it. The app runs on real time, not the
    // clock's, so the first period alone would read premium here too.
    const renewedEnd = periodEnd(pastDue)
    expect(
      renewedEnd,
      "the failed renewal opened a new period",
    ).toBeGreaterThan(firstEnd)
    await expect
      .poll(
        () =>
          Math.abs(storedSubscription(s.userId).expiration - renewedEnd) <= 60,
        {
          message: "the past-due period end was not mirrored into the app",
          timeout: 90_000,
          intervals: [5_000],
        },
      )
      .toBe(true)
    const { api, headers } = await apiLogin(s.email, PASSWORD)
    try {
      const me = await api.get("/users/me", { headers })
      expect(me.ok(), `GET /users/me: ${await me.text()}`).toBeTruthy()
      expect((await me.json()).subscription_status).toBe("Premium")
    } finally {
      await api.dispose()
    }

    // 292: through Stripe's retries to the account's final dunning action.
    // A test clock moves at most two billing intervals per advance.
    let now = firstEnd + 3600
    let status = pastDue.status
    while (status !== "canceled" && now - t0 < 40 * DAY) {
      now += 2 * DAY
      await advanceClock(clock.id, now)
      status = stripeGet(`/v1/subscriptions/${s.subscriptionId}`).status
    }
    expect(status, "Stripe's retries end in cancellation").toBe("canceled")
    const ended = stripeGet(`/v1/subscriptions/${s.subscriptionId}`) as Record<
      string,
      any
    >
    expect(ended.ended_at).toBeTruthy()
    const invoices = stripeGet("/v1/invoices", {
      customer: s.customerId,
      limit: 100,
    }).data as Record<string, any>[]
    expect(
      invoices.some(
        (i) =>
          i.billing_reason === "subscription_create" && i.status === "paid",
      ),
      "the initial paid invoice is preserved",
    ).toBe(true)
    expect(
      invoices.filter((i) => i.created > ended.ended_at).map((i) => i.id),
      "no invoice after the cancellation",
    ).toEqual([])
    expect
      .soft(
        invoices
          .filter((i) => i.status === "open" && i.next_payment_attempt)
          .map((i) => i.id),
        "open invoices Stripe still plans to charge after the cancellation " +
          "(the account's revenue-recovery setting leaves them open)",
      )
      .toEqual([])

    // The app's fallback: customer.subscription.deleted ends access now and
    // drops the quota to free
    await expect
      .poll(
        () => {
          const stored = storedSubscription(s.userId)
          return (
            stored.expiration <= Date.now() / 1000 + 60 &&
            stored.quota === FREE_QUOTA
          )
        },
        {
          message:
            "customer.subscription.deleted did not end access in the app",
          timeout: 90_000,
          intervals: [5_000],
        },
      )
      .toBe(true)
  })
})

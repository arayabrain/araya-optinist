/**
 * The free-trial wording on the Subscription page.
 *
 * - The paid plan card leads with the trial (ribbon, headline, button label,
 *   terms under the button) only once the API has answered that the user has
 *   no subscription: anyone who bought before gets no trial, and a pending or
 *   failed request says nothing about it. The price after the trial stays on
 *   the card.
 * - The Current Plan box names a running trial's end, which is the first
 *   charge, not an expiry.
 *
 * Driven against the real subscription reducer, so the order in which the two
 * requests settle is the one the page sees.
 */

/* eslint-disable no-undef -- `jest` is the injected global here rather than the
   `@jest/globals` import, because babel-plugin-jest-hoist rejects an imported
   `jest` being referenced inside a `jest.mock` factory. */

import { Provider } from "react-redux"
import { MemoryRouter } from "react-router-dom"

import { describe, it, expect, beforeEach } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"
import "@testing-library/jest-dom"
import { render, screen, waitFor, within } from "@testing-library/react"

import { SubscriptionUserStatus } from "const/Subscription"
import SubscriptionPlans from "pages/Subscription"
import subscriptionReducer from "store/slice/Subscriptions/SubscriptionSlice"

const mockGetSubscriptionPlansApi = jest.fn()
const mockGetUserSubscriptionApi = jest.fn()
jest.mock("api/subscriptions/Subscriptions", () => ({
  getSubscriptionPlansApi: () => mockGetSubscriptionPlansApi(),
  getUserSubscriptionApi: () => mockGetUserSubscriptionApi(),
}))

const plan = (id: number, name: string, price: number) => ({
  id,
  name,
  price,
  billing_cycle: 1,
  features: {},
  currency: 1,
  status: true,
  created_at: "2026-01-01T00:00:00",
})

const PLANS = [plan(1, "Free", 0), plan(2, "Premium", 5000)]

const OFFER = {
  ribbon: "Free trial for first-time subscribers",
  headline: "30 days free",
  button: "Start 30-day free trial",
  terms:
    "No charge today. After 30 days, $50/month + applicable taxes unless you cancel.",
}

// No part of the offer, and the plain price and button
const expectNoOffer = (card: HTMLElement) => {
  for (const text of [OFFER.ribbon, OFFER.headline, OFFER.terms]) {
    expect(within(card).queryByText(text)).not.toBeInTheDocument()
  }
  expect(
    within(card).queryByRole("button", { name: OFFER.button }),
  ).not.toBeInTheDocument()
}

const expectPlainPremium = (card: HTMLElement) => {
  expectNoOffer(card)
  expect(within(card).getByText("$50/month")).toBeInTheDocument()
  expect(within(card).getByRole("button", { name: "Upgrade" })).toBeEnabled()
}

const later = <T,>(ms: number, value: T) =>
  new Promise<T>((resolve) => setTimeout(() => resolve(value), ms))

const renderPlans = () => {
  const store = configureStore({
    reducer: {
      subscription: subscriptionReducer,
      user: () => ({ currentUser: { id: 1 }, loading: false }),
    },
  })
  render(
    <Provider store={store}>
      <MemoryRouter>
        <SubscriptionPlans />
      </MemoryRouter>
    </Provider>,
  )
  return store
}

describe("Subscription page trial offer", () => {
  beforeEach(() => {
    mockGetSubscriptionPlansApi.mockReset()
    mockGetUserSubscriptionApi.mockReset()
    mockGetSubscriptionPlansApi.mockResolvedValue(PLANS)
  })

  it("offers the trial on the paid card to a user who never subscribed", async () => {
    mockGetUserSubscriptionApi.mockResolvedValue(null)

    renderPlans()

    const premium = await screen.findByTestId("plan-card-Premium")
    expect(await within(premium).findByText(OFFER.ribbon)).toBeInTheDocument()
    expect(within(premium).getByText(OFFER.headline)).toBeInTheDocument()
    // The price after the trial stays on the card
    expect(within(premium).getByText("then")).toBeInTheDocument()
    expect(within(premium).getByText("$50/month")).toBeInTheDocument()
    expect(
      within(premium).getByRole("button", { name: OFFER.button }),
    ).toBeEnabled()
    expect(within(premium).getByText(OFFER.terms)).toBeInTheDocument()
    expect(
      within(premium).queryByRole("button", { name: "Upgrade" }),
    ).not.toBeInTheDocument()
    expectNoOffer(screen.getByTestId("plan-card-Free"))
  })

  it("leaves the card unchanged for a user who subscribed before", async () => {
    // A lapsed subscription: the user is free again but gets no second trial
    mockGetUserSubscriptionApi.mockResolvedValue({
      id: 1,
      plan_id: 2,
      user_id: 1,
      expiration: "2026-01-01T00:00:00+00:00",
      is_expired: true,
      scheduled_downgrade: false,
      status: SubscriptionUserStatus.EXPIRED,
      plan_name: "Premium",
      plan_price: 5000,
      is_trial: false,
      trial_end: null,
    })

    const store = renderPlans()

    await waitFor(() =>
      expect(store.getState().subscription.userSubscription).not.toBeNull(),
    )
    expectPlainPremium(screen.getByTestId("plan-card-Premium"))
  })

  it("waits for the subscription when the plans arrive first", async () => {
    mockGetUserSubscriptionApi.mockReturnValue(new Promise(() => {}))

    renderPlans()

    expectPlainPremium(await screen.findByTestId("plan-card-Premium"))
  })

  it("offers nothing when the subscription cannot be read", async () => {
    // The plans arriving after the failure clear the shared error, so only
    // the failed request itself tells this from "never subscribed"
    mockGetUserSubscriptionApi.mockRejectedValue(new Error("network"))
    mockGetSubscriptionPlansApi.mockImplementation(() => later(20, PLANS))

    const store = renderPlans()

    expect(await screen.findByTestId("plan-card-Premium")).toBeInTheDocument()
    await waitFor(() =>
      expect(store.getState().subscription.plansLoading).toBe(false),
    )
    expect(store.getState().subscription.error).toBeNull()
    expectPlainPremium(screen.getByTestId("plan-card-Premium"))
  })
})

describe("Subscription page Current Plan box", () => {
  const TRIAL_END = "2026-12-17T10:59:20+00:00"
  const localDate = (iso: string) => new Date(iso).toLocaleDateString()

  const premium = (overrides: object) => ({
    id: 1,
    plan_id: 2,
    user_id: 1,
    expiration: TRIAL_END,
    is_expired: false,
    scheduled_downgrade: false,
    status: SubscriptionUserStatus.SUBSCRIBED,
    plan_name: "Premium",
    plan_price: 5000,
    is_trial: true,
    trial_end: TRIAL_END,
    ...overrides,
  })

  beforeEach(() => {
    mockGetSubscriptionPlansApi.mockReset()
    mockGetUserSubscriptionApi.mockReset()
    mockGetSubscriptionPlansApi.mockResolvedValue(PLANS)
  })

  it("names the trial end while a trial runs", async () => {
    mockGetUserSubscriptionApi.mockResolvedValue(premium({}))

    renderPlans()

    expect(
      await screen.findByText(`Free trial ends on ${localDate(TRIAL_END)}`),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Expires on/)).not.toBeInTheDocument()
  })

  it("keeps Expires on for a trial cancelled before its end", async () => {
    mockGetUserSubscriptionApi.mockResolvedValue(
      premium({ scheduled_downgrade: true }),
    )

    renderPlans()

    expect(
      await screen.findByText(`Expires on ${localDate(TRIAL_END)}`),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Free trial/)).not.toBeInTheDocument()
  })

  it("keeps Expires on for a trial that has converted to paid", async () => {
    const periodEnd = "2026-11-15T00:00:00+00:00"
    mockGetUserSubscriptionApi.mockResolvedValue(
      premium({
        expiration: periodEnd,
        is_trial: false,
        trial_end: "2026-10-15T00:00:00+00:00",
      }),
    )

    renderPlans()

    expect(
      await screen.findByText(`Expires on ${localDate(periodEnd)}`),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Free trial/)).not.toBeInTheDocument()
  })
})

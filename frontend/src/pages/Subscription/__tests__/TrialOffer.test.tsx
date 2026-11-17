/**
 * The free-trial line on the Subscription page's paid plan card.
 *
 * Shown only once the API has answered that the user has no subscription:
 * anyone who bought before gets no trial, and a pending or failed request says
 * nothing about it. Driven against the real subscription reducer, so the order
 * in which the two requests settle is the one the page sees.
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

const TRIAL_OFFER = "First-time subscribers get 30 days free"

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
    expect(await within(premium).findByText(TRIAL_OFFER)).toBeInTheDocument()
    expect(
      within(screen.getByTestId("plan-card-Free")).queryByText(TRIAL_OFFER),
    ).not.toBeInTheDocument()
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
    expect(screen.getByTestId("plan-card-Premium")).toBeInTheDocument()
    expect(screen.queryByText(TRIAL_OFFER)).not.toBeInTheDocument()
  })

  it("waits for the subscription when the plans arrive first", async () => {
    mockGetUserSubscriptionApi.mockReturnValue(new Promise(() => {}))

    renderPlans()

    expect(await screen.findByTestId("plan-card-Premium")).toBeInTheDocument()
    expect(screen.queryByText(TRIAL_OFFER)).not.toBeInTheDocument()
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
    expect(screen.queryByText(TRIAL_OFFER)).not.toBeInTheDocument()
  })
})

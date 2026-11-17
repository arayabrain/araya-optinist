/**
 * The free-trial line on the Subscription page's paid plan card.
 *
 * Shown only to a user the API has no subscription for: anyone who bought
 * before gets no trial, so they keep the card as it was.
 */

import { Provider } from "react-redux"
import { MemoryRouter } from "react-router-dom"

import configureMockStore from "redux-mock-store"

import { describe, it, expect, jest } from "@jest/globals"
import "@testing-library/jest-dom"
import { render, screen, within } from "@testing-library/react"

import { SubscriptionUserStatus } from "const/Subscription"
import SubscriptionPlans from "pages/Subscription"

jest.mock("store/slice/Subscriptions/SubscriptionActions", () => ({
  getSubscriptionPlan: () => ({
    type: "subscription/getSubscriptionPlan/mock",
  }),
  getUserSubscription: () => ({
    type: "subscription/getUserSubscription/mock",
  }),
  createCheckoutSession: () => ({ type: "subscription/createCheckout/mock" }),
  cancelSubscription: () => ({ type: "subscription/cancel/mock" }),
  reactivateSubscription: () => ({ type: "subscription/reactivate/mock" }),
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

const TRIAL_OFFER = "First-time subscribers get 30 days free"

const renderPlans = (userSubscription: Record<string, unknown> | null) =>
  render(
    <Provider
      store={configureMockStore([])({
        user: { currentUser: { id: 1 }, loading: false },
        subscription: {
          plans: [plan(1, "Free", 0), plan(2, "Premium", 5000)],
          userSubscription,
          loading: false,
          checkoutLoading: false,
          error: null,
          plansLoading: false,
          userSubscriptionLoading: false,
          serverTime: null,
          deletionPriority: null,
          deletionPriorityLoading: false,
        },
      })}
    >
      <MemoryRouter>
        <SubscriptionPlans />
      </MemoryRouter>
    </Provider>,
  )

describe("Subscription page trial offer", () => {
  it("offers the trial on the paid card to a user who never subscribed", () => {
    renderPlans(null)

    const premium = screen.getByTestId("plan-card-Premium")
    expect(within(premium).getByText(TRIAL_OFFER)).toBeInTheDocument()
    expect(
      within(screen.getByTestId("plan-card-Free")).queryByText(TRIAL_OFFER),
    ).not.toBeInTheDocument()
  })

  it("leaves the card unchanged for a user who subscribed before", () => {
    // A lapsed subscription: the user is free again but gets no second trial
    renderPlans({
      id: 1,
      plan_id: 2,
      user_id: 1,
      expiration: "2026-01-01T00:00:00",
      is_expired: true,
      scheduled_downgrade: false,
      status: SubscriptionUserStatus.EXPIRED,
      plan_name: "Premium",
      plan_price: 5000,
      is_trial: false,
      trial_end: null,
    })

    expect(screen.getByTestId("plan-card-Premium")).toBeInTheDocument()
    expect(screen.queryByText(TRIAL_OFFER)).not.toBeInTheDocument()
  })
})

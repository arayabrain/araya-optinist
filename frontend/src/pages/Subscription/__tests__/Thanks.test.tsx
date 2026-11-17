/**
 * The checkout thanks page: a trial checkout takes no payment, so the page names
 * the first charge date in place of the payment-email sentence.
 *
 * Driven against the real subscription reducer, so the response fields, the
 * slice that copies them and the page that reads them are exercised together.
 */

/* eslint-disable no-undef -- `jest` is the injected global here rather than the
   `@jest/globals` import, because babel-plugin-jest-hoist rejects an imported
   `jest` being referenced inside a `jest.mock` factory. */

import { Provider } from "react-redux"
import { MemoryRouter } from "react-router-dom"

import { describe, it, expect, beforeEach } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"
import "@testing-library/jest-dom"
import { render, screen } from "@testing-library/react"

import { SubscriptionUserStatus } from "const/Subscription"
import Thanks from "pages/Subscription/thanks"
import subscriptionReducer from "store/slice/Subscriptions/SubscriptionSlice"

const mockGetUserSubscriptionApi = jest.fn()
jest.mock("api/subscriptions/Subscriptions", () => ({
  getUserSubscriptionApi: () => mockGetUserSubscriptionApi(),
}))

// The session check has its own retries and redirects; here it succeeds
jest.mock("components/utils/ValidateCheckoutSession", () => ({
  validateSession: async (
    _sessionId: string | null,
    setStatus: (status: string) => void,
    setIsLoading: (loading: boolean) => void,
  ) => {
    setStatus("success")
    setIsLoading(false)
  },
}))

const subscriptionResponse = (fields: Record<string, unknown>) => ({
  id: 1,
  plan_id: 2,
  user_id: 1,
  expiration: "2027-01-15T00:00:00",
  is_expired: false,
  scheduled_downgrade: false,
  status: SubscriptionUserStatus.SUBSCRIBED,
  plan_name: "Premium",
  plan_price: 5000,
  is_trial: false,
  trial_end: null,
  ...fields,
})

const PAYMENT_EMAIL = "We've sent you an email with the payment information."

const renderThanks = () =>
  render(
    <Provider
      store={configureStore({ reducer: { subscription: subscriptionReducer } })}
    >
      <MemoryRouter>
        <Thanks />
      </MemoryRouter>
    </Provider>,
  )

describe("Thanks page after checkout", () => {
  beforeEach(() => {
    mockGetUserSubscriptionApi.mockReset()
  })

  it("names the first charge date when the checkout started a trial", async () => {
    mockGetUserSubscriptionApi.mockResolvedValue(
      subscriptionResponse({
        is_trial: true,
        trial_end: "2027-01-15T00:00:00",
      }),
    )

    renderThanks()

    expect(
      await screen.findByText(
        "Your 30-day free trial has started. Your first payment will be taken on January 15, 2027.",
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText(PAYMENT_EMAIL)).not.toBeInTheDocument()
  })

  it("names no charge date for a trial whose cancellation is scheduled", async () => {
    // The checkout URL reopened after cancelling: the trial still runs, but no
    // payment follows it
    mockGetUserSubscriptionApi.mockResolvedValue(
      subscriptionResponse({
        is_trial: true,
        trial_end: "2027-01-15T00:00:00",
        scheduled_downgrade: true,
      }),
    )

    renderThanks()

    expect(await screen.findByText(PAYMENT_EMAIL)).toBeInTheDocument()
    expect(screen.queryByText(/first payment/)).not.toBeInTheDocument()
  })

  it("keeps the payment sentence for a paid checkout", async () => {
    mockGetUserSubscriptionApi.mockResolvedValue(subscriptionResponse({}))

    renderThanks()

    expect(await screen.findByText(PAYMENT_EMAIL)).toBeInTheDocument()
    expect(screen.queryByText(/free trial/)).not.toBeInTheDocument()
  })

  it("still shows the result when the subscription cannot be read", async () => {
    mockGetUserSubscriptionApi.mockRejectedValue(new Error("network"))

    renderThanks()

    expect(await screen.findByText("Thank you!")).toBeInTheDocument()
    expect(screen.getByText(PAYMENT_EMAIL)).toBeInTheDocument()
  })
})

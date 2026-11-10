import { Provider } from "react-redux"
import { MemoryRouter } from "react-router-dom"

import configureStore from "redux-mock-store"

import { describe, it, expect, jest } from "@jest/globals"
import { render, screen } from "@testing-library/react"

import { SubscriptionUserStatus } from "const/Subscription"
import InvoicesPage from "pages/Invoice"

jest.mock("store/slice/Subscriptions/SubscriptionActions", () => ({
  getUserSubscription: () => ({
    type: "subscription/getUserSubscription/mock",
  }),
}))

jest.mock("store/slice/PaymentMethod/PaymentMethodActions", () => ({
  getDefaultPaymentMethod: () => ({
    type: "paymentMethods/getDefaultPaymentMethod/mock",
  }),
  getUserInvoices: () => ({ type: "paymentMethods/getUserInvoices/mock" }),
}))

const mockStore = configureStore([])

const hoursFromNowAsNaiveUtc = (hours: number): string =>
  new Date(Date.now() + hours * 3600_000).toISOString().replace("Z", "")

const renderInvoice = (subscription: Record<string, unknown>) =>
  render(
    <Provider
      store={mockStore({
        user: { currentUser: { id: 1 }, loading: false },
        subscription: {
          plans: [],
          userSubscription: {
            id: 1,
            plan_id: 2,
            user_id: 1,
            scheduled_downgrade: false,
            status: SubscriptionUserStatus.SUBSCRIBED,
            plan_name: "Premium",
            plan_price: 10,
            ...subscription,
          },
          loading: false,
          checkoutLoading: false,
          error: null,
          plansLoading: false,
          userSubscriptionLoading: false,
          serverTime: null,
          deletionPriority: null,
          deletionPriorityLoading: false,
        },
        paymentMethod: {
          defaultPaymentMethod: null,
          allPaymentMethods: [],
          invoices: [],
          loading: {
            defaultPaymentMethod: false,
            allPaymentMethods: false,
            invoices: false,
          },
          error: {
            defaultPaymentMethod: null,
            allPaymentMethods: null,
            invoices: null,
          },
        },
      })}
    >
      <MemoryRouter>
        <InvoicesPage />
      </MemoryRouter>
    </Provider>,
  )

describe("Invoice page subscription expiration", () => {
  it("trusts is_expired=false although the naive UTC expiration parses as local time", async () => {
    renderInvoice({
      expiration: hoursFromNowAsNaiveUtc(3),
      is_expired: false,
    })

    expect(
      await screen.findByText(/Your subscription will renew on/),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Downgrade" }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/expired on/)).not.toBeInTheDocument()
  })

  it("shows expired on and Upgrade when is_expired=true", async () => {
    renderInvoice({
      expiration: hoursFromNowAsNaiveUtc(-3),
      is_expired: true,
    })

    expect(
      await screen.findByText(/Your subscription expired on/),
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Upgrade" })).toBeInTheDocument()
  })
})

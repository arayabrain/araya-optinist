import { Provider } from "react-redux"
import { MemoryRouter } from "react-router-dom"

import { SnackbarProvider } from "notistack"
import configureStore from "redux-mock-store"

import {
  describe,
  it,
  expect,
  beforeEach,
  afterEach,
  jest,
} from "@jest/globals"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

import InvoicesPage from "pages/Invoice"

const mockPortal = jest.fn<Promise<string>, []>()

jest.mock("api/subscriptions/Subscriptions", () => ({
  createPortalSessionApi: () => mockPortal(),
}))

jest.mock("store/slice/PaymentMethod/PaymentMethodActions", () => ({
  getDefaultPaymentMethod: () => ({ type: "paymentMethod/default/mock" }),
  getUserInvoices: () => ({ type: "paymentMethod/invoices/mock" }),
}))

jest.mock("store/slice/Subscriptions/SubscriptionActions", () => ({
  getUserSubscription: () => ({ type: "subscription/get/mock" }),
  getUTCServerTime: () => ({
    type: "subscription/serverTime/mock",
    payload: { server_time: "2026-11-10T00:00:00Z" },
  }),
}))

const store = configureStore([])({
  user: { currentUser: { id: 1 } },
  subscription: { userSubscription: null, error: null },
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
})

const clickManageBilling = async () => {
  render(
    <Provider store={store}>
      <MemoryRouter>
        <SnackbarProvider>
          <InvoicesPage />
        </SnackbarProvider>
      </MemoryRouter>
    </Provider>,
  )
  const button = await screen.findByRole("button", { name: "Manage Billing" })
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false))
  fireEvent.click(button)
}

describe("Manage Billing", () => {
  const originalLocation = window.location
  const assign = jest.fn()

  beforeEach(() => {
    assign.mockClear()
    Object.defineProperty(window, "location", {
      value: { ...originalLocation, assign },
      writable: true,
      configurable: true,
    })
  })

  afterEach(() => {
    Object.defineProperty(window, "location", {
      value: originalLocation,
      writable: true,
      configurable: true,
    })
  })

  it("redirects to the portal session url from the backend", async () => {
    mockPortal.mockResolvedValue("https://billing.stripe.com/p/session/test_x")

    await clickManageBilling()

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(
        "https://billing.stripe.com/p/session/test_x",
      ),
    )
  })

  it("shows an error and stays on the page when the session fails", async () => {
    mockPortal.mockRejectedValue(new Error("404"))

    await clickManageBilling()

    expect(
      await screen.findByText("Could not open the billing portal"),
    ).toBeTruthy()
    expect(assign).not.toHaveBeenCalled()
  })
})

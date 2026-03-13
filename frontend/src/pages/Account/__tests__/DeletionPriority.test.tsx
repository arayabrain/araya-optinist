import { Provider } from "react-redux"
import { MemoryRouter } from "react-router-dom"

import { SnackbarProvider } from "notistack"
import configureStore from "redux-mock-store"

import {
  describe,
  it,
  expect,
  beforeEach,
  jest,
  afterEach,
} from "@jest/globals"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"

import { SubscriptionUserStatus } from "const/Subscription"
import Account from "pages/Account"

jest.mock("api/subscriptions/Subscriptions")

jest.mock("utils/auth/AuthUtils", () => ({
  getToken: () => "mock-token",
}))

// Mock user actions to prevent unrelated thunk dispatches
jest.mock("store/slice/User/UserActions", () => ({
  getMe: () => ({ type: "user/getMe/mock" }),
  updateMe: () => ({ type: "user/updateMe/mock" }),
  updateMePassword: () => ({ type: "user/updateMePassword/mock" }),
  deleteMe: () => ({ type: "user/deleteMe/mock" }),
}))

// Mock subscription actions to return plain actions
jest.mock("store/slice/Subscriptions/SubscriptionActions", () => ({
  getUserSubscription: () => ({
    type: "subscription/getUserSubscription/mock",
  }),
  getDeletionPriority: () => ({
    type: "subscription/getDeletionPriority/mock",
  }),
  updateDeletionPriority: (priority: string) => ({
    type: "subscription/updateDeletionPriority/pending",
    meta: { arg: priority },
  }),
}))

const mockStoreCreator = configureStore([])

const createStore = (overrides: Record<string, unknown> = {}) =>
  mockStoreCreator({
    user: {
      currentUser: {
        id: 1,
        name: "Test User",
        email: "test@example.com",
        data_usage: 1000,
        attributes: { remote_bucket_name: "test-bucket" },
      },
      loading: false,
      ...((overrides.user as Record<string, unknown>) || {}),
    },
    subscription: {
      plans: [],
      userSubscription: {
        id: 1,
        plan_id: 2,
        user_id: 1,
        expiration: "2026-12-31",
        is_expired: false,
        scheduled_downgrade: false,
        status: SubscriptionUserStatus.SUBSCRIBED,
        plan_name: "Premium",
        plan_price: 10,
      },
      loading: false,
      checkoutLoading: false,
      error: null,
      plansLoading: false,
      userSubscriptionLoading: false,
      serverTime: null,
      deletionPriority: "preserve_outputs",
      deletionPriorityLoading: false,
      ...((overrides.subscription as Record<string, unknown>) || {}),
    },
  })

const renderAccount = (store: ReturnType<typeof mockStoreCreator>) =>
  render(
    <Provider store={store}>
      <MemoryRouter>
        <SnackbarProvider>
          <Account />
        </SnackbarProvider>
      </MemoryRouter>
    </Provider>,
  )

describe("DeletionPriority dropdown", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it("renders with default value", async () => {
    const store = createStore()
    renderAccount(store)

    await waitFor(() => {
      expect(screen.getByText("Data Deletion Priority")).toBeTruthy()
      expect(screen.getByText("Preserve Outputs")).toBeTruthy()
    })
  })

  it("renders with stored value", async () => {
    const store = createStore({
      subscription: { deletionPriority: "preserve_inputs" },
    })
    renderAccount(store)

    await waitFor(() => {
      expect(screen.getByText("Preserve Inputs")).toBeTruthy()
    })
  })

  it("dispatches update action on change", async () => {
    const store = createStore()
    renderAccount(store)

    await waitFor(() => {
      expect(screen.getByText("Preserve Outputs")).toBeTruthy()
    })

    // Open the select dropdown
    const selectEl = screen.getByText("Preserve Outputs")
    fireEvent.mouseDown(selectEl)

    // Click the "Preserve Inputs" option in the dropdown menu
    await waitFor(() => {
      const option = screen.getByRole("option", { name: "Preserve Inputs" })
      fireEvent.click(option)
    })

    // Verify dispatch was called with the right action
    const actions = store.getActions()
    expect(
      actions.some(
        (a: { type: string }) =>
          a.type === "subscription/updateDeletionPriority/pending",
      ),
    ).toBeTruthy()
  })

  it("renders label when error state is set", async () => {
    const store = createStore({
      subscription: { error: "Failed to update deletion priority" },
    })
    renderAccount(store)

    await waitFor(() => {
      expect(screen.getByText("Data Deletion Priority")).toBeTruthy()
    })
  })
})

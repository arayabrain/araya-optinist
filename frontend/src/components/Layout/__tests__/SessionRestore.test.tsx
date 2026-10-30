/**
 * A reload starts with no access token in memory, so a protected page must wait
 * for the cookie restore before deciding the user is signed out.
 */

import { ReactNode } from "react"
import { Provider } from "react-redux"
import { MemoryRouter, useLocation } from "react-router-dom"

import { default as configureStore } from "redux-mock-store"

import { describe, it, expect, jest, beforeEach } from "@jest/globals"
import "@testing-library/jest-dom"
import { render, act, screen } from "@testing-library/react"

import Layout from "components/Layout"
import { AppDispatch } from "store/store"
import { removeToken } from "utils/auth/AuthUtils"

const mockRefreshTokenApi = jest.fn<() => Promise<{ access_token: string }>>()

jest.mock("api/auth/Auth", () => ({
  refreshTokenApi: () => mockRefreshTokenApi(),
}))
jest.mock("api/workspace")

jest.mock("components/Layout/Header", () => ({
  __esModule: true,
  default: () => null,
}))
jest.mock("components/Layout/LeftMenu", () => ({
  __esModule: true,
  default: () => null,
}))
jest.mock("components/common/LimitAlert", () => ({
  __esModule: true,
  default: () => null,
}))
jest.mock("components/common/LogsFloatingButton", () => ({
  LogsFloatingButton: () => null,
}))
jest.mock("components/Premium/InactivityWarning", () => ({
  __esModule: true,
  default: () => null,
}))
jest.mock("components/Premium/PremiumAssignmentManager", () => ({
  __esModule: true,
  default: () => null,
}))
jest.mock("components/Premium/PremiumNotificationManager", () => ({
  __esModule: true,
  default: () => null,
}))
jest.mock("contexts/PremiumAssignmentContext", () => ({
  PremiumAssignmentProvider: ({ children }: { children: ReactNode }) =>
    children,
}))

const mockStore = configureStore<Record<string, unknown>, AppDispatch>([])

const PathProbe = () => <div data-testid="path">{useLocation().pathname}</div>

const mountAt = async (path: string) => {
  const store = mockStore({
    user: { currentUser: null },
    mode: { mode: false, loading: false },
    logsModal: { isOpen: false },
  })
  store.dispatch = jest.fn(() => Promise.resolve({})) as never
  await act(async () => {
    render(
      <Provider store={store}>
        <MemoryRouter initialEntries={[path]}>
          <Layout>
            <div>content</div>
          </Layout>
          <PathProbe />
        </MemoryRouter>
      </Provider>,
    )
  })
}

describe("Layout on a reload", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    sessionStorage.clear()
    removeToken()
    localStorage.clear()
  })

  it("stays on the protected page when the cookie restores the session", async () => {
    mockRefreshTokenApi.mockResolvedValue({ access_token: "restored" })

    await mountAt("/dashboard")

    expect(mockRefreshTokenApi).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("path")).toHaveTextContent("/dashboard")
  })

  it("sends the user to login when there is no session", async () => {
    mockRefreshTokenApi.mockRejectedValue({ response: { status: 401 } })

    await mountAt("/dashboard")

    expect(screen.getByTestId("path")).toHaveTextContent("/login")
  })
})

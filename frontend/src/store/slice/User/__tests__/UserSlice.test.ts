import { describe, it, expect, beforeEach, jest } from "@jest/globals"

import type { User } from "store/slice/User/UserType"
import type { RootState } from "store/store"

/**
 * Tests for UserSlice
 *
 * These tests verify:
 * - logoutGeneration is initialized to 0
 * - logoutGeneration increments on logout
 * - logoutGeneration is preserved through logout (other state reset)
 */

// Mock dependencies
jest.mock("utils/axios", () => ({
  setLoggingOut: jest.fn(),
}))

jest.mock("utils/auth/AuthUtils", () => ({
  removeToken: jest.fn(),
  removeRefreshToken: jest.fn(),
  removeExToken: jest.fn(),
  saveToken: jest.fn(),
  saveRefreshToken: jest.fn(),
  saveExToken: jest.fn(),
}))

jest.mock("utils/routing/RoutingService", () => ({
  routingService: {
    clearRoutingInfo: jest.fn(),
    updateRoutingInfo: jest.fn(),
  },
}))

// Mock localStorage
const localStorageMock = {
  removeItem: jest.fn(),
  getItem: jest.fn(),
  setItem: jest.fn(),
  clear: jest.fn(),
  length: 0,
  key: jest.fn(),
}
Object.defineProperty(global, "localStorage", { value: localStorageMock })

// Mock sessionStorage
const sessionStorageMock = {
  removeItem: jest.fn(),
  getItem: jest.fn(),
  setItem: jest.fn(),
  clear: jest.fn(),
  length: 0,
  key: jest.fn(),
}
Object.defineProperty(global, "sessionStorage", { value: sessionStorageMock })

describe("UserSlice", () => {
  let userSlice: typeof import("store/slice/User/UserSlice").userSlice
  let logout: typeof import("store/slice/User/UserSlice").logout

  beforeEach(async () => {
    jest.clearAllMocks()
    jest.resetModules()

    // Re-import to get fresh state
    const module = await import("store/slice/User/UserSlice")
    userSlice = module.userSlice
    logout = module.logout
  })

  describe("initialState", () => {
    it("should have logoutGeneration initialized to 0", () => {
      const state = userSlice.reducer(undefined, { type: "@@INIT" })
      expect(state.logoutGeneration).toBe(0)
    })

    it("should have currentUser as undefined", () => {
      const state = userSlice.reducer(undefined, { type: "@@INIT" })
      expect(state.currentUser).toBeUndefined()
    })

    it("should have loading as false", () => {
      const state = userSlice.reducer(undefined, { type: "@@INIT" })
      expect(state.loading).toBe(false)
    })
  })

  describe("logout action", () => {
    it("should increment logoutGeneration", () => {
      const initialState = userSlice.reducer(undefined, { type: "@@INIT" })
      expect(initialState.logoutGeneration).toBe(0)

      const stateAfterLogout = userSlice.reducer(initialState, logout())
      expect(stateAfterLogout.logoutGeneration).toBe(1)
    })

    it("should increment logoutGeneration on each logout", () => {
      let state = userSlice.reducer(undefined, { type: "@@INIT" })
      expect(state.logoutGeneration).toBe(0)

      state = userSlice.reducer(state, logout())
      expect(state.logoutGeneration).toBe(1)

      state = userSlice.reducer(state, logout())
      expect(state.logoutGeneration).toBe(2)

      state = userSlice.reducer(state, logout())
      expect(state.logoutGeneration).toBe(3)
    })

    it("should reset currentUser to undefined", () => {
      const stateWithUser: User = {
        currentUser: {
          id: 1,
          uid: "test",
          email: "test@example.com",
        } as User["currentUser"],
        listUserSearch: undefined,
        listUser: undefined,
        loading: false,
        logoutGeneration: 5,
      }

      const stateAfterLogout = userSlice.reducer(stateWithUser, logout())
      expect(stateAfterLogout.currentUser).toBeUndefined()
    })

    it("should reset loading to false", () => {
      const stateWithLoading: User = {
        currentUser: undefined,
        listUserSearch: undefined,
        listUser: undefined,
        loading: true,
        logoutGeneration: 0,
      }

      const stateAfterLogout = userSlice.reducer(stateWithLoading, logout())
      expect(stateAfterLogout.loading).toBe(false)
    })

    it("should call setLoggingOut(true)", async () => {
      const axiosModule = await import("utils/axios")
      const state = userSlice.reducer(undefined, { type: "@@INIT" })

      userSlice.reducer(state, logout())

      expect(axiosModule.setLoggingOut).toHaveBeenCalledWith(true)
    })

    it("should clear localStorage dismissedAlerts", () => {
      const state = userSlice.reducer(undefined, { type: "@@INIT" })

      userSlice.reducer(state, logout())

      expect(localStorageMock.removeItem).toHaveBeenCalledWith(
        "dismissedAlerts",
      )
    })

    it("should clear sessionStorage storage-refreshed-on-login", () => {
      const state = userSlice.reducer(undefined, { type: "@@INIT" })

      userSlice.reducer(state, logout())

      expect(sessionStorageMock.removeItem).toHaveBeenCalledWith(
        "storage-refreshed-on-login",
      )
    })
  })
})

describe("selectLogoutGeneration", () => {
  it("should select logoutGeneration from state", async () => {
    const { selectLogoutGeneration } = await import(
      "store/slice/User/UserSelector"
    )

    const mockState: Pick<RootState, "user"> = {
      user: {
        currentUser: undefined,
        listUserSearch: undefined,
        listUser: undefined,
        loading: false,
        logoutGeneration: 42,
      },
    }

    const result = selectLogoutGeneration(mockState as RootState)
    expect(result).toBe(42)
  })
})

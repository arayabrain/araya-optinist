import { describe, it, expect, beforeEach, jest } from "@jest/globals"

import type { User } from "store/slice/User/UserType"
import type { RootState } from "store/store"

/**
 * Tests for UserSlice
 *
 * These tests verify:
 * - Initial state values
 * - Logout action behavior
 * - getMe.rejected does not clear tokens (prevents forced logout on transient errors)
 */

// Mock dependencies - use mock prefix for hoisting compatibility
const mockSetLoggingOut = jest.fn()
const mockRemoveToken = jest.fn()
const mockRemoveRefreshToken = jest.fn()
const mockRemoveExToken = jest.fn()
const mockSaveToken = jest.fn()
const mockSaveRefreshToken = jest.fn()
const mockSaveExToken = jest.fn()
const mockClearRoutingInfo = jest.fn()
const mockUpdateRoutingInfo = jest.fn()

jest.mock("utils/axios", () => ({
  setLoggingOut: mockSetLoggingOut,
}))

jest.mock("utils/auth/AuthUtils", () => ({
  removeToken: mockRemoveToken,
  removeRefreshToken: mockRemoveRefreshToken,
  removeExToken: mockRemoveExToken,
  saveToken: mockSaveToken,
  saveRefreshToken: mockSaveRefreshToken,
  saveExToken: mockSaveExToken,
}))

jest.mock("utils/routing/RoutingService", () => ({
  routingService: {
    clearRoutingInfo: mockClearRoutingInfo,
    updateRoutingInfo: mockUpdateRoutingInfo,
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
      }

      const stateAfterLogout = userSlice.reducer(stateWithLoading, logout())
      expect(stateAfterLogout.loading).toBe(false)
    })

    it("should call setLoggingOut(true)", () => {
      const state = userSlice.reducer(undefined, { type: "@@INIT" })

      userSlice.reducer(state, logout())

      expect(mockSetLoggingOut).toHaveBeenCalledWith(true)
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

  describe("getMe.rejected", () => {
    it("should clear currentUser but not clear tokens", () => {
      const stateWithUser: User = {
        currentUser: {
          id: 1,
          uid: "test",
          email: "test@example.com",
        } as User["currentUser"],
        listUserSearch: undefined,
        listUser: undefined,
        loading: true,
      }

      const getMeRejectedAction = {
        type: "user/getMe/rejected",
        error: { message: "Network Error" },
      }

      const state = userSlice.reducer(stateWithUser, getMeRejectedAction)

      // Should clear currentUser and set loading to false
      expect(state.currentUser).toBeUndefined()
      expect(state.loading).toBe(false)
      // Should NOT call token removal functions
      expect(mockRemoveToken).not.toHaveBeenCalled()
      expect(mockRemoveRefreshToken).not.toHaveBeenCalled()
      expect(mockRemoveExToken).not.toHaveBeenCalled()
      // Should NOT clear routing info
      expect(mockClearRoutingInfo).not.toHaveBeenCalled()
    })
  })
})

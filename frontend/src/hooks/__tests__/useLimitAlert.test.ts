import React from "react"

import { SnackbarProvider, useSnackbar } from "notistack"

import { renderHook, act, waitFor } from "@testing-library/react"

import * as StorageAlerts from "api/storage/StorageAlerts"
import { LimitAlertType } from "const/Subscription"
import {
  useLimitAlert,
  useLimitAlertStatus,
  AlertContext,
  determineAlertContext,
  isPremiumUserAlert,
  requiresImmediateAction,
} from "hooks/useLimitAlert"

// Mock the API
jest.mock("../../api/storage/StorageAlerts")
const mockGetMyLimitAlertApi = StorageAlerts.getMyLimitAlertApi as jest.Mock
const mockCheckLimitAlertStatusApi =
  StorageAlerts.checkLimitAlertStatusApi as jest.Mock

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: jest.fn((key: string) => store[key] || null),
    setItem: jest.fn((key: string, value: string) => {
      store[key] = value
    }),
    removeItem: jest.fn((key: string) => {
      delete store[key]
    }),
    clear: jest.fn(() => {
      store = {}
    }),
  }
})()
Object.defineProperty(window, "localStorage", { value: localStorageMock })

// Wrapper with SnackbarProvider
const wrapper = ({ children }: { children: React.ReactNode }) =>
  React.createElement(SnackbarProvider, { maxSnack: 5 }, children)

describe("useLimitAlert", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    localStorageMock.clear()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  describe("basic functionality", () => {
    it("should initialize with default values", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue({ has_alert: false })

      const { result } = renderHook(() => useLimitAlert({ autoCheck: false }), {
        wrapper,
      })

      expect(result.current.alert).toBeNull()
      expect(result.current.hasAlert).toBe(false)
      expect(result.current.loading).toBe(false)
      expect(result.current.error).toBeNull()
      expect(result.current.consecutiveFailures).toBe(0)
    })

    it("should fetch alert on mount when autoCheck is true", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue({
        has_alert: true,
        alert_type: LimitAlertType.STORAGE,
        days_remaining: 5,
        excess_data_gb: 1.5,
      })

      const { result } = renderHook(() => useLimitAlert({ autoCheck: true }), {
        wrapper,
      })

      await waitFor(() => {
        expect(result.current.hasAlert).toBe(true)
      })

      expect(mockGetMyLimitAlertApi).toHaveBeenCalled()
    })

    it("should return correct alertContext", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue({
        has_alert: true,
        alert_type: LimitAlertType.GRACE,
        days_remaining: 10,
        excess_data_gb: 0,
        subscription_end_date: "2024-01-01",
        grace_end_date: "2024-01-15",
      })

      const { result } = renderHook(() => useLimitAlert({ autoCheck: true }), {
        wrapper,
      })

      await waitFor(() => {
        expect(result.current.alertContext).toBe(
          AlertContext.PREMIUM_GRACE_PERIOD,
        )
      })
    })
  })

  describe("error handling (Case 30)", () => {
    it("should set error state on API failure", async () => {
      const testError = new Error("Network error")
      mockGetMyLimitAlertApi.mockRejectedValue(testError)

      const { result } = renderHook(() => useLimitAlert({ autoCheck: false }), {
        wrapper,
      })

      await act(async () => {
        await result.current.checkLimitAlert()
      })

      expect(result.current.error).toEqual(testError)
      expect(result.current.consecutiveFailures).toBe(1)
    })

    it("should increment consecutiveFailures on each failure", async () => {
      mockGetMyLimitAlertApi.mockRejectedValue(new Error("Network error"))

      const { result } = renderHook(() => useLimitAlert({ autoCheck: false }), {
        wrapper,
      })

      await act(async () => {
        await result.current.checkLimitAlert()
      })
      expect(result.current.consecutiveFailures).toBe(1)

      await act(async () => {
        await result.current.checkLimitAlert()
      })
      expect(result.current.consecutiveFailures).toBe(2)

      await act(async () => {
        await result.current.checkLimitAlert()
      })
      expect(result.current.consecutiveFailures).toBe(3)
    })

    it("should reset error state on successful fetch", async () => {
      // First fail
      mockGetMyLimitAlertApi.mockRejectedValueOnce(new Error("Network error"))

      const { result } = renderHook(() => useLimitAlert({ autoCheck: false }), {
        wrapper,
      })

      await act(async () => {
        await result.current.checkLimitAlert()
      })
      expect(result.current.error).not.toBeNull()
      expect(result.current.consecutiveFailures).toBe(1)

      // Then succeed
      mockGetMyLimitAlertApi.mockResolvedValueOnce({ has_alert: false })

      await act(async () => {
        await result.current.checkLimitAlert()
      })

      expect(result.current.error).toBeNull()
      expect(result.current.consecutiveFailures).toBe(0)
    })

    it("should reset consecutiveFailures when retryNow is called", async () => {
      mockGetMyLimitAlertApi
        .mockRejectedValueOnce(new Error("Error 1"))
        .mockRejectedValueOnce(new Error("Error 2"))
        .mockResolvedValueOnce({ has_alert: false })

      const { result } = renderHook(() => useLimitAlert({ autoCheck: false }), {
        wrapper,
      })

      // Accumulate failures
      await act(async () => {
        await result.current.checkLimitAlert()
      })
      await act(async () => {
        await result.current.checkLimitAlert()
      })
      expect(result.current.consecutiveFailures).toBe(2)

      // Retry - should reset counter and fetch again
      await act(async () => {
        result.current.retryNow()
      })

      // Wait for the retryNow to complete its async checkLimitAlert call
      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.consecutiveFailures).toBe(0)
      expect(result.current.error).toBeNull()
    })

    it("should handle non-Error exceptions", async () => {
      mockGetMyLimitAlertApi.mockRejectedValue("String error")

      const { result } = renderHook(() => useLimitAlert({ autoCheck: false }), {
        wrapper,
      })

      await act(async () => {
        await result.current.checkLimitAlert()
      })

      expect(result.current.error).toBeInstanceOf(Error)
      expect(result.current.error?.message).toBe("String error")
    })
  })

  describe("dismissal", () => {
    it("should persist dismissal to localStorage", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue({
        has_alert: true,
        alert_type: LimitAlertType.STORAGE,
      })

      const { result } = renderHook(() => useLimitAlert({ autoCheck: true }), {
        wrapper,
      })

      await waitFor(() => {
        expect(result.current.hasAlert).toBe(true)
      })

      act(() => {
        result.current.dismissAlert()
      })

      expect(result.current.hasAlert).toBe(false)
      expect(localStorageMock.setItem).toHaveBeenCalledWith(
        "dismissedAlerts",
        expect.stringContaining("\"limitAlert\":true"),
      )
    })

    it("should not return alert when dismissed", async () => {
      localStorageMock.getItem.mockReturnValue(
        JSON.stringify({ limitAlert: true }),
      )

      mockGetMyLimitAlertApi.mockResolvedValue({
        has_alert: true,
        alert_type: LimitAlertType.STORAGE,
      })

      const { result } = renderHook(() => useLimitAlert({ autoCheck: true }), {
        wrapper,
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.alert).toBeNull()
      expect(result.current.hasAlert).toBe(false)
    })
  })
})

describe("useLimitAlertStatus", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("should fetch status on mount", async () => {
    mockCheckLimitAlertStatusApi.mockResolvedValue({ has_alert: true })

    const { result } = renderHook(() => useLimitAlertStatus(), { wrapper })

    await waitFor(() => {
      expect(result.current.hasAlert).toBe(true)
    })

    expect(mockCheckLimitAlertStatusApi).toHaveBeenCalled()
  })

  it("should handle API errors gracefully", async () => {
    mockCheckLimitAlertStatusApi.mockRejectedValue(new Error("API error"))

    const { result } = renderHook(() => useLimitAlertStatus(), { wrapper })

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    // Should not throw, just return default state
    expect(result.current.hasAlert).toBe(false)
  })
})

describe("determineAlertContext", () => {
  it("should return NONE for null alert", () => {
    expect(determineAlertContext(null)).toBe(AlertContext.NONE)
  })

  it("should return NONE for alert without has_alert", () => {
    expect(
      determineAlertContext({ has_alert: false } as StorageAlerts.LimitAlert),
    ).toBe(AlertContext.NONE)
  })

  it("should return FREE_USER_STORAGE_EXCEEDED for storage alert with deletion_date", () => {
    const alert: StorageAlerts.LimitAlert = {
      has_alert: true,
      alert_type: LimitAlertType.STORAGE,
      days_remaining: 5,
      excess_data_bytes: 1000000,
      excess_data_gb: 1,
      storage_usage_bytes: 5000000000,
      storage_usage_gb: 5,
      storage_quota_bytes: 4000000000,
      storage_quota_gb: 4,
      deletion_date: "2024-01-15",
      message: "Storage exceeded",
    }
    expect(determineAlertContext(alert)).toBe(
      AlertContext.FREE_USER_STORAGE_EXCEEDED,
    )
  })

  it("should return PREMIUM_STORAGE_EXCEEDED for storage alert with subscription_end_date", () => {
    const alert: StorageAlerts.LimitAlert = {
      has_alert: true,
      alert_type: LimitAlertType.STORAGE,
      days_remaining: 5,
      excess_data_bytes: 1000000,
      excess_data_gb: 1,
      storage_usage_bytes: 5000000000,
      storage_usage_gb: 5,
      storage_quota_bytes: 4000000000,
      storage_quota_gb: 4,
      subscription_end_date: "2024-01-15",
      message: "Storage exceeded",
    }
    expect(determineAlertContext(alert)).toBe(
      AlertContext.PREMIUM_STORAGE_EXCEEDED,
    )
  })

  it("should return PREMIUM_GRACE_PERIOD for grace alert", () => {
    const alert: StorageAlerts.LimitAlert = {
      has_alert: true,
      alert_type: LimitAlertType.GRACE,
      days_remaining: 10,
      excess_data_bytes: 0,
      excess_data_gb: 0,
      storage_usage_bytes: 3000000000,
      storage_usage_gb: 3,
      storage_quota_bytes: 4000000000,
      storage_quota_gb: 4,
      subscription_end_date: "2024-01-01",
      grace_end_date: "2024-01-15",
      message: "Grace period",
    }
    expect(determineAlertContext(alert)).toBe(AlertContext.PREMIUM_GRACE_PERIOD)
  })

  it("should return PREMIUM_OVERDUE for overdue alert", () => {
    const alert: StorageAlerts.LimitAlert = {
      has_alert: true,
      alert_type: LimitAlertType.OVERDUE,
      days_remaining: 0,
      excess_data_bytes: 1000000,
      excess_data_gb: 1,
      storage_usage_bytes: 5000000000,
      storage_usage_gb: 5,
      storage_quota_bytes: 4000000000,
      storage_quota_gb: 4,
      message: "Overdue",
    }
    expect(determineAlertContext(alert)).toBe(AlertContext.PREMIUM_OVERDUE)
  })
})

describe("isPremiumUserAlert", () => {
  it("should return true for premium contexts", () => {
    expect(isPremiumUserAlert(AlertContext.PREMIUM_STORAGE_EXCEEDED)).toBe(true)
    expect(isPremiumUserAlert(AlertContext.PREMIUM_GRACE_PERIOD)).toBe(true)
    expect(isPremiumUserAlert(AlertContext.PREMIUM_OVERDUE)).toBe(true)
  })

  it("should return false for non-premium contexts", () => {
    expect(isPremiumUserAlert(AlertContext.NONE)).toBe(false)
    expect(isPremiumUserAlert(AlertContext.FREE_USER_STORAGE_EXCEEDED)).toBe(
      false,
    )
  })
})

describe("requiresImmediateAction", () => {
  it("should return true for overdue context", () => {
    const alert: StorageAlerts.LimitAlert = {
      has_alert: true,
      alert_type: LimitAlertType.OVERDUE,
      days_remaining: 0,
      excess_data_bytes: 0,
      excess_data_gb: 0,
      storage_usage_bytes: 0,
      storage_usage_gb: 0,
      storage_quota_bytes: 0,
      storage_quota_gb: 0,
      message: "Overdue",
    }
    expect(requiresImmediateAction(alert, AlertContext.PREMIUM_OVERDUE)).toBe(
      true,
    )
  })

  it("should return true for alerts with 7 or fewer days remaining", () => {
    const alert: StorageAlerts.LimitAlert = {
      has_alert: true,
      alert_type: LimitAlertType.STORAGE,
      days_remaining: 7,
      excess_data_bytes: 1000,
      excess_data_gb: 0.001,
      storage_usage_bytes: 1000,
      storage_usage_gb: 0.001,
      storage_quota_bytes: 500,
      storage_quota_gb: 0.0005,
      message: "Warning",
    }
    expect(
      requiresImmediateAction(alert, AlertContext.FREE_USER_STORAGE_EXCEEDED),
    ).toBe(true)
  })

  it("should return false for alerts with more than 7 days remaining", () => {
    const alert: StorageAlerts.LimitAlert = {
      has_alert: true,
      alert_type: LimitAlertType.STORAGE,
      days_remaining: 14,
      excess_data_bytes: 1000,
      excess_data_gb: 0.001,
      storage_usage_bytes: 1000,
      storage_usage_gb: 0.001,
      storage_quota_bytes: 500,
      storage_quota_gb: 0.0005,
      message: "Warning",
    }
    expect(
      requiresImmediateAction(alert, AlertContext.FREE_USER_STORAGE_EXCEEDED),
    ).toBe(false)
  })

  it("should return false for NONE context", () => {
    expect(requiresImmediateAction(null, AlertContext.NONE)).toBe(false)
  })
})

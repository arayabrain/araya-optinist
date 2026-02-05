import { MemoryRouter } from "react-router-dom"

import { SnackbarProvider } from "notistack"

import {
  describe,
  it,
  expect,
  beforeEach,
  jest,
  afterEach,
} from "@jest/globals"
import { render, screen, act, waitFor } from "@testing-library/react"

import * as StorageAlertsApi from "api/storage/StorageAlerts"
import LimitAlert from "components/common/LimitAlert"
import { LimitAlertType } from "const/Subscription"


// Mock the API module
jest.mock("api/storage/StorageAlerts")

// Mock auth utils
jest.mock("utils/auth/AuthUtils", () => ({
  getToken: jest.fn(() => "mock-token"),
}))

const mockGetMyLimitAlertApi =
  StorageAlertsApi.getMyLimitAlertApi as jest.MockedFunction<
    typeof StorageAlertsApi.getMyLimitAlertApi
  >

const createMockAlert = (
  overrides: Partial<StorageAlertsApi.LimitAlert> = {},
): StorageAlertsApi.LimitAlert => ({
  has_alert: true,
  alert_type: LimitAlertType.GRACE,
  message: "Your subscription has expired",
  days_remaining: 7,
  deletion_date: "2026-02-15",
  storage_usage_bytes: 5905580032,
  storage_usage_gb: 5.5,
  storage_quota_bytes: 3221225472,
  storage_quota_gb: 3.0,
  excess_data_bytes: 2684354560,
  excess_data_gb: 2.5,
  ...overrides,
})

const renderLimitAlert = (props = {}) => {
  return render(
    <MemoryRouter>
      <SnackbarProvider>
        <LimitAlert {...props} />
      </SnackbarProvider>
    </MemoryRouter>,
  )
}

describe("LimitAlert", () => {
  let mockStorage: Record<string, string>

  beforeEach(() => {
    mockStorage = {}

    const localStorageMock = {
      getItem: (key: string) => mockStorage[key] || null,
      setItem: (key: string, value: string) => {
        mockStorage[key] = value
      },
      removeItem: (key: string) => {
        delete mockStorage[key]
      },
      clear: () => {
        mockStorage = {}
      },
      length: 0,
      key: () => null,
    }

    Object.defineProperty(window, "localStorage", {
      value: localStorageMock,
      writable: true,
    })

    jest.clearAllMocks()
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  describe("Cross-Tab Alert Dismissal (Case 31)", () => {
    it("should sync dismissal from other tabs via storage event", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(createMockAlert())

      renderLimitAlert()

      await waitFor(() => {
        expect(screen.getByText("Your subscription has expired")).toBeTruthy()
      })

      // Simulate dismissal from another tab
      act(() => {
        const event = new StorageEvent("storage", {
          key: "dismissedAlerts",
          newValue: JSON.stringify({ limitAlert: true }),
        })
        window.dispatchEvent(event)
      })

      // Alert should be dismissed
      await waitFor(() => {
        expect(screen.queryByText("Your subscription has expired")).toBeFalsy()
      })
    })

    it("should ignore storage events for other keys", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(createMockAlert())

      renderLimitAlert()

      await waitFor(() => {
        expect(screen.getByText("Your subscription has expired")).toBeTruthy()
      })

      // Simulate storage event for different key
      act(() => {
        const event = new StorageEvent("storage", {
          key: "someOtherKey",
          newValue: JSON.stringify({ limitAlert: true }),
        })
        window.dispatchEvent(event)
      })

      // Alert should still be visible
      expect(screen.getByText("Your subscription has expired")).toBeTruthy()
    })

    it("should handle invalid JSON in storage event gracefully", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(createMockAlert())

      renderLimitAlert()

      await waitFor(() => {
        expect(screen.getByText("Your subscription has expired")).toBeTruthy()
      })

      // Simulate storage event with invalid JSON
      act(() => {
        const event = new StorageEvent("storage", {
          key: "dismissedAlerts",
          newValue: "invalid-json",
        })
        window.dispatchEvent(event)
      })

      // Alert should still be visible (error handled gracefully)
      expect(screen.getByText("Your subscription has expired")).toBeTruthy()
    })
  })

  describe("Negative Days Remaining (Case 35)", () => {
    it("should show progress bar at 0% for negative days", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(
        createMockAlert({ days_remaining: -5 }),
      )

      renderLimitAlert()

      await waitFor(() => {
        expect(screen.getByRole("progressbar")).toBeTruthy()
      })

      const progressBar = screen.getByRole("progressbar")
      expect(progressBar.getAttribute("aria-valuenow")).toBe("0")
    })

    it("should show progress bar at 0% for zero days", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(
        createMockAlert({ days_remaining: 0 }),
      )

      renderLimitAlert()

      await waitFor(() => {
        expect(screen.getByRole("progressbar")).toBeTruthy()
      })

      const progressBar = screen.getByRole("progressbar")
      expect(progressBar.getAttribute("aria-valuenow")).toBe("0")
    })

    it("should display 0 days for negative values", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(
        createMockAlert({ days_remaining: -3 }),
      )

      renderLimitAlert()

      await waitFor(() => {
        expect(screen.getByText("0 days")).toBeTruthy()
      })
    })

    it("should use error color for zero or negative days", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(
        createMockAlert({ days_remaining: 0 }),
      )

      const { container } = renderLimitAlert()

      await waitFor(() => {
        expect(screen.getByRole("progressbar")).toBeTruthy()
      })

      // MUI uses class names for colors
      const progressBar = container.querySelector(".MuiLinearProgress-root")
      expect(progressBar?.className).toContain("colorError")
    })

    it("should show progress bar when days_remaining is undefined", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(
        createMockAlert({ days_remaining: undefined as unknown as number }),
      )

      renderLimitAlert()

      await waitFor(() => {
        // Progress bar should not be shown for undefined
        expect(screen.queryByRole("progressbar")).toBeFalsy()
      })
    })
  })

  describe("Progress Bar Color Thresholds", () => {
    it("should show error color for CRITICAL days (<=0)", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(
        createMockAlert({ days_remaining: 0 }),
      )

      const { container } = renderLimitAlert()

      await waitFor(() => {
        const progressBar = container.querySelector(".MuiLinearProgress-root")
        expect(progressBar?.className).toContain("colorError")
      })
    })

    it("should show warning color for WARNING days (8-14)", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(
        createMockAlert({ days_remaining: 10 }),
      )

      const { container } = renderLimitAlert()

      await waitFor(() => {
        const progressBar = container.querySelector(".MuiLinearProgress-root")
        expect(progressBar?.className).toContain("colorWarning")
      })
    })

    it("should show primary color for safe days (>14)", async () => {
      mockGetMyLimitAlertApi.mockResolvedValue(
        createMockAlert({ days_remaining: 20 }),
      )

      const { container } = renderLimitAlert()

      await waitFor(() => {
        const progressBar = container.querySelector(".MuiLinearProgress-root")
        expect(progressBar?.className).toContain("colorPrimary")
      })
    })
  })
})

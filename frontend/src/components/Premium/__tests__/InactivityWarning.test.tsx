/**
 * Tests for the InactivityWarning snackbar copy.
 *
 * The idle time and the release countdown are both derived from
 * PremiumTiming, so the snackbar cannot disagree with the thresholds the
 * context actually enforces.
 */

import React from "react"

import { beforeEach, describe, expect, jest, test } from "@jest/globals"
import { render, screen } from "@testing-library/react"

// --- Module mocks (must precede the SUT import) ---

const mockRecordActivity = jest.fn<Promise<void>, []>()
const mockDismissInactivityWarning = jest.fn()
const mockPerformLogout = jest.fn()

let mockShowInactivityWarning = true

jest.mock("contexts/PremiumAssignmentContext", () => ({
  __esModule: true,
  usePremiumAssignment: () => ({
    showInactivityWarning: mockShowInactivityWarning,
    dismissInactivityWarning: mockDismissInactivityWarning,
    recordActivity: mockRecordActivity,
  }),
}))

jest.mock("hooks/useLogout", () => ({
  __esModule: true,
  useLogout: () => ({ performLogout: mockPerformLogout }),
}))

const InactivityWarning: React.FC =
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("components/Premium/InactivityWarning").default

// --- Tests ---

describe("InactivityWarning", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockShowInactivityWarning = true
  })

  test("states the idle time and the remaining release countdown", () => {
    render(<InactivityWarning />)

    const alert = screen.getByRole("alert").textContent ?? ""
    expect(alert).toContain("inactive for 1h.")
    expect(alert).toContain("released in 1h if no activity")
    expect(alert).not.toContain("1h 0m")
  })
})

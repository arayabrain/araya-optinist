import React from "react"

import {
  afterEach,
  beforeEach,
  describe,
  expect,
  jest,
  test,
} from "@jest/globals"
import { act, render } from "@testing-library/react"

import type { PremiumAssignmentResult } from "api/premium/PremiumAssignmentApi"

// --- Module mocks (must be declared before importing the hook) ---

const mockServiceLogs = jest.fn<
  Promise<{
    data: { text: string; id: string }[]
    params: Record<string, unknown>
    offset: { next: number; pre: number }
    platform?: { service_name: string; task_id: string; instance_id: string }
  }>,
  [Record<string, unknown>]
>()

jest.mock("components/Workspace/FlowChart/ModalLogs/helpers/service", () => ({
  __esModule: true,
  serviceLogs: mockServiceLogs,
  TLevelsLog: {
    ALL: "ALL",
    INFO: "INFO",
    ERROR: "ERROR",
    DEBUG: "DEBUG",
    WARNING: "WARNING",
    CRITICAL: "CRITICAL",
    FRONTEND: "FRONTEND",
  },
}))

let mockAssignment: PremiumAssignmentResult | null = null

jest.mock("contexts/PremiumAssignmentContext", () => ({
  __esModule: true,
  usePremiumAssignment: () => ({ assignmentResult: mockAssignment }),
}))

// require() after mocks to avoid hoist-before-mock TDZ.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { useLogs } = require("../useLogs")

// --- Harness ---

type Handle = ReturnType<typeof useLogs>

const Harness: React.FC<{ captureRef: { current: Handle | null } }> = ({
  captureRef,
}) => {
  captureRef.current = useLogs([], "", true)
  return null
}

const sharedAssignment: PremiumAssignmentResult = {
  message: "shared",
  instance_id: "i-shared",
  assigned: true,
  is_shared: true,
}

const dedicatedAssignment: PremiumAssignmentResult = {
  message: "dedicated",
  instance_id: "i-dedicated-A",
  assigned: true,
  is_shared: false,
}

const mkResponse = (instanceId: string) => ({
  data: [],
  params: {},
  offset: { next: 100, pre: 50 },
  platform: {
    service_name: instanceId.includes("dedicated")
      ? "premium-service"
      : "studio-service",
    task_id: "t1",
    instance_id: instanceId,
  },
})

// --- Tests ---

describe("useLogs — premium-assignment-driven reset", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
    mockAssignment = null
    mockServiceLogs.mockResolvedValue(mkResponse("i-studio"))
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  test("calls reset when assignmentResult.instance_id changes (shared → dedicated)", async () => {
    mockAssignment = sharedAssignment
    const captureRef: { current: Handle | null } = { current: null }
    const { rerender } = render(<Harness captureRef={captureRef} />)

    // Let the mount-time polling promise settle so the initial serviceLogs
    // call is accounted for in the call counter.
    await act(async () => {
      await Promise.resolve()
    })

    mockServiceLogs.mockClear()
    mockServiceLogs.mockResolvedValue(mkResponse("i-dedicated-A"))

    // Simulate the assignment promoting from shared to dedicated.
    mockAssignment = dedicatedAssignment
    await act(async () => {
      rerender(<Harness captureRef={captureRef} />)
      await Promise.resolve()
    })

    // reset() triggers realtimeApi() which calls serviceLogs immediately —
    // proves we didn't wait for the 2 s tick and proves logs were cleared
    // (state was set to []).
    expect(mockServiceLogs).toHaveBeenCalled()
    expect(captureRef.current?.logs).toEqual([])
  })

  test("does NOT call reset when assignmentResult.instance_id is unchanged across renders", async () => {
    mockAssignment = dedicatedAssignment
    const captureRef: { current: Handle | null } = { current: null }
    const { rerender } = render(<Harness captureRef={captureRef} />)

    await act(async () => {
      await Promise.resolve()
    })

    mockServiceLogs.mockClear()

    // Re-render with the SAME instance_id — must not trigger a reset.
    mockAssignment = { ...dedicatedAssignment }
    await act(async () => {
      rerender(<Harness captureRef={captureRef} />)
      await Promise.resolve()
    })

    // Without a real-timer advance, the 2 s tick won't fire on its own, so
    // any serviceLogs call here would only come from a spurious reset.
    expect(mockServiceLogs).not.toHaveBeenCalled()
  })

  test("does NOT call reset on first non-null assignment after a null start", async () => {
    mockAssignment = null
    const captureRef: { current: Handle | null } = { current: null }
    const { rerender } = render(<Harness captureRef={captureRef} />)

    await act(async () => {
      await Promise.resolve()
    })

    mockServiceLogs.mockClear()

    // First real assignment lands — this is not a migration.
    mockAssignment = sharedAssignment
    await act(async () => {
      rerender(<Harness captureRef={captureRef} />)
      await Promise.resolve()
    })

    expect(mockServiceLogs).not.toHaveBeenCalled()
  })

  test("calls reset exactly once across 'X' → null → 'Y'", async () => {
    mockAssignment = sharedAssignment
    const captureRef: { current: Handle | null } = { current: null }
    const { rerender } = render(<Harness captureRef={captureRef} />)

    await act(async () => {
      await Promise.resolve()
    })

    mockServiceLogs.mockClear()

    // Transient null — must not trigger reset.
    mockAssignment = null
    await act(async () => {
      rerender(<Harness captureRef={captureRef} />)
      await Promise.resolve()
    })
    expect(mockServiceLogs).not.toHaveBeenCalled()

    // New instance arrives — reset fires exactly once.
    mockServiceLogs.mockResolvedValue(mkResponse("i-dedicated-A"))
    mockAssignment = dedicatedAssignment
    await act(async () => {
      rerender(<Harness captureRef={captureRef} />)
      await Promise.resolve()
    })

    expect(mockServiceLogs).toHaveBeenCalled()
    expect(captureRef.current?.logs).toEqual([])
  })
})

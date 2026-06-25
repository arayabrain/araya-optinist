/**
 * Tests for the re-trigger assign logic during polling.
 *
 * Covers:
 *  1. Re-trigger fires at correct interval (every ASSIGN_RETRY_POLL_THRESHOLD polls).
 *  2. Re-trigger success transitions state correctly.
 *  3. Re-trigger failure continues polling without crash.
 *  4. Normal assign (assigned: true) does NOT fire re-trigger (regression test).
 *  5. MAX_POLL_ATTEMPTS resets flags for retryable cases.
 *  6. User gesture recovery after MAX_POLL_ATTEMPTS exhaustion.
 */

import React from "react"

import { SnackbarProvider } from "notistack"

import {
  afterEach,
  beforeEach,
  describe,
  expect,
  jest,
  test,
} from "@jest/globals"
import { act, render, waitFor } from "@testing-library/react"

import type {
  PremiumAssignmentResult,
  PremiumReleaseResult,
  PremiumStatusResult,
  PremiumHeartbeatResult,
  RoutingInfo,
} from "api/premium/PremiumAssignmentApi"
import { UserTier } from "const/Subscription"
import type { TabSyncMessage, TabSyncMessageType } from "utils/crossTabSync"

// --- Module mocks (must precede provider import) ---

const mockUser = {
  id: 1,
  uid: "test-uid",
  subscription_plan_name: "Premium",
  subscription_status: "Premium",
}

const mockDispatchFn = jest.fn(() => Promise.resolve())
const mockLogoutFn = jest.fn()

jest.mock("react-redux", () => ({
  useSelector: (selector: (s: unknown) => unknown) =>
    selector({ user: { currentUser: mockUser, logoutGeneration: 0 } }),
  useDispatch: () => mockDispatchFn,
}))

jest.mock("store/slice/User/UserActions", () => ({
  __esModule: true,
  getMe: () => ({ type: "user/getMe" }),
}))

jest.mock("utils/auth/AuthUtils", () => ({
  __esModule: true,
  logout: mockLogoutFn,
}))

const mockAssignPremiumInstance = jest.fn<
  Promise<PremiumAssignmentResult>,
  []
>()
const mockReleasePremiumInstance = jest.fn<Promise<PremiumReleaseResult>, []>()
const mockGetPremiumStatus = jest.fn<Promise<PremiumStatusResult>, []>()
const mockGetBeaconTokenApi = jest
  .fn<Promise<{ data: { token: string } }>, []>()
  .mockResolvedValue({ data: { token: "t" } })
const mockSendPremiumHeartbeat = jest
  .fn<Promise<PremiumHeartbeatResult>, []>()
  .mockResolvedValue({} as PremiumHeartbeatResult)
const mockGetRoutingInfo = jest
  .fn<Promise<RoutingInfo | null>, []>()
  .mockResolvedValue(null)
const mockLogPremiumUiEvent = jest.fn<
  Promise<void>,
  [string, Record<string, unknown>?]
>()

jest.mock("api/premium/PremiumAssignmentApi", () => ({
  __esModule: true,
  assignPremiumInstance: mockAssignPremiumInstance,
  releasePremiumInstance: mockReleasePremiumInstance,
  getPremiumStatus: mockGetPremiumStatus,
  getBeaconTokenApi: mockGetBeaconTokenApi,
  sendPremiumHeartbeat: mockSendPremiumHeartbeat,
  getRoutingInfo: mockGetRoutingInfo,
  logPremiumUiEvent: mockLogPremiumUiEvent,
}))

jest.mock("hooks/useSleepDetection", () => ({
  __esModule: true,
  useSleepDetection: () => undefined,
}))

const mockTabSyncHandlers: Map<
  TabSyncMessageType,
  Set<(msg: TabSyncMessage) => void>
> = new Map()

jest.mock("utils/crossTabSync", () => ({
  __esModule: true,
  tabSync: {
    broadcast: () => {},
    broadcastLogout: () => {},
    broadcastPremiumReleased: () => {},
    on: (type: TabSyncMessageType, handler: (m: TabSyncMessage) => void) => {
      if (!mockTabSyncHandlers.has(type))
        mockTabSyncHandlers.set(type, new Set())
      mockTabSyncHandlers.get(type)!.add(handler)
      return () => mockTabSyncHandlers.get(type)?.delete(handler)
    },
    onAny: () => () => {},
    destroy: () => {},
  },
  syncActivityAcrossTabs: () => {},
  getLastActivityFromAnyTab: () => 0,
  onActivityFromOtherTab: () => () => {},
  CrossTabLeaderElection: class {
    constructor(onBecomeLeader: () => void) {
      setTimeout(onBecomeLeader, 0)
    }
    getIsLeader() {
      return true
    }
    destroy() {}
  },
}))

// require() not import — static imports hoist above the mock vars.
const { PremiumAssignmentProvider, usePremiumAssignment } =
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("contexts/PremiumAssignmentContext")
const { routingService } =
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("utils/routing/RoutingService")

// --- Helpers ---

type Ctx = ReturnType<typeof usePremiumAssignment>

const Harness: React.FC<{ ctxRef: { current: Ctx | null } }> = ({ ctxRef }) => {
  ctxRef.current = usePremiumAssignment()
  return null
}

const renderProvider = () => {
  const ctxRef: { current: Ctx | null } = { current: null }
  render(
    <SnackbarProvider maxSnack={3}>
      <PremiumAssignmentProvider>
        <Harness ctxRef={ctxRef} />
      </PremiumAssignmentProvider>
    </SnackbarProvider>,
  )
  return ctxRef
}

/** A retryable assign response (assigned: false, scaling_in_progress: true). */
const retryableAssignment: PremiumAssignmentResult = {
  message: "scaling in progress",
  instance_id: "",
  assigned: false,
  is_shared: false,
  assignment_source: "pending",
  scaling_in_progress: true,
  retry_after: 30,
}

/** A successful dedicated assignment. */
const dedicatedAssignment: PremiumAssignmentResult = {
  message: "dedicated",
  instance_id: "inst-A",
  instance_id_hash: "hash-A",
  assigned: true,
  is_shared: false,
  assignment_source: "existing",
}

/** Status endpoint returning null assignment (no assignment exists). */
const nullAssignmentStatus: PremiumStatusResult = {
  user_id: 1,
  subscription_type: UserTier.PREMIUM,
  is_premium: true,
  assignment: null,
}

/** Status endpoint returning a dedicated assignment. */
const dedicatedStatus: PremiumStatusResult = {
  user_id: 1,
  subscription_type: UserTier.PREMIUM,
  is_premium: true,
  assignment: {
    instance_id: "inst-A",
    is_shared: false,
    assigned_at: "2026-06-22T00:00:00Z",
    status: "active",
  },
}

/** Simulate a pointerdown event on the window (as a user click). */
const simulateClick = () => {
  window.dispatchEvent(new Event("pointerdown"))
}

/**
 * Advance timers and flush microtasks to simulate polling cycles.
 * Each call advances 60s (enough for any backoff interval).
 */
const advanceOnePollCycle = async () => {
  await act(async () => {
    jest.advanceTimersByTime(60_000)
    await Promise.resolve()
  })
}

// --- Tests ---

describe("PremiumAssignmentProvider — re-trigger assign during polling", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
    mockTabSyncHandlers.clear()
    localStorage.clear()
    sessionStorage.clear()
    routingService.clearRoutingInfo()
    routingService.setPremiumAssigned(false)
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  test("re-trigger fires at correct interval (every 3 polls with null status)", async () => {
    // autoAssignOnLogin: /status returns null → calls /assign → retryable
    mockGetPremiumStatus.mockResolvedValue(nullAssignmentStatus)
    mockAssignPremiumInstance.mockResolvedValue(retryableAssignment)

    renderProvider()

    // Wait for autoAssignOnLogin to complete with retryable response.
    await waitFor(() => {
      expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)
    })

    // Clear to track only polling-phase calls.
    mockAssignPremiumInstance.mockClear()
    // Keep returning null status so re-trigger fires.
    mockAssignPremiumInstance.mockResolvedValue(retryableAssignment)

    // Poll 1: no re-trigger expected
    await advanceOnePollCycle()
    expect(mockAssignPremiumInstance).not.toHaveBeenCalled()

    // Poll 2: no re-trigger expected
    await advanceOnePollCycle()
    expect(mockAssignPremiumInstance).not.toHaveBeenCalled()

    // Poll 3: re-trigger expected (pollAttempts=2, (2+1)%3===0)
    await advanceOnePollCycle()
    expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)

    mockAssignPremiumInstance.mockClear()

    // Polls 4-5: no re-trigger
    await advanceOnePollCycle()
    await advanceOnePollCycle()
    expect(mockAssignPremiumInstance).not.toHaveBeenCalled()

    // Poll 6: re-trigger again
    await advanceOnePollCycle()
    expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)
  })

  test("re-trigger success transitions state and stops polling", async () => {
    // autoAssignOnLogin: retryable
    mockGetPremiumStatus.mockResolvedValue(nullAssignmentStatus)
    mockAssignPremiumInstance.mockResolvedValue(retryableAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)
    })

    // After polls 1-2 return null, poll 3 re-triggers assign.
    // This time, assign succeeds.
    mockAssignPremiumInstance.mockResolvedValue(dedicatedAssignment)

    // Advance through 3 poll cycles.
    await advanceOnePollCycle() // poll 1
    await advanceOnePollCycle() // poll 2
    await advanceOnePollCycle() // poll 3 → re-trigger → success

    // State should reflect the dedicated assignment.
    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.assigned).toBe(true)
      expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
    })

    // Routing should be configured.
    expect(routingService.isPremiumAssigned()).toBe(true)

    // Error should be cleared.
    expect(ctxRef.current?.error).toBeNull()

    // Beacon token should have been acquired.
    expect(mockGetBeaconTokenApi).toHaveBeenCalled()
  })

  test("re-trigger failure continues polling without crash", async () => {
    // autoAssignOnLogin: retryable
    mockGetPremiumStatus.mockResolvedValue(nullAssignmentStatus)
    mockAssignPremiumInstance.mockResolvedValue(retryableAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)
    })

    // Re-trigger will throw on poll 3.
    mockAssignPremiumInstance.mockRejectedValue(new Error("Lock contention"))

    // Advance through 3 polls → re-trigger fires and fails.
    await advanceOnePollCycle()
    await advanceOnePollCycle()
    await advanceOnePollCycle()

    // Polling should continue — no crash.
    // assignmentResult should still be the retryable one (not cleared).
    expect(ctxRef.current?.assignmentResult?.assigned).toBe(false)
    // error is the original retryable message from autoAssignOnLogin — the
    // re-trigger failure must NOT replace or escalate it.
    expect(ctxRef.current?.error).toBe("scaling in progress")

    // Advance one more cycle to confirm polling continues.
    mockAssignPremiumInstance.mockClear()
    await advanceOnePollCycle() // poll 4
    // Still polling (getPremiumStatus called).
    expect(mockGetPremiumStatus.mock.calls.length).toBeGreaterThan(3)
  })

  test("normal assign (assigned: true) does NOT fire re-trigger", async () => {
    // autoAssignOnLogin: /status returns null → calls /assign → succeeds
    mockGetPremiumStatus.mockResolvedValue(nullAssignmentStatus)
    mockAssignPremiumInstance.mockResolvedValue(dedicatedAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.assigned).toBe(true)
    })

    // Polling should not be active (dedicated assignment found).
    // assignPremiumInstance should have been called exactly once.
    expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)

    mockAssignPremiumInstance.mockClear()
    mockGetPremiumStatus.mockClear()

    // Advance several cycles — no re-trigger or polling should fire.
    await advanceOnePollCycle()
    await advanceOnePollCycle()
    await advanceOnePollCycle()

    expect(mockAssignPremiumInstance).not.toHaveBeenCalled()
  })

  test("MAX_POLL_ATTEMPTS resets flags for retryable cases", async () => {
    // Cannot pre-seed pollAttempts via sessionStorage because the reset
    // effect clears it when assignmentResult.is_shared changes on mount.
    // Instead, loop through all 40 poll cycles (fast with fake timers).
    mockGetPremiumStatus.mockResolvedValue(nullAssignmentStatus)
    mockAssignPremiumInstance.mockResolvedValue(retryableAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)
    })

    // Advance through all 40 poll cycles to reach MAX_POLL_ATTEMPTS.
    for (let i = 0; i < 40; i++) {
      await advanceOnePollCycle()
    }

    // State should show error and assignmentResult should be null (polling stopped).
    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult).toBeNull()
      expect(ctxRef.current?.error).toContain(
        "No premium instance available after extended wait",
      )
    })

    // sessionStorage hasAttempted should be cleared (allowing fresh retry).
    expect(sessionStorage.getItem("premium_hasAttempted")).toBeNull()
  })

  test("user gesture recovery after MAX_POLL_ATTEMPTS exhaustion", async () => {
    mockGetPremiumStatus.mockResolvedValue(nullAssignmentStatus)
    mockAssignPremiumInstance.mockResolvedValue(retryableAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)
    })

    // Advance through all 40 poll cycles to reach MAX_POLL_ATTEMPTS.
    for (let i = 0; i < 40; i++) {
      await advanceOnePollCycle()
    }

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult).toBeNull()
    })

    // Now set up mocks for the recovery path: assign succeeds.
    mockAssignPremiumInstance.mockClear()
    mockGetPremiumStatus.mockClear()
    mockGetPremiumStatus.mockResolvedValue(dedicatedStatus)

    // Simulate user gesture (click) — should trigger fresh autoAssignOnLogin.
    act(() => {
      simulateClick()
    })

    // autoAssignOnLogin should re-fire via autoAssignGeneration bump.
    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
      expect(ctxRef.current?.assignmentResult?.assigned).toBe(true)
    })

    // Error should be cleared.
    expect(ctxRef.current?.error).toBeNull()
  })
})

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

jest.mock("react-redux", () => ({
  useSelector: (selector: (s: unknown) => unknown) =>
    selector({ user: { currentUser: mockUser, logoutGeneration: 0 } }),
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
  getLastActivityFromAnyTab: () => null,
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
const { PremiumAssignmentProvider, usePremiumAssignment, SS_POLL_ATTEMPTS } =
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

const sharedAssignment: PremiumAssignmentResult = {
  message: "shared",
  instance_id: "inst-shared",
  assigned: true,
  is_shared: true,
  assignment_source: "shared",
}

const dedicatedAssignment: PremiumAssignmentResult = {
  message: "dedicated",
  instance_id: "inst-A",
  assigned: true,
  is_shared: false,
  assignment_source: "existing",
}

const sharedStatus: PremiumStatusResult = {
  user_id: 1,
  subscription_type: UserTier.PREMIUM,
  is_premium: true,
  assignment: {
    instance_id: "inst-shared",
    is_shared: true,
    assigned_at: "2026-05-12T00:00:00Z",
    status: "active",
  },
}

const dedicatedStatus: PremiumStatusResult = {
  user_id: 1,
  subscription_type: UserTier.PREMIUM,
  is_premium: true,
  assignment: {
    instance_id: "inst-A",
    is_shared: false,
    assigned_at: "2026-05-12T00:00:00Z",
    status: "active",
  },
}

// --- Tests ---

describe("PremiumAssignmentProvider — polling routing restore", () => {
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

  test("polling success on dedicated restores premiumAssigned=true after a prior 502/503 stripped it", async () => {
    // Stage 1: autoAssignOnLogin sees an existing SHARED assignment via
    // GET /status — provider adopts it and polling begins.
    // Stage 2: the next /status poll returns DEDICATED.
    mockGetPremiumStatus
      .mockResolvedValueOnce(sharedStatus)
      .mockResolvedValue(dedicatedStatus)

    const ctxRef = renderProvider()

    // Wait until the provider has adopted the shared status (mount + autoAssign).
    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(true)
    })

    // Simulate the failure mode: a prior 502/503 in this tab fired
    // handlePremiumRoutingError, which strips routing.
    act(() => {
      routingService.setPremiumAssigned(false)
    })
    expect(routingService.isPremiumAssigned()).toBe(false)

    // Advance through the polling timer; the first poll returns dedicated.
    await act(async () => {
      jest.advanceTimersByTime(60_000)
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(false)
    })

    // The regression: without the fix, premiumAssigned stays false here.
    expect(routingService.isPremiumAssigned()).toBe(true)
  })

  test("polling success on shared→dedicated calls getBeaconTokenApi", async () => {
    // Before the fix, the polling success path set assignmentResult and
    // restored routing but did NOT call getBeaconTokenApi(). This left
    // beaconTokenRef stale and skipped the routing probe that would
    // trigger auto-recovery on 502/503.
    mockGetPremiumStatus
      .mockResolvedValueOnce(sharedStatus)
      .mockResolvedValue(dedicatedStatus)

    mockGetBeaconTokenApi.mockClear()

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(true)
    })

    // autoAssignOnLogin path calls getBeaconTokenApi once for the shared assignment.
    const callsAfterMount = mockGetBeaconTokenApi.mock.calls.length
    expect(callsAfterMount).toBeGreaterThanOrEqual(1)

    // Advance timer to trigger polling; next /status returns dedicated.
    await act(async () => {
      jest.advanceTimersByTime(60_000)
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(false)
    })

    // The fix: getBeaconTokenApi must be called again after polling detects
    // the dedicated instance — this is the beacon-token acquisition +
    // routing probe that was missing before this fix.
    expect(mockGetBeaconTokenApi.mock.calls.length).toBeGreaterThan(
      callsAfterMount,
    )
  })

  test("polling success on shared→dedicated acquires beacon token even when fetch fails", async () => {
    // When getBeaconTokenApi rejects (e.g. 502/503 from the dedicated
    // instance), the polling success path must NOT throw — the catch
    // block absorbs the error and the assignment flow continues.
    mockGetPremiumStatus
      .mockResolvedValueOnce(sharedStatus)
      .mockResolvedValue(dedicatedStatus)

    // Let mount-time beacon call succeed, then fail on the polling path.
    mockGetBeaconTokenApi
      .mockResolvedValueOnce({ data: { token: "mount-token" } })
      .mockRejectedValueOnce(new Error("Service Unavailable"))

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(true)
    })

    // Advance timer to trigger polling; beacon fetch will reject.
    await act(async () => {
      jest.advanceTimersByTime(60_000)
      await Promise.resolve()
    })

    // Despite the beacon failure, assignment must still transition to dedicated.
    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(false)
    })
    expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
    // No error should be surfaced to the user.
    expect(ctxRef.current?.error).toBeNull()
  })

  test("polling uses /status — converges to dedicated even if /assign would still return shared", async () => {
    // Models ISSUE_2 candidate (3): the canonical row is dedicated, but a hypothetical
    // /assign call would still return shared. /status reads the canonical row, so
    // polling must converge to dedicated within one cycle without a reload.
    mockGetPremiumStatus
      .mockResolvedValueOnce(sharedStatus)
      .mockResolvedValue(dedicatedStatus)
    mockAssignPremiumInstance.mockResolvedValue(sharedAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(true)
    })

    await act(async () => {
      jest.advanceTimersByTime(60_000)
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(false)
    })
    expect(ctxRef.current?.assignmentResult?.instance_id).toBe(
      dedicatedAssignment.instance_id,
    )
    // /assign must NOT have been called by the polling effect.
    expect(mockAssignPremiumInstance).not.toHaveBeenCalled()
  })

  test("polling does not terminate at MAX_POLL_ATTEMPTS while on shared — converges to dedicated post-cap", async () => {
    // pollAttempts is restored from sessionStorage on mount, so a long-running tab
    // can come up already past the cap.
    sessionStorage.setItem(SS_POLL_ATTEMPTS, "41")

    mockGetPremiumStatus
      .mockResolvedValueOnce(sharedStatus)
      .mockResolvedValue(dedicatedStatus)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(true)
    })

    await act(async () => {
      jest.advanceTimersByTime(60_000)
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(false)
    })
    expect(ctxRef.current?.error).toBeNull()
  })

  test("polling on shared neither terminates nor increments pollAttempts", async () => {
    // Isolates the cap-bypass + counter-freeze: across multiple shared polls,
    // error must stay null and pollAttempts must not be persisted (the provider
    // only writes the SS key when the counter is >0, so a null read proves no
    // increment occurred).
    mockGetPremiumStatus.mockResolvedValue(sharedStatus)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(true)
    })

    for (let i = 0; i < 3; i++) {
      await act(async () => {
        jest.advanceTimersByTime(120_000)
        await Promise.resolve()
      })
    }

    expect(ctxRef.current?.assignmentResult?.is_shared).toBe(true)
    expect(ctxRef.current?.error).toBeNull()
    expect(sessionStorage.getItem(SS_POLL_ATTEMPTS)).toBeNull()
  })

  test("repeated shared-status polls do not churn assignmentResult identity", async () => {
    // Guards the value-equality short-circuit: when /status returns the same
    // shared row, the provider must keep assignmentResult reference-stable so
    // downstream consumers (useLogs, etc.) don't re-render or reset.
    mockGetPremiumStatus.mockResolvedValue(sharedStatus)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(true)
    })

    const firstRef = ctxRef.current?.assignmentResult

    for (let i = 0; i < 3; i++) {
      await act(async () => {
        jest.advanceTimersByTime(120_000)
        await Promise.resolve()
      })
    }

    expect(ctxRef.current?.assignmentResult).toBe(firstRef)
  })

  test("polling fires via unreachable path on dedicated assignment, uses /status not /assign", async () => {
    // Models the live scenario the bundle-inspection check substituted for: dedicated EC2
    // dies, axios.handlePremiumRoutingError emits emitPremiumUnreachable, the unreachable
    // machine flips, shouldPoll returns true via the unreachable path (not the shared
    // path), and the polling tick must call /status — not /assign.
    mockGetPremiumStatus.mockResolvedValue(dedicatedStatus)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.is_shared).toBe(false)
    })

    act(() => {
      routingService.emitPremiumUnreachable({
        url: "/some/premium-routed/endpoint",
        status: 503,
        sentAt: 1000,
      })
    })
    await waitFor(() => {
      expect(ctxRef.current?.unreachable.state.instanceUnreachable).toBe(true)
    })

    mockGetPremiumStatus.mockClear()
    mockAssignPremiumInstance.mockClear()

    await act(async () => {
      jest.advanceTimersByTime(60_000)
      await Promise.resolve()
    })

    expect(mockGetPremiumStatus).toHaveBeenCalled()
    expect(mockAssignPremiumInstance).not.toHaveBeenCalled()
  })
})

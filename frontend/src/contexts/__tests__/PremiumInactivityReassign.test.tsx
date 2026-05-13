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

import {
  PremiumAssignmentResult,
  PremiumReleaseResult,
  PremiumStatusResult,
  PremiumHeartbeatResult,
  RoutingInfo,
} from "api/premium/PremiumAssignmentApi"
import { UserTier } from "const/Subscription"
import { TabSyncMessage, TabSyncMessageType } from "utils/crossTabSync"

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

const { PremiumAssignmentProvider, usePremiumAssignment } =
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("contexts/PremiumAssignmentContext")
const { routingService } =
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("utils/routing/RoutingService")

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

const dedicatedAssignment: PremiumAssignmentResult = {
  message: "dedicated",
  instance_id: "inst-A",
  assigned: true,
  is_shared: false,
  assignment_source: "fresh",
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

const noAssignmentStatus: PremiumStatusResult = {
  user_id: 1,
  subscription_type: UserTier.PREMIUM,
  is_premium: true,
  assignment: null,
}

describe("PremiumAssignmentProvider — inactivity re-assignment", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
    mockTabSyncHandlers.clear()
    localStorage.clear()
    sessionStorage.clear()
    routingService.clearRoutingInfo()
    routingService.setPremiumAssigned(false)
    Object.defineProperty(navigator, "sendBeacon", {
      configurable: true,
      value: jest.fn(() => true),
    })
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  test("clicks after 2h inactivity auto-release re-fire assignPremiumInstance", async () => {
    mockGetPremiumStatus.mockResolvedValueOnce(dedicatedStatus)
    mockAssignPremiumInstance.mockResolvedValue(dedicatedAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
    })
    expect(sessionStorage.getItem("premium_hasAttempted")).toBe("true")

    mockGetPremiumStatus.mockResolvedValue(noAssignmentStatus)
    mockAssignPremiumInstance.mockClear()

    await act(async () => {
      jest.advanceTimersByTime(2 * 60 * 60 * 1000 + 60 * 1000)
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult).toBeNull()
    })
    expect(sessionStorage.getItem("premium_hasAttempted")).toBeNull()
    expect(mockAssignPremiumInstance).not.toHaveBeenCalled()

    await act(async () => {
      window.dispatchEvent(new Event("pointerdown"))
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)
    })
  })

  test("a failed reassign does not strand the user — next click retries", async () => {
    mockGetPremiumStatus.mockResolvedValueOnce(dedicatedStatus)
    mockAssignPremiumInstance.mockResolvedValue(dedicatedAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
    })

    mockGetPremiumStatus.mockResolvedValue(noAssignmentStatus)
    mockAssignPremiumInstance.mockReset()
    mockAssignPremiumInstance
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue(dedicatedAssignment)

    await act(async () => {
      jest.advanceTimersByTime(2 * 60 * 60 * 1000 + 60 * 1000)
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult).toBeNull()
    })

    await act(async () => {
      window.dispatchEvent(new Event("pointerdown"))
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)
    })

    await act(async () => {
      window.dispatchEvent(new Event("pointerdown"))
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(2)
    })
  })

  test("cross-tab PREMIUM_RELEASED primes the receiving tab to reassign on click", async () => {
    mockGetPremiumStatus.mockResolvedValueOnce(dedicatedStatus)
    mockAssignPremiumInstance.mockResolvedValue(dedicatedAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
    })
    expect(sessionStorage.getItem("premium_hasAttempted")).toBe("true")

    mockGetPremiumStatus.mockResolvedValue(noAssignmentStatus)
    mockAssignPremiumInstance.mockClear()

    await act(async () => {
      const handlers = mockTabSyncHandlers.get("PREMIUM_RELEASED")
      handlers?.forEach((h) =>
        h({ type: "PREMIUM_RELEASED" } as TabSyncMessage),
      )
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult).toBeNull()
    })
    expect(sessionStorage.getItem("premium_hasAttempted")).toBeNull()

    await act(async () => {
      window.dispatchEvent(new Event("pointerdown"))
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(mockAssignPremiumInstance).toHaveBeenCalledTimes(1)
    })
  })

  test("clicks while assignment is active do not trigger redundant re-assignment", async () => {
    mockGetPremiumStatus.mockResolvedValue(dedicatedStatus)
    mockAssignPremiumInstance.mockResolvedValue(dedicatedAssignment)

    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
    })

    mockAssignPremiumInstance.mockClear()

    await act(async () => {
      window.dispatchEvent(new Event("pointerdown"))
      window.dispatchEvent(new Event("keydown"))
      await Promise.resolve()
    })

    expect(mockAssignPremiumInstance).not.toHaveBeenCalled()
  })
})

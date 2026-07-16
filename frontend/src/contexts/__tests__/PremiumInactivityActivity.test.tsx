/**
 * Tests for the inactivity-clock reset on genuine activity (Issue #737).
 *
 * Covers:
 *  1. User interaction (pointerdown) resets the clock and prevents the 2h
 *     auto-release.
 *  2. A running workflow keeps the instance active with no direct user input.
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

// --- Module mocks (must precede provider import) ---

const mockUser = {
  id: 1,
  uid: "test-uid",
  subscription_plan_name: "Premium",
  subscription_status: "Premium",
}

// Mutable pipeline status so a test can simulate a running workflow.
// Prefixed with `mock` so the jest.mock factory may reference it.
let mockPipelineStatus = "StartUninitialized"

const mockDispatchFn = jest.fn(() => Promise.resolve())
const mockLogoutFn = jest.fn()
const mockBroadcastPremiumReleased = jest.fn()
const mockLogPremiumUiEvent = jest.fn()

jest.mock("react-redux", () => ({
  useSelector: (selector: (s: unknown) => unknown) =>
    selector({
      user: { currentUser: mockUser, logoutGeneration: 0 },
      pipeline: { run: { status: mockPipelineStatus } },
    }),
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

jest.mock("utils/crossTabSync", () => ({
  __esModule: true,
  tabSync: {
    broadcast: () => {},
    broadcastLogout: () => {},
    broadcastPremiumReleased: mockBroadcastPremiumReleased,
    on: () => () => {},
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

const advance = async (ms: number) => {
  await act(async () => {
    jest.advanceTimersByTime(ms)
    await Promise.resolve()
  })
}

const ONE_MIN = 60 * 1000

// --- Tests ---

describe("PremiumAssignmentProvider — activity resets inactivity clock", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
    localStorage.clear()
    sessionStorage.clear()
    mockPipelineStatus = "StartUninitialized"
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  test("user interaction resets the clock and prevents the 2h auto-release", async () => {
    mockGetPremiumStatus.mockResolvedValue(dedicatedStatus)
    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
    })

    // Almost 2h with no activity — not released yet.
    await advance(115 * ONE_MIN)
    expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")

    // Genuine user interaction resets the clock.
    act(() => {
      window.dispatchEvent(new Event("pointerdown"))
    })

    // Another ~2h from mount, but under 2h since the interaction.
    await advance(115 * ONE_MIN)

    // Activity prevented the auto-release.
    expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
  })

  test("a running workflow prevents the 2h auto-release with no user input", async () => {
    mockPipelineStatus = "StartSuccess"
    mockGetPremiumStatus.mockResolvedValue(dedicatedStatus)
    const ctxRef = renderProvider()

    await waitFor(() => {
      expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
    })

    // Advance past 2h with zero interaction — workflow keeps it active.
    await advance(2 * 60 * ONE_MIN + 5000)

    expect(ctxRef.current?.assignmentResult?.instance_id).toBe("inst-A")
    expect(ctxRef.current?.showInactivityWarning).toBe(false)
  })
})

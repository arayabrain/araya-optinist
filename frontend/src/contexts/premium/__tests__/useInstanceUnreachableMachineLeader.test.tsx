/**
 * Leader-gated behaviour in useInstanceUnreachableMachine.
 *
 * The hook takes `isTabLeader` as an argument; leader-only logic is:
 *  - Snapshot write to localStorage (both the "write on unreachable" and
 *    "clear on recovery" branches).
 *  - Probe timer arming after INITIAL_PROBE_DELAY_MS.
 *
 * These tests drive the hook directly so we can pin the gate without the
 * extra surface area of PremiumAssignmentProvider.
 */

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
import type { TabSyncMessage, TabSyncMessageType } from "utils/crossTabSync"

// --- Module mocks (must be declared before importing the hook) ---

const mockLogPremiumUiEvent = jest.fn<
  Promise<void>,
  [string, Record<string, unknown>?]
>()

jest.mock("api/premium/PremiumAssignmentApi", () => ({
  __esModule: true,
  logPremiumUiEvent: mockLogPremiumUiEvent,
}))

// tabSync mock — no-op broadcast; register-only handlers.
jest.mock("utils/crossTabSync", () => {
  const handlers = new Map<
    TabSyncMessageType,
    Set<(msg: TabSyncMessage) => void>
  >()
  return {
    __esModule: true,
    tabSync: {
      broadcast: () => {},
      broadcastPremiumReleased: () => {},
      on: (type: TabSyncMessageType, h: (m: TabSyncMessage) => void) => {
        if (!handlers.has(type)) handlers.set(type, new Set())
        handlers.get(type)!.add(h)
        return () => handlers.get(type)?.delete(h)
      },
      onAny: () => () => {},
      destroy: () => {},
    },
  }
})

// --- Imports (after mocks) ---
// require() not import: static imports hoist above mock vars and cause TDZ when factories run.
const {
  DEDICATED_HANDOFF_GRACE_MS,
  INITIAL_PROBE_DELAY_MS,
  LS_UNREACHABLE_SNAPSHOT,
}: typeof import("contexts/premium/unreachableConstants") =
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("contexts/premium/unreachableConstants")
const {
  useInstanceUnreachableMachine,
}: typeof import("contexts/premium/useInstanceUnreachableMachine") =
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("contexts/premium/useInstanceUnreachableMachine")
const { routingService }: typeof import("utils/routing/RoutingService") =
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("utils/routing/RoutingService")

// --- Harness ---

type Handle = ReturnType<typeof useInstanceUnreachableMachine>

const Harness: React.FC<{
  assignment: PremiumAssignmentResult | null
  isTabLeader: boolean
  captureRef: { current: Handle | null }
}> = ({ assignment, isTabLeader, captureRef }) => {
  captureRef.current = useInstanceUnreachableMachine({
    assignment,
    isTabLeader,
  })
  return null
}

const dedicated: PremiumAssignmentResult = {
  message: "ok",
  instance_id: "inst-A",
  assigned: true,
  is_shared: false,
}

const shared: PremiumAssignmentResult = {
  message: "ok",
  instance_id: "shared-pool",
  assigned: true,
  is_shared: true,
}

const renderHook = (opts: {
  assignment?: PremiumAssignmentResult | null
  isTabLeader?: boolean
}) => {
  const captureRef: { current: Handle | null } = { current: null }
  const { rerender } = render(
    <Harness
      assignment={opts.assignment ?? dedicated}
      isTabLeader={opts.isTabLeader ?? true}
      captureRef={captureRef}
    />,
  )
  return {
    ref: captureRef,
    rerender: (next: {
      assignment?: PremiumAssignmentResult | null
      isTabLeader?: boolean
    }) =>
      rerender(
        <Harness
          assignment={next.assignment ?? opts.assignment ?? dedicated}
          isTabLeader={next.isTabLeader ?? opts.isTabLeader ?? true}
          captureRef={captureRef}
        />,
      ),
  }
}

const readSnapshot = (): Record<string, unknown> | null => {
  const raw = localStorage.getItem(LS_UNREACHABLE_SNAPSHOT)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

// --- Tests ---

describe("useInstanceUnreachableMachine — leader-gated side effects", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    localStorage.clear()
    routingService.clearRoutingInfo()
    routingService.setPremiumAssigned(false)
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  test("non-leader tab does NOT write premium_unreachable_snapshot on unreachable", () => {
    // Drive the hook into unreachable state on a non-leader tab. The
    // leader-only write branch must skip — no snapshot key in localStorage.
    const { ref } = renderHook({ isTabLeader: false })

    act(() => {
      routingService.emitPremiumUnreachable({ status: 503, sentAt: 1 })
    })

    expect(ref.current?.state.instanceUnreachable).toBe(true)
    expect(readSnapshot()).toBeNull()
  })

  test("leader tab writes snapshot when unreachable, and clears it on recovery", () => {
    const { ref } = renderHook({ isTabLeader: true })

    act(() => {
      routingService.emitPremiumUnreachable({ status: 503, sentAt: 1 })
    })

    expect(ref.current?.state.instanceUnreachable).toBe(true)
    const snap = readSnapshot()
    expect(snap).not.toBeNull()
    expect(snap?.instance_id).toBe("inst-A")
    expect(snap?.is_terminal).toBe(false)
    expect(typeof snap?.updated_at).toBe("number")

    act(() => {
      routingService.emitPremiumReachable({ status: 200, sentAt: 2 })
    })

    expect(ref.current?.state.instanceUnreachable).toBe(false)
    // Clear branch — leader has "ever been unreachable" flag set, so it
    // proactively removes the key rather than leaving a stale entry.
    expect(localStorage.getItem(LS_UNREACHABLE_SNAPSHOT)).toBeNull()
  })

  test("leader-gated probe timer arms setPremiumAssigned(true) and logs instance_probe_armed after INITIAL_PROBE_DELAY_MS", () => {
    jest.useFakeTimers()

    const { ref } = renderHook({ isTabLeader: true })

    act(() => {
      routingService.emitPremiumUnreachable({ status: 503, sentAt: 1 })
    })
    expect(ref.current?.state.instanceUnreachable).toBe(true)
    // setPremiumAssigned was NOT flipped back to true by the unreachable
    // emission itself — the probe timer is the only thing that re-arms it.
    expect(routingService.isPremiumAssigned()).toBe(false)

    // Advance to just before the delay — no probe yet.
    act(() => {
      jest.advanceTimersByTime(INITIAL_PROBE_DELAY_MS - 1)
    })
    expect(routingService.isPremiumAssigned()).toBe(false)
    expect(mockLogPremiumUiEvent).not.toHaveBeenCalledWith(
      "instance_probe_armed",
      expect.anything(),
    )

    // Cross the threshold — probe fires.
    act(() => {
      jest.advanceTimersByTime(1)
    })

    expect(routingService.isPremiumAssigned()).toBe(true)
    expect(mockLogPremiumUiEvent).toHaveBeenCalledWith(
      "instance_probe_armed",
      expect.objectContaining({
        instance_id: "inst-A",
        failed_probes: 0,
        delay_ms: INITIAL_PROBE_DELAY_MS,
      }),
    )
  })

  test("non-leader tab does NOT arm the probe timer", () => {
    jest.useFakeTimers()

    renderHook({ isTabLeader: false })

    act(() => {
      routingService.emitPremiumUnreachable({ status: 503, sentAt: 1 })
    })

    // Run well past the probe delay — non-leader must not arm.
    act(() => {
      jest.advanceTimersByTime(INITIAL_PROBE_DELAY_MS * 3)
    })

    expect(routingService.isPremiumAssigned()).toBe(false)
    expect(mockLogPremiumUiEvent).not.toHaveBeenCalledWith(
      "instance_probe_armed",
      expect.anything(),
    )
  })
})

describe("useInstanceUnreachableMachine — dedicated handoff warm-up grace", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    localStorage.clear()
    routingService.clearRoutingInfo()
    routingService.setPremiumAssigned(false)
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  test("first 5xx within grace after shared → dedicated transition is suppressed", () => {
    jest.useFakeTimers()

    // Start on shared, then flip to dedicated — that flip starts the grace.
    const { ref, rerender } = renderHook({ assignment: shared })
    act(() => {
      rerender({ assignment: dedicated })
    })

    act(() => {
      jest.advanceTimersByTime(DEDICATED_HANDOFF_GRACE_MS - 1)
      routingService.emitPremiumUnreachable({
        url: "/api/run",
        status: 503,
        sentAt: Date.now(),
      })
    })

    expect(ref.current?.state.instanceUnreachable).toBe(false)
    expect(mockLogPremiumUiEvent).toHaveBeenCalledWith(
      "instance_unreachable_warmup_suppressed",
      expect.objectContaining({
        instance_id: "inst-A",
        url: "/api/run",
        status: 503,
      }),
    )
    expect(mockLogPremiumUiEvent).not.toHaveBeenCalledWith(
      "instance_unreachable",
      expect.anything(),
    )
  })

  test("5xx after grace expires flips to unreachable as normal", () => {
    jest.useFakeTimers()

    const { ref, rerender } = renderHook({ assignment: shared })
    act(() => {
      rerender({ assignment: dedicated })
    })

    act(() => {
      jest.advanceTimersByTime(DEDICATED_HANDOFF_GRACE_MS + 1)
      routingService.emitPremiumUnreachable({
        url: "/api/run",
        status: 503,
        sentAt: Date.now(),
      })
    })

    expect(ref.current?.state.instanceUnreachable).toBe(true)
    expect(mockLogPremiumUiEvent).toHaveBeenCalledWith(
      "instance_unreachable",
      expect.objectContaining({ instance_id: "inst-A", status: 503 }),
    )
    expect(mockLogPremiumUiEvent).not.toHaveBeenCalledWith(
      "instance_unreachable_warmup_suppressed",
      expect.anything(),
    )
  })

  test("grace is single-shot: a second 5xx within the window is NOT suppressed", () => {
    jest.useFakeTimers()

    const { ref, rerender } = renderHook({ assignment: shared })
    act(() => {
      rerender({ assignment: dedicated })
    })

    // First 5xx within grace — suppressed.
    act(() => {
      jest.advanceTimersByTime(1000)
      routingService.emitPremiumUnreachable({
        status: 503,
        sentAt: Date.now(),
      })
    })
    expect(ref.current?.state.instanceUnreachable).toBe(false)

    // Second 5xx still within the original grace window — must flip.
    act(() => {
      jest.advanceTimersByTime(1000)
      routingService.emitPremiumUnreachable({
        status: 503,
        sentAt: Date.now(),
      })
    })

    expect(ref.current?.state.instanceUnreachable).toBe(true)
    expect(mockLogPremiumUiEvent).toHaveBeenCalledWith(
      "instance_unreachable_warmup_suppressed",
      expect.anything(),
    )
    expect(mockLogPremiumUiEvent).toHaveBeenCalledWith(
      "instance_unreachable",
      expect.anything(),
    )
  })

  test("dedicated reassignment to a different instance starts a fresh grace", () => {
    jest.useFakeTimers()

    const { ref, rerender } = renderHook({ assignment: shared })
    act(() => {
      rerender({ assignment: dedicated })
    })

    // Burn the first grace.
    act(() => {
      jest.advanceTimersByTime(1000)
      routingService.emitPremiumUnreachable({
        status: 503,
        sentAt: Date.now(),
      })
    })
    expect(ref.current?.state.instanceUnreachable).toBe(false)

    // Reassign onto a different dedicated instance — should re-arm grace and clear state.
    const dedicatedB: PremiumAssignmentResult = {
      ...dedicated,
      instance_id: "inst-B",
    }
    act(() => {
      rerender({ assignment: dedicatedB })
    })

    // First 5xx after reassignment, well within new grace.
    act(() => {
      jest.advanceTimersByTime(2000)
      routingService.emitPremiumUnreachable({
        status: 503,
        sentAt: Date.now(),
      })
    })

    expect(ref.current?.state.instanceUnreachable).toBe(false)
    // Two separate suppression events — one per dedicated transition.
    const suppressedCalls = mockLogPremiumUiEvent.mock.calls.filter(
      ([event]) => event === "instance_unreachable_warmup_suppressed",
    )
    expect(suppressedCalls).toHaveLength(2)
    expect(suppressedCalls[1][1]).toEqual(
      expect.objectContaining({ instance_id: "inst-B" }),
    )
  })
})

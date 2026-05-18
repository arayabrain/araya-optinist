/**
 * Tests for PremiumNotificationManager's instance-unreachable snackbar.
 *
 * Covers:
 *  - Initial non-terminal snackbar (no Retry button)
 *  - Non-terminal → terminal transition swaps the snackbar (close + enqueue)
 *    and the new snackbar carries an actionable Retry button
 *  - Retry click closes the snackbar and invokes retryProbe
 *  - Dismiss logs the correct `reason` for each exit path
 */

import React from "react"

import { beforeEach, describe, expect, jest, test } from "@jest/globals"
import { act, fireEvent, render } from "@testing-library/react"

// --- Snackbar mock -----------------------------------------------------------

type SnackbarCall = {
  key: string | number
  message: string
  options?: {
    variant?: string
    action?: (key: string | number) => React.ReactNode
  }
}

const mockEnqueueSnackbar = jest.fn() as unknown as jest.Mock<
  string | number,
  [string, Record<string, unknown>]
>
const mockCloseSnackbar = jest.fn()

let snackbarLog: SnackbarCall[] = []
let snackbarKeyCounter = 0

jest.mock("notistack", () => ({
  __esModule: true,
  useSnackbar: () => ({
    enqueueSnackbar: mockEnqueueSnackbar,
    closeSnackbar: mockCloseSnackbar,
  }),
  // eslint-disable-next-line react/prop-types
  SnackbarProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}))

// --- Context mock ------------------------------------------------------------

type CtxShape = {
  isPremiumUser: boolean
  assignmentResult: {
    instance_id?: string
    assigned?: boolean
    is_shared?: boolean
  } | null
  error: string | null
  isAssigning: boolean
  isRetryableError: boolean
  unreachable: {
    state: { instanceUnreachable: boolean; isUnreachableTerminal: boolean }
    retryProbe: () => void
  }
}

let mockCtxValue: CtxShape = {
  isPremiumUser: true,
  assignmentResult: null,
  error: null,
  isAssigning: false,
  isRetryableError: false,
  unreachable: {
    state: { instanceUnreachable: false, isUnreachableTerminal: false },
    retryProbe: jest.fn(),
  },
}

jest.mock("contexts/PremiumAssignmentContext", () => ({
  __esModule: true,
  usePremiumAssignment: () => mockCtxValue,
}))

const mockLogPremiumUiEvent = jest.fn<
  Promise<void>,
  [string, Record<string, unknown>?]
>()

jest.mock("api/premium/PremiumAssignmentApi", () => ({
  __esModule: true,
  logPremiumUiEvent: mockLogPremiumUiEvent,
}))

// --- SUT import (after mocks) ------------------------------------------------

const PremiumNotificationManager: React.FC =
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("components/Premium/PremiumNotificationManager").default

// --- Helpers -----------------------------------------------------------------

const setCtx = (patch: Partial<CtxShape>) => {
  mockCtxValue = {
    ...mockCtxValue,
    ...patch,
    unreachable: { ...mockCtxValue.unreachable, ...(patch.unreachable ?? {}) },
  }
}

const baseCtx = (): CtxShape => ({
  isPremiumUser: true,
  assignmentResult: {
    instance_id: "inst-A",
    assigned: true,
    is_shared: false,
  },
  error: null,
  isAssigning: false,
  isRetryableError: false,
  unreachable: {
    state: { instanceUnreachable: false, isUnreachableTerminal: false },
    retryProbe: jest.fn(),
  },
})

// --- Tests -------------------------------------------------------------------

describe("PremiumNotificationManager — unreachable snackbar", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    snackbarLog = []
    snackbarKeyCounter = 0
    mockCtxValue = baseCtx()

    mockEnqueueSnackbar.mockImplementation(
      (message: string, options?: Record<string, unknown>) => {
        snackbarKeyCounter += 1
        const key = snackbarKeyCounter
        snackbarLog.push({
          key,
          message,
          options: options as SnackbarCall["options"],
        })
        return key
      },
    )
  })

  test("non-terminal unreachable: enqueues a warning snackbar without Retry action", () => {
    setCtx({
      unreachable: {
        state: { instanceUnreachable: true, isUnreachableTerminal: false },
        retryProbe: jest.fn(),
      },
    })

    render(<PremiumNotificationManager />)

    const unreachableCall = snackbarLog.find((c) =>
      c.message.includes("temporarily unreachable"),
    )
    expect(unreachableCall).toBeDefined()
    expect(unreachableCall?.options?.variant).toBe("warning")
    expect(unreachableCall?.options?.action).toBeUndefined()
    expect(mockLogPremiumUiEvent).toHaveBeenCalledWith(
      "instance_unreachable_popup_shown",
      expect.objectContaining({ terminal: false, instance_id: "inst-A" }),
    )
  })

  test("non-terminal → terminal transition: closes the old snackbar and enqueues a new one with a Retry action", () => {
    const retry = jest.fn()
    setCtx({
      unreachable: {
        state: { instanceUnreachable: true, isUnreachableTerminal: false },
        retryProbe: retry,
      },
    })

    const { rerender } = render(<PremiumNotificationManager />)
    // First render should have queued one unreachable snackbar (non-terminal).
    expect(snackbarLog).toHaveLength(1)
    const firstKey = snackbarLog[0].key

    // Flip to terminal.
    mockCtxValue = {
      ...mockCtxValue,
      unreachable: {
        state: { instanceUnreachable: true, isUnreachableTerminal: true },
        retryProbe: retry,
      },
    }
    act(() => {
      rerender(<PremiumNotificationManager />)
    })

    // The old snackbar must have been closed before the new one was enqueued.
    expect(mockCloseSnackbar).toHaveBeenCalledWith(firstKey)
    // And a second snackbar enqueued — this one must carry an action fn.
    expect(snackbarLog).toHaveLength(2)
    const terminalCall = snackbarLog[1]
    expect(terminalCall.message).toMatch(/unresponsive/i)
    expect(typeof terminalCall.options?.action).toBe("function")

    // Render the action and click the Retry button.
    const actionEl = terminalCall.options!.action!(terminalCall.key)
    const { getByRole } = render(<>{actionEl}</>)
    fireEvent.click(getByRole("button", { name: /retry/i }))

    expect(mockCloseSnackbar).toHaveBeenCalledWith(terminalCall.key)
    expect(retry).toHaveBeenCalledTimes(1)
  })

  test("unreachable → reachable: dismisses with reason=reachable", () => {
    setCtx({
      unreachable: {
        state: { instanceUnreachable: true, isUnreachableTerminal: false },
        retryProbe: jest.fn(),
      },
    })

    const { rerender } = render(<PremiumNotificationManager />)
    expect(snackbarLog).toHaveLength(1)
    mockLogPremiumUiEvent.mockClear()

    mockCtxValue = {
      ...mockCtxValue,
      unreachable: {
        state: { instanceUnreachable: false, isUnreachableTerminal: false },
        retryProbe: jest.fn(),
      },
    }
    act(() => {
      rerender(<PremiumNotificationManager />)
    })

    expect(mockLogPremiumUiEvent).toHaveBeenCalledWith(
      "instance_unreachable_popup_dismissed",
      expect.objectContaining({
        reason: "reachable",
        instance_id: "inst-A",
      }),
    )
  })

  test("dedicated → shared while unreachable: dismisses with reason=not_dedicated", () => {
    setCtx({
      unreachable: {
        state: { instanceUnreachable: true, isUnreachableTerminal: false },
        retryProbe: jest.fn(),
      },
    })

    const { rerender } = render(<PremiumNotificationManager />)
    mockLogPremiumUiEvent.mockClear()

    mockCtxValue = {
      ...mockCtxValue,
      assignmentResult: {
        instance_id: "inst-A",
        assigned: true,
        is_shared: true, // flipped to shared
      },
    }
    act(() => {
      rerender(<PremiumNotificationManager />)
    })

    expect(mockLogPremiumUiEvent).toHaveBeenCalledWith(
      "instance_unreachable_popup_dismissed",
      expect.objectContaining({ reason: "not_dedicated" }),
    )
  })

  test("premium → non-premium while unreachable: dismisses with reason=not_premium", () => {
    setCtx({
      unreachable: {
        state: { instanceUnreachable: true, isUnreachableTerminal: false },
        retryProbe: jest.fn(),
      },
    })

    const { rerender } = render(<PremiumNotificationManager />)
    mockLogPremiumUiEvent.mockClear()

    mockCtxValue = { ...mockCtxValue, isPremiumUser: false }
    act(() => {
      rerender(<PremiumNotificationManager />)
    })

    expect(mockLogPremiumUiEvent).toHaveBeenCalledWith(
      "instance_unreachable_popup_dismissed",
      expect.objectContaining({ reason: "not_premium" }),
    )
  })
})

/**
 * Tests for Sleep Detection Hook (Cases 50-51)
 *
 * Tests verify that the hook correctly detects when a device wakes from sleep
 * by monitoring interval timing gaps.
 */

import {
  describe,
  it,
  expect,
  jest,
  beforeEach,
  afterEach,
} from "@jest/globals"
import { renderHook, act } from "@testing-library/react"

import { useSleepDetection } from "hooks/useSleepDetection"

describe("useSleepDetection (Cases 50-51)", () => {
  let currentTime: number

  beforeEach(() => {
    currentTime = 1000000
    jest.useFakeTimers()
    jest.spyOn(Date, "now").mockImplementation(() => currentTime)
  })

  afterEach(() => {
    jest.useRealTimers()
    jest.restoreAllMocks()
  })

  const advanceTime = (ms: number) => {
    currentTime += ms
    jest.advanceTimersByTime(ms)
  }

  it("should not call onWake during normal interval ticks", () => {
    const onWake = jest.fn()
    renderHook(() => useSleepDetection(onWake))

    act(() => {
      advanceTime(10000)
    })

    expect(onWake).not.toHaveBeenCalled()
  })

  it("should call onWake when interval fires late (simulating sleep)", () => {
    const onWake = jest.fn()
    renderHook(() => useSleepDetection(onWake))

    act(() => {
      // Simulate sleep: advance Date.now() by more than the interval fires
      currentTime += 25000
      jest.advanceTimersByTime(10000)
    })

    expect(onWake).toHaveBeenCalledTimes(1)
  })

  it("should call onWake multiple times for multiple sleep events", () => {
    const onWake = jest.fn()
    renderHook(() => useSleepDetection(onWake))

    // First sleep event
    act(() => {
      currentTime += 25000
      jest.advanceTimersByTime(10000)
    })
    expect(onWake).toHaveBeenCalledTimes(1)

    // Normal tick - no wake
    act(() => {
      advanceTime(10000)
    })
    expect(onWake).toHaveBeenCalledTimes(1)

    // Second sleep event
    act(() => {
      currentTime += 25000
      jest.advanceTimersByTime(10000)
    })
    expect(onWake).toHaveBeenCalledTimes(2)
  })

  it("should use custom check interval when provided", () => {
    const onWake = jest.fn()
    renderHook(() => useSleepDetection(onWake, { checkIntervalMs: 5000 }))

    // Normal tick at 5s interval
    act(() => {
      advanceTime(5000)
    })
    expect(onWake).not.toHaveBeenCalled()

    // Sleep event - time jumped more than 2x the 5s interval (>10s)
    act(() => {
      currentTime += 15000
      jest.advanceTimersByTime(5000)
    })
    expect(onWake).toHaveBeenCalled()
  })

  it("should use custom sleep threshold multiplier when provided", () => {
    const onWake = jest.fn()
    renderHook(() =>
      useSleepDetection(onWake, {
        checkIntervalMs: 10000,
        sleepThresholdMultiplier: 3,
      }),
    )

    // With 3x multiplier, threshold is 30s. 25s elapsed should not trigger.
    act(() => {
      currentTime += 25000
      jest.advanceTimersByTime(10000)
    })
    expect(onWake).not.toHaveBeenCalled()

    // 35s elapsed should trigger (> 30s threshold)
    act(() => {
      currentTime += 35000
      jest.advanceTimersByTime(10000)
    })
    expect(onWake).toHaveBeenCalled()
  })

  it("should not run when disabled", () => {
    const onWake = jest.fn()
    renderHook(() => useSleepDetection(onWake, { enabled: false }))

    act(() => {
      currentTime += 60000
      jest.advanceTimersByTime(60000)
    })

    expect(onWake).not.toHaveBeenCalled()
  })

  it("should cleanup interval on unmount", () => {
    const clearIntervalSpy = jest.spyOn(global, "clearInterval")
    const onWake = jest.fn()
    const { unmount } = renderHook(() => useSleepDetection(onWake))

    unmount()

    expect(clearIntervalSpy).toHaveBeenCalled()
    clearIntervalSpy.mockRestore()
  })

  it("should update onWake callback when it changes", () => {
    const onWake1 = jest.fn()
    const onWake2 = jest.fn()

    const { rerender } = renderHook(
      ({ callback }) => useSleepDetection(callback),
      { initialProps: { callback: onWake1 } },
    )

    rerender({ callback: onWake2 })

    // Simulate sleep event
    act(() => {
      currentTime += 25000
      jest.advanceTimersByTime(10000)
    })

    expect(onWake1).not.toHaveBeenCalled()
    expect(onWake2).toHaveBeenCalled()
  })
})

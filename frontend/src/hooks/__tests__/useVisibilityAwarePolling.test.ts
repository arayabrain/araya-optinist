import { act, renderHook } from "@testing-library/react"

import { useVisibilityAwarePolling } from "hooks/useVisibilityAwarePolling"

describe("useVisibilityAwarePolling", () => {
  let originalHidden: boolean

  beforeEach(() => {
    jest.useFakeTimers()
    originalHidden = document.hidden
    Object.defineProperty(document, "hidden", {
      value: false,
      writable: true,
      configurable: true,
    })
  })

  afterEach(() => {
    jest.useRealTimers()
    Object.defineProperty(document, "hidden", {
      value: originalHidden,
      writable: true,
      configurable: true,
    })
  })

  it("should poll at specified interval", () => {
    const pollFn = jest.fn()

    renderHook(() =>
      useVisibilityAwarePolling(pollFn, { interval: 1000, immediate: false }),
    )

    expect(pollFn).not.toHaveBeenCalled()

    act(() => {
      jest.advanceTimersByTime(1000)
    })

    expect(pollFn).toHaveBeenCalledTimes(1)

    act(() => {
      jest.advanceTimersByTime(1000)
    })

    expect(pollFn).toHaveBeenCalledTimes(2)
  })

  it("should poll immediately when immediate=true", () => {
    const pollFn = jest.fn()

    renderHook(() =>
      useVisibilityAwarePolling(pollFn, { interval: 1000, immediate: true }),
    )

    expect(pollFn).toHaveBeenCalledTimes(1)
  })

  it("should not poll when enabled=false", () => {
    const pollFn = jest.fn()

    renderHook(() =>
      useVisibilityAwarePolling(pollFn, { interval: 1000, enabled: false }),
    )

    act(() => {
      jest.advanceTimersByTime(5000)
    })

    expect(pollFn).not.toHaveBeenCalled()
  })

  it("should stop polling when tab is hidden", () => {
    const pollFn = jest.fn()

    renderHook(() => useVisibilityAwarePolling(pollFn, { interval: 1000 }))

    act(() => {
      jest.advanceTimersByTime(1000)
    })

    expect(pollFn).toHaveBeenCalledTimes(1)

    act(() => {
      Object.defineProperty(document, "hidden", { value: true })
      document.dispatchEvent(new Event("visibilitychange"))
    })

    act(() => {
      jest.advanceTimersByTime(5000)
    })

    expect(pollFn).toHaveBeenCalledTimes(1)
  })

  it("should resume polling when tab becomes visible", () => {
    const pollFn = jest.fn()

    renderHook(() =>
      useVisibilityAwarePolling(pollFn, {
        interval: 1000,
        visibilityDebounce: 0,
      }),
    )

    act(() => {
      Object.defineProperty(document, "hidden", { value: true })
      document.dispatchEvent(new Event("visibilitychange"))
    })

    act(() => {
      Object.defineProperty(document, "hidden", { value: false })
      document.dispatchEvent(new Event("visibilitychange"))
    })

    expect(pollFn).toHaveBeenCalled()
  })

  it("should respect visibilityDebounce", () => {
    const pollFn = jest.fn()

    renderHook(() =>
      useVisibilityAwarePolling(pollFn, {
        interval: 10000,
        visibilityDebounce: 5000,
        immediate: true,
      }),
    )

    expect(pollFn).toHaveBeenCalledTimes(1)

    act(() => {
      Object.defineProperty(document, "hidden", { value: true })
      document.dispatchEvent(new Event("visibilitychange"))
    })

    act(() => {
      jest.advanceTimersByTime(1000)
    })

    act(() => {
      Object.defineProperty(document, "hidden", { value: false })
      document.dispatchEvent(new Event("visibilitychange"))
    })

    expect(pollFn).toHaveBeenCalledTimes(1)
  })

  it("should provide pause and resume functions", () => {
    const pollFn = jest.fn()

    const { result } = renderHook(() =>
      useVisibilityAwarePolling(pollFn, { interval: 1000 }),
    )

    expect(result.current.isPaused).toBe(false)

    act(() => {
      result.current.pause()
    })

    expect(result.current.isPaused).toBe(true)

    act(() => {
      jest.advanceTimersByTime(5000)
    })

    expect(pollFn).not.toHaveBeenCalled()

    act(() => {
      result.current.resume()
    })

    expect(result.current.isPaused).toBe(false)
  })

  it("should provide pollNow function", async () => {
    const pollFn = jest.fn().mockResolvedValue(undefined)

    const { result } = renderHook(() =>
      useVisibilityAwarePolling(pollFn, { interval: 10000 }),
    )

    await act(async () => {
      await result.current.pollNow()
    })

    expect(pollFn).toHaveBeenCalledTimes(1)
  })

  it("should report visibility state", () => {
    const pollFn = jest.fn()

    const { result } = renderHook(() =>
      useVisibilityAwarePolling(pollFn, { interval: 1000 }),
    )

    expect(result.current.isVisible).toBe(true)

    act(() => {
      Object.defineProperty(document, "hidden", { value: true })
      document.dispatchEvent(new Event("visibilitychange"))
    })

    expect(result.current.isVisible).toBe(false)
  })

  it("should handle poll function errors gracefully", async () => {
    const consoleSpy = jest.spyOn(console, "error").mockImplementation()
    const pollFn = jest.fn().mockRejectedValue(new Error("Poll error"))

    const { result } = renderHook(() =>
      useVisibilityAwarePolling(pollFn, { interval: 1000 }),
    )

    await act(async () => {
      await result.current.pollNow()
    })

    expect(consoleSpy).toHaveBeenCalled()
    consoleSpy.mockRestore()
  })
})

import {
  describe,
  it,
  expect,
  beforeEach,
  afterEach,
  jest,
} from "@jest/globals"

import {
  initChunkReloadHandler,
  isChunkLoadError,
  triggerChunkReload,
  _resetForTesting,
} from "utils/chunkLoadReload"

describe("isChunkLoadError", () => {
  it("matches a webpack-style ChunkLoadError instance", () => {
    const error = new Error("Loading chunk 42 failed.")
    error.name = "ChunkLoadError"
    expect(isChunkLoadError(error)).toBe(true)
  })

  it("matches by message even when name is generic", () => {
    expect(isChunkLoadError(new Error("Loading chunk abc.def failed."))).toBe(
      true,
    )
  })

  it("matches CSS chunk load failures", () => {
    expect(isChunkLoadError(new Error("Loading CSS chunk 12 failed."))).toBe(
      true,
    )
  })

  it("matches a raw string message", () => {
    expect(isChunkLoadError("Loading chunk main failed.")).toBe(true)
    expect(isChunkLoadError("ChunkLoadError: oops")).toBe(true)
  })

  it("does not match unrelated errors", () => {
    expect(isChunkLoadError(new Error("Network Error"))).toBe(false)
    expect(isChunkLoadError("Something else")).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
    expect(isChunkLoadError(undefined)).toBe(false)
    expect(isChunkLoadError(42)).toBe(false)
    expect(isChunkLoadError({})).toBe(false)
  })
})

describe("triggerChunkReload", () => {
  let reloadMock: ReturnType<typeof jest.fn>

  let originalLocation: Location

  beforeEach(() => {
    _resetForTesting()
    sessionStorage.clear()
    originalLocation = window.location
    reloadMock = jest.fn()
    Object.defineProperty(window, "location", {
      value: { reload: reloadMock },
      writable: true,
      configurable: true,
    })
  })

  afterEach(() => {
    _resetForTesting()
    sessionStorage.clear()
    Object.defineProperty(window, "location", {
      value: originalLocation,
      writable: true,
      configurable: true,
    })
  })

  it("reloads once and sets the loop-guard flag", () => {
    const result = triggerChunkReload()
    expect(result).toBe(true)
    expect(reloadMock).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem("chunk-reload-attempted")).toBe("1")
  })

  it("does not reload again while the loop-guard flag is set", () => {
    triggerChunkReload()
    reloadMock.mockClear()

    const result = triggerChunkReload()
    expect(result).toBe(false)
    expect(reloadMock).not.toHaveBeenCalled()
  })

  it("reloads again after the flag is cleared", () => {
    triggerChunkReload()
    sessionStorage.removeItem("chunk-reload-attempted")
    reloadMock.mockClear()

    expect(triggerChunkReload()).toBe(true)
    expect(reloadMock).toHaveBeenCalledTimes(1)
  })
})

describe("initChunkReloadHandler", () => {
  let reloadMock: ReturnType<typeof jest.fn>

  let originalLocation: Location

  beforeEach(() => {
    _resetForTesting()
    sessionStorage.clear()
    originalLocation = window.location
    reloadMock = jest.fn()
    Object.defineProperty(window, "location", {
      value: { reload: reloadMock },
      writable: true,
      configurable: true,
    })
  })

  afterEach(() => {
    _resetForTesting()
    sessionStorage.clear()
    Object.defineProperty(window, "location", {
      value: originalLocation,
      writable: true,
      configurable: true,
    })
  })

  it("reloads on a window 'error' event whose error matches", () => {
    initChunkReloadHandler()
    const error = new Error("Loading chunk 7 failed.")
    error.name = "ChunkLoadError"
    window.dispatchEvent(new ErrorEvent("error", { error, message: "" }))
    expect(reloadMock).toHaveBeenCalledTimes(1)
  })

  it("reloads when only the event message matches (no error object)", () => {
    initChunkReloadHandler()
    window.dispatchEvent(
      new ErrorEvent("error", { message: "ChunkLoadError: boom" }),
    )
    expect(reloadMock).toHaveBeenCalledTimes(1)
  })

  it("ignores unrelated window 'error' events", () => {
    initChunkReloadHandler()
    window.dispatchEvent(
      new ErrorEvent("error", { error: new Error("Network Error") }),
    )
    expect(reloadMock).not.toHaveBeenCalled()
  })

  it("reloads on an unhandledrejection whose reason matches", () => {
    initChunkReloadHandler()
    const error = new Error("Loading chunk 9 failed.")
    error.name = "ChunkLoadError"
    const event = new Event("unhandledrejection") as PromiseRejectionEvent
    Object.defineProperty(event, "reason", { value: error })
    window.dispatchEvent(event)
    expect(reloadMock).toHaveBeenCalledTimes(1)
  })

  it("is idempotent across repeated init calls", () => {
    initChunkReloadHandler()
    initChunkReloadHandler()
    const error = new Error("Loading chunk 1 failed.")
    error.name = "ChunkLoadError"
    window.dispatchEvent(new ErrorEvent("error", { error }))
    expect(reloadMock).toHaveBeenCalledTimes(1)
  })

  it("does not clear the loop-guard flag immediately on 'load'", () => {
    jest.useFakeTimers()
    try {
      initChunkReloadHandler()
      sessionStorage.setItem("chunk-reload-attempted", "1")
      window.dispatchEvent(new Event("load"))
      expect(sessionStorage.getItem("chunk-reload-attempted")).toBe("1")
    } finally {
      jest.useRealTimers()
    }
  })

  it("clears the loop-guard flag after the post-load delay elapses", () => {
    jest.useFakeTimers()
    try {
      initChunkReloadHandler()
      sessionStorage.setItem("chunk-reload-attempted", "1")
      window.dispatchEvent(new Event("load"))
      jest.advanceTimersByTime(30_000)
      expect(sessionStorage.getItem("chunk-reload-attempted")).toBeNull()
    } finally {
      jest.useRealTimers()
    }
  })

  it("stops propagation on matched chunk-load rejections", () => {
    initChunkReloadHandler()
    const downstream = jest.fn()
    window.addEventListener("unhandledrejection", downstream)
    try {
      const error = new Error("Loading chunk 5 failed.")
      error.name = "ChunkLoadError"
      const event = new Event("unhandledrejection") as PromiseRejectionEvent
      Object.defineProperty(event, "reason", { value: error })
      window.dispatchEvent(event)
      expect(reloadMock).toHaveBeenCalledTimes(1)
      expect(downstream).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener("unhandledrejection", downstream)
    }
  })

  it("removes its window listeners on _resetForTesting", () => {
    initChunkReloadHandler()
    _resetForTesting()
    // Sink absorbs the event so jsdom doesn't surface it as uncaught.
    const sink = jest.fn()
    window.addEventListener("error", sink)
    try {
      const error = new Error("Loading chunk 8 failed.")
      error.name = "ChunkLoadError"
      window.dispatchEvent(new ErrorEvent("error", { error }))
      expect(reloadMock).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener("error", sink)
    }
  })
})

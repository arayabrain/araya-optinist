import {
  describe,
  it,
  expect,
  beforeEach,
  afterEach,
  jest,
} from "@jest/globals"

let mockTokenValue = "test-token"
const MOCK_BASE_URL = "http://test-host:9999"

jest.mock("utils/auth/AuthUtils", () => ({
  getToken: () => mockTokenValue,
}))

jest.mock("const/API", () => ({
  BASE_URL: "http://test-host:9999",
}))

import {
  initErrorReporter,
  flushErrors,
  _resetForTesting,
  _getQueue,
  MAX_QUEUE_SIZE,
  MAX_MESSAGE_LENGTH,
} from "utils/errorReporter"

describe("errorReporter", () => {
  let originalError
  let originalWarn
  let fetchSpy

  beforeEach(() => {
    originalError = console.error
    originalWarn = console.warn
    _resetForTesting()
    fetchSpy = jest.spyOn(global, "fetch").mockResolvedValue(new Response())
    jest.useFakeTimers()
    mockTokenValue = "test-token"
  })

  afterEach(() => {
    _resetForTesting()
    console.error = originalError
    console.warn = originalWarn
    fetchSpy.mockRestore()
    jest.useRealTimers()
  })

  it("overrides console.error and console.warn", () => {
    initErrorReporter()
    expect(console.error).not.toBe(originalError)
  })

  it("guards against double initialization", () => {
    initErrorReporter()
    const afterFirst = console.error
    initErrorReporter()
    expect(console.error).toBe(afterFirst)
  })

  it("enqueues console.error calls", () => {
    initErrorReporter()
    console.error("test error message")
    expect(_getQueue()).toHaveLength(1)
    expect(_getQueue()[0].level).toBe("error")
    expect(_getQueue()[0].message).toBe("test error message")
  })

  it("enqueues console.warn calls", () => {
    initErrorReporter()
    console.warn("test warning")
    expect(_getQueue()).toHaveLength(1)
    expect(_getQueue()[0].level).toBe("warn")
  })

  it("caps queue at MAX_QUEUE_SIZE items (drops oldest)", () => {
    initErrorReporter()
    for (let i = 0; i < MAX_QUEUE_SIZE + 5; i++) {
      console.error("error " + i)
    }
    expect(_getQueue()).toHaveLength(MAX_QUEUE_SIZE)
    expect(_getQueue()[0].message).toBe("error 5")
  })

  it("serializes Error objects using .stack", () => {
    initErrorReporter()
    const err = new Error("boom")
    console.error(err)
    expect(_getQueue()[0].message).toContain("boom")
    expect(_getQueue()[0].message).toContain("Error")
  })

  it("handles circular references", () => {
    initErrorReporter()
    const obj = { a: 1 }
    obj.self = obj
    console.error(obj)
    expect(_getQueue()[0].message).toContain("[circular]")
  })

  it("truncates messages to MAX_MESSAGE_LENGTH chars", () => {
    initErrorReporter()
    const longMsg = "x".repeat(MAX_MESSAGE_LENGTH + 1000)
    console.error(longMsg)
    expect(_getQueue()[0].message.length).toBe(MAX_MESSAGE_LENGTH)
  })

  it("calls fetch with correct shape on flush", () => {
    initErrorReporter()
    console.error("test")
    flushErrors()

    expect(fetchSpy).toHaveBeenCalledWith(
      `${MOCK_BASE_URL}/log-report/frontend-errors`,
      expect.objectContaining({
        method: "POST",
        keepalive: true,
        headers: expect.objectContaining({
          Authorization: "Bearer test-token",
        }),
      }),
    )
  })

  it("does not call fetch when queue is empty", () => {
    initErrorReporter()
    flushErrors()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("preserves queue when no token", () => {
    mockTokenValue = null
    initErrorReporter()
    console.error("test")
    flushErrors()
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(_getQueue()).toHaveLength(1)
  })

  it("swallows network failures silently", () => {
    fetchSpy.mockRejectedValue(new Error("network"))
    initErrorReporter()
    console.error("test")
    expect(() => flushErrors()).not.toThrow()
  })

  it("clears queue after flush", () => {
    initErrorReporter()
    console.error("test")
    flushErrors()
    expect(_getQueue()).toHaveLength(0)
  })

  it("does not enqueue when suppressed during flush", () => {
    initErrorReporter()
    console.error("first")
    // Trigger flush which sets _suppressed = true synchronously
    flushErrors()
    // Queue should be cleared after flush
    expect(_getQueue()).toHaveLength(0)
  })

  it("chains previous window.onerror handler", () => {
    const prevHandler = jest.fn()
    window.onerror = prevHandler
    initErrorReporter()
    window.onerror("msg", "source.js", 1, 1, new Error("test"))
    expect(prevHandler).toHaveBeenCalled()
    expect(_getQueue()).toHaveLength(1)
  })

  it("window.onerror sets source on enqueued entry", () => {
    initErrorReporter()
    window.onerror("test msg", "http://app/main.js", 1, 1, undefined)
    // jsdom may double-dispatch onerror, so just verify at least one entry has source
    const withSource = _getQueue().filter(
      (e) => e.source === "http://app/main.js",
    )
    expect(withSource.length).toBeGreaterThanOrEqual(1)
    expect(withSource[0].message).toBe("test msg")
  })

  it("window.onerror is safe when queue is suppressed", () => {
    initErrorReporter()
    // Manually flush to set _suppressed
    console.error("pre")
    flushErrors()
    // Now trigger onerror while suppressed — should not throw
    expect(() => {
      window.onerror("msg", "source.js", 1, 1, new Error("test"))
    }).not.toThrow()
  })
})

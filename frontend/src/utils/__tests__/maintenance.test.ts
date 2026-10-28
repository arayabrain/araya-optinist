import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  jest,
} from "@jest/globals"

import {
  MAINTENANCE_MARKER,
  MAINTENANCE_RELOAD_KEY,
  reloadIfUnderMaintenance,
} from "utils/maintenance"

const mockReload = jest.fn()
const originalLocation = window.location
const originalFetch = global.fetch

const respond = (status: number, body: string) =>
  jest.fn(async () => ({ status, text: async () => body }) as Response)

describe("reloadIfUnderMaintenance", () => {
  beforeEach(() => {
    sessionStorage.clear()
    mockReload.mockClear()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, reload: mockReload },
    })
  })

  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    })
    global.fetch = originalFetch
  })

  it("reloads on a same-origin 503 that carries the marker", async () => {
    global.fetch = respond(503, `<html>${MAINTENANCE_MARKER}</html>`)
    await expect(reloadIfUnderMaintenance()).resolves.toBe(true)
    expect(global.fetch).toHaveBeenCalledWith("/", { cache: "no-store" })
    expect(mockReload).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem(MAINTENANCE_RELOAD_KEY)).not.toBeNull()
  })

  it("ignores a 503 without the marker", async () => {
    global.fetch = respond(503, "<html>Service Unavailable</html>")
    await expect(reloadIfUnderMaintenance()).resolves.toBe(false)
    expect(mockReload).not.toHaveBeenCalled()
  })

  it("ignores a healthy site", async () => {
    global.fetch = respond(200, `<html>${MAINTENANCE_MARKER}</html>`)
    await expect(reloadIfUnderMaintenance()).resolves.toBe(false)
    expect(mockReload).not.toHaveBeenCalled()
  })

  it("ignores a failed probe", async () => {
    global.fetch = jest.fn(async () => {
      throw new TypeError("offline")
    }) as typeof fetch
    await expect(reloadIfUnderMaintenance()).resolves.toBe(false)
    expect(mockReload).not.toHaveBeenCalled()
  })

  it("does not reload twice within a minute", async () => {
    sessionStorage.setItem(MAINTENANCE_RELOAD_KEY, String(Date.now()))
    global.fetch = respond(503, MAINTENANCE_MARKER)
    await expect(reloadIfUnderMaintenance()).resolves.toBe(false)
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it("reloads again after a minute", async () => {
    sessionStorage.setItem(MAINTENANCE_RELOAD_KEY, String(Date.now() - 61_000))
    global.fetch = respond(503, MAINTENANCE_MARKER)
    await expect(reloadIfUnderMaintenance()).resolves.toBe(true)
  })

  it("shares one probe between concurrent callers", async () => {
    global.fetch = respond(200, "")
    await Promise.all([
      reloadIfUnderMaintenance(),
      reloadIfUnderMaintenance(),
      reloadIfUnderMaintenance(),
    ])
    expect(global.fetch).toHaveBeenCalledTimes(1)
  })
})

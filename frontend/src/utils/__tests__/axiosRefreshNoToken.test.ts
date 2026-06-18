/**
 * Integration: no stored refresh token -> single clean logout, no network.
 *
 * Exercises the REAL refreshTokenApi short-circuit through the REAL axios
 * interceptor (only AuthUtils is mocked). Pins that a missing refresh token
 * triggers exactly one logout and never posts to /auth/refresh, and that the
 * interceptor does not fire a second logout off the non-Axios error that the
 * short-circuit throws.
 */

import { beforeEach, describe, expect, it, jest } from "@jest/globals"

const mockGetRefreshToken = jest.fn(() => null as string | null)
const mockGetToken = jest.fn(() => "access-token" as string | null)
const mockGetExToken = jest.fn(() => null as string | null)
const mockLogout = jest.fn()
const mockSaveToken = jest.fn()
const mockGetRoutingHeaders = jest.fn<Record<string, string>, []>(() => ({}))
const mockUpdateRoutingToken = jest.fn<void, [string]>()
const mockRequiresPremiumRouting = jest.fn<boolean, []>(() => false)
const mockIsDataviewPublicOutputsRequest = jest.fn<boolean, [string]>(
  () => false,
)

// Deliberately NOT mocking "api/auth/Auth" or "utils/axios": both run for real
// so the short-circuit and the interceptor are exercised end to end.
jest.mock("utils/auth/AuthUtils", () => ({
  getRefreshToken: mockGetRefreshToken,
  getToken: mockGetToken,
  getExToken: mockGetExToken,
  logout: mockLogout,
  saveToken: mockSaveToken,
}))
jest.mock("utils/routing/RoutingService", () => ({
  routingService: {
    getRoutingHeaders: mockGetRoutingHeaders,
    updateRoutingToken: mockUpdateRoutingToken,
    requiresPremiumRouting: mockRequiresPremiumRouting,
  },
}))
jest.mock("utils/DataviewUtils", () => ({
  isDataviewPublicOutputsRequest: mockIsDataviewPublicOutputsRequest,
  DATAVIEW_PUBLIC_REQUEST_KEY: "x-dataview-public",
}))

type AdapterResponse = { status: number; data?: unknown }
let responses: Map<string, AdapterResponse> = new Map()
let requestedUrls: string[] = []

const installAdapter = (instance: { defaults: { adapter: unknown } }): void => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  instance.defaults.adapter = async (config: any) => {
    const url = config.url ?? ""
    requestedUrls.push(url)
    const resolved = responses.get(url)
    if (!resolved) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const err: any = new Error(`No responder for ${url}`)
      err.config = config
      throw err
    }
    if (resolved.status >= 400) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const err: any = new Error(`HTTP ${resolved.status}`)
      err.isAxiosError = true
      err.response = {
        status: resolved.status,
        data: resolved.data ?? {},
        headers: {},
        config,
      }
      err.config = config
      throw err
    }
    return {
      data: resolved.data ?? {},
      status: resolved.status,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    }
  }
}

describe("axios 401->refresh with no stored refresh token", () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let axiosInstance: any

  beforeEach(() => {
    jest.resetModules()
    jest.clearAllMocks()
    responses = new Map()
    requestedUrls = []
    // CRA's jest config resets mock implementations before each test, so the
    // default returns must be re-established here.
    mockGetRefreshToken.mockReturnValue(null)
    mockGetToken.mockReturnValue("access-token")
    mockGetExToken.mockReturnValue(null)
    mockGetRoutingHeaders.mockReturnValue({})
    mockRequiresPremiumRouting.mockReturnValue(false)
    mockIsDataviewPublicOutputsRequest.mockReturnValue(false)
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const mod = require("utils/axios")
    axiosInstance = mod.default
    installAdapter(axiosInstance)
    responses.set("/protected", { status: 401, data: {} })
  })

  it("logs out exactly once and never posts to /auth/refresh", async () => {
    await expect(axiosInstance.get("/protected")).rejects.toBeTruthy()

    expect(mockLogout).toHaveBeenCalledTimes(1)
    expect(requestedUrls).not.toContain("/auth/refresh")
  })

  it("short-circuits when the stored token is an empty string", async () => {
    mockGetRefreshToken.mockReturnValue("")

    await expect(axiosInstance.get("/protected")).rejects.toBeTruthy()

    expect(mockLogout).toHaveBeenCalledTimes(1)
    expect(requestedUrls).not.toContain("/auth/refresh")
  })
})

/**
 * Axios 401 -> token-refresh interceptor: failure handling.
 *
 * Pins that a 422 from the refresh attempt triggers a clean logout, the same
 * as a 400/401, instead of leaving a dead token that fell through to the
 * public service and produced a 404/405 error cascade. A 5xx from the refresh
 * attempt must NOT force logout (it is treated as transient).
 */

import { beforeEach, describe, expect, it, jest } from "@jest/globals"

const mockRefreshTokenApi = jest.fn() as unknown as jest.Mock<
  Promise<{ access_token: string }>
>
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

jest.mock("api/auth/Auth", () => ({ refreshTokenApi: mockRefreshTokenApi }))
jest.mock("utils/auth/AuthUtils", () => ({
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

const installAdapter = (instance: { defaults: { adapter: unknown } }): void => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  instance.defaults.adapter = async (config: any) => {
    const resolved = responses.get(config.url ?? "")
    if (!resolved) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const err: any = new Error(`No responder for ${config.url}`)
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const refreshError = (status: number): any => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const err: any = new Error(`refresh failed ${status}`)
  err.isAxiosError = true
  err.response = { status, data: {} }
  return err
}

describe("axios 401->refresh failure handling", () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let axiosInstance: any

  beforeEach(() => {
    jest.resetModules()
    jest.clearAllMocks()
    responses = new Map()
    // CRA's jest config resets mock implementations before each test, so the
    // default returns must be re-established here.
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

  it("logs out when the refresh attempt fails with 422 (parity with 400/401)", async () => {
    mockRefreshTokenApi.mockRejectedValue(refreshError(422))

    await expect(axiosInstance.get("/protected")).rejects.toBeTruthy()
    expect(mockLogout).toHaveBeenCalledTimes(1)
  })

  it("logs out when the refresh attempt fails with 401", async () => {
    mockRefreshTokenApi.mockRejectedValue(refreshError(401))

    await expect(axiosInstance.get("/protected")).rejects.toBeTruthy()
    expect(mockLogout).toHaveBeenCalledTimes(1)
  })

  it("logs out when the refresh attempt fails with 400", async () => {
    mockRefreshTokenApi.mockRejectedValue(refreshError(400))

    await expect(axiosInstance.get("/protected")).rejects.toBeTruthy()
    expect(mockLogout).toHaveBeenCalledTimes(1)
  })

  it("does NOT log out when the refresh attempt fails with a transient 500", async () => {
    mockRefreshTokenApi.mockRejectedValue(refreshError(500))

    await expect(axiosInstance.get("/protected")).rejects.toBeTruthy()
    expect(mockLogout).not.toHaveBeenCalled()
  })
})

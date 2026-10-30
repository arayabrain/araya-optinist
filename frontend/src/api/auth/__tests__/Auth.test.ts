import { describe, it, expect, beforeEach, jest } from "@jest/globals"

import { BASE_URL } from "const/API"

const mockPost = jest.fn() as unknown as jest.Mock<
  Promise<{ data: { access_token: string } }>
>
const mockGetRefreshToken = jest.fn<string | null, []>()
const mockRemoveRefreshToken = jest.fn()

jest.mock("axios", () => ({
  __esModule: true,
  default: {
    post: mockPost,
    isAxiosError: (e: { isAxiosError?: boolean }) => !!e?.isAxiosError,
  },
}))

const httpError = (status: number) => ({
  isAxiosError: true,
  response: { status },
})

jest.mock("utils/axios", () => ({
  __esModule: true,
  default: { post: mockPost },
}))

jest.mock("utils/auth/AuthUtils", () => ({
  getRefreshToken: mockGetRefreshToken,
  removeRefreshToken: mockRemoveRefreshToken,
}))

const COOKIE_OPTIONS = expect.objectContaining({
  withCredentials: true,
  headers: { "X-Refresh-Cookie": "1" },
})

describe("refreshTokenApi", () => {
  beforeEach(() => {
    jest.resetModules()
    jest.clearAllMocks()
    mockPost.mockResolvedValue({ data: { access_token: "new-access-token" } })
  })

  it("refreshes from the cookie when nothing is stored", async () => {
    mockGetRefreshToken.mockReturnValue(null)
    const { refreshTokenApi } = await import("api/auth/Auth")

    await expect(refreshTokenApi()).resolves.toEqual({
      access_token: "new-access-token",
    })
    expect(mockPost).toHaveBeenCalledWith(
      `${BASE_URL}/auth/refresh`,
      {},
      COOKIE_OPTIONS,
    )
  })

  it("keeps using a token stored by an older SPA while it is valid", async () => {
    mockGetRefreshToken.mockReturnValue("legacy")
    const { refreshTokenApi } = await import("api/auth/Auth")

    await refreshTokenApi()

    expect(mockPost).toHaveBeenCalledWith(
      `${BASE_URL}/auth/refresh`,
      { refresh_token: "legacy" },
      COOKIE_OPTIONS,
    )
    expect(mockRemoveRefreshToken).not.toHaveBeenCalled()
  })

  it("keeps the stored token when the backend is unreachable or failing", async () => {
    mockGetRefreshToken.mockReturnValue("legacy")
    mockPost.mockRejectedValueOnce(new Error("network"))
    mockPost.mockRejectedValueOnce(httpError(503))
    const { refreshTokenApi } = await import("api/auth/Auth")

    await expect(refreshTokenApi()).rejects.toThrow("network")
    await expect(refreshTokenApi()).rejects.toEqual(httpError(503))
    expect(mockRemoveRefreshToken).not.toHaveBeenCalled()
    expect(mockPost).toHaveBeenCalledTimes(2)
  })

  it("falls back to the cookie when the stored token is dead", async () => {
    mockGetRefreshToken.mockReturnValue("expired")
    mockPost.mockRejectedValueOnce(httpError(400))
    const { refreshTokenApi } = await import("api/auth/Auth")

    await expect(refreshTokenApi()).resolves.toEqual({
      access_token: "new-access-token",
    })
    expect(mockRemoveRefreshToken).toHaveBeenCalledTimes(1)
    expect(mockPost).toHaveBeenLastCalledWith(
      `${BASE_URL}/auth/refresh`,
      {},
      COOKIE_OPTIONS,
    )
  })

  it("shares one request between concurrent callers", async () => {
    mockGetRefreshToken.mockReturnValue(null)
    const { refreshTokenApi } = await import("api/auth/Auth")

    await Promise.all([refreshTokenApi(), refreshTokenApi()])
    await refreshTokenApi()

    expect(mockPost).toHaveBeenCalledTimes(2)
  })
})

describe("login opts in to the refresh cookie", () => {
  beforeEach(() => {
    jest.resetModules()
    jest.clearAllMocks()
    mockPost.mockResolvedValue({ data: { access_token: "a" } })
  })

  it("on a password login", async () => {
    const { loginApi } = await import("api/auth/Auth")

    await loginApi({ email: "a@example.com", password: "pw" })

    expect(mockPost).toHaveBeenCalledWith(
      "/auth/login",
      { email: "a@example.com", password: "pw" },
      COOKIE_OPTIONS,
    )
  })

  it("on a proxy login", async () => {
    const { proxyLoginApi } = await import("api/auth/Auth")

    await proxyLoginApi("uid-1")

    expect(mockPost).toHaveBeenCalledWith(
      "/auth/proxy-login/uid-1",
      undefined,
      COOKIE_OPTIONS,
    )
  })
})

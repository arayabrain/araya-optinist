import { describe, it, expect, beforeEach, jest } from "@jest/globals"

const mockRefreshTokenApi = jest.fn() as unknown as jest.Mock<
  Promise<{ access_token: string }>
>

jest.mock("api/auth/Auth", () => ({ refreshTokenApi: mockRefreshTokenApi }))
jest.mock("utils/routing/RoutingService", () => ({ routingService: {} }))

const load = () => import("utils/auth/AuthUtils")

describe("access token storage", () => {
  beforeEach(() => {
    jest.resetModules()
    jest.clearAllMocks()
    localStorage.clear()
  })

  it("keeps the access token out of localStorage", async () => {
    const { getToken, saveToken } = await load()

    saveToken("secret")

    expect(getToken()).toBe("secret")
    expect(Object.values({ ...localStorage })).not.toContain("secret")
  })

  it("restores the session once per page load", async () => {
    mockRefreshTokenApi.mockResolvedValue({ access_token: "restored" })
    const { getToken, restoreSession } = await load()

    const tokens = await Promise.all([restoreSession(), restoreSession()])

    expect(tokens).toEqual(["restored", "restored"])
    expect(getToken()).toBe("restored")
    expect(mockRefreshTokenApi).toHaveBeenCalledTimes(1)
  })

  it("resolves null without a session and does not retry", async () => {
    mockRefreshTokenApi.mockRejectedValue({ response: { status: 401 } })
    const { restoreSession } = await load()

    expect(await restoreSession()).toBeNull()
    expect(await restoreSession()).toBeNull()
    expect(mockRefreshTokenApi).toHaveBeenCalledTimes(1)
  })

  it("asks the cookie again after a logout instead of reusing the old result", async () => {
    mockRefreshTokenApi.mockResolvedValue({ access_token: "second" })
    const { removeToken, restoreSession, saveToken } = await load()
    saveToken("first")

    removeToken()
    saveToken("relogged")
    removeToken()
    localStorage.clear()

    expect(await restoreSession()).toBe("second")
    expect(mockRefreshTokenApi).toHaveBeenCalledTimes(1)
  })

  it("does not restore after a logout, even if the cookie survived", async () => {
    mockRefreshTokenApi.mockResolvedValue({ access_token: "revived" })
    const { removeToken, restoreSession } = await load()

    removeToken()

    expect(await restoreSession()).toBeNull()
    expect(mockRefreshTokenApi).not.toHaveBeenCalled()
  })

  it("restores again once the user logs in", async () => {
    mockRefreshTokenApi.mockResolvedValue({ access_token: "restored" })
    const first = await load()
    first.removeToken()
    first.saveToken("fresh-login")
    jest.resetModules()
    const reloaded = await load()

    expect(await reloaded.restoreSession()).toBe("restored")
  })

  it("retries after a server error instead of caching no session", async () => {
    mockRefreshTokenApi.mockRejectedValueOnce({ response: { status: 503 } })
    mockRefreshTokenApi.mockResolvedValueOnce({ access_token: "recovered" })
    const { restoreSession } = await load()

    expect(await restoreSession()).toBeNull()
    expect(await restoreSession()).toBe("recovered")
  })

  it("drops an access token left in localStorage by an older SPA", async () => {
    localStorage.setItem("access_token", "legacy")
    const { removeToken } = await load()

    removeToken()

    expect(localStorage.getItem("access_token")).toBeNull()
  })

  it("does not bring a token back after a logout during the restore", async () => {
    let finish: (value: { access_token: string }) => void = () => undefined
    mockRefreshTokenApi.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const { getToken, removeToken, restoreSession } = await load()

    const pending = restoreSession()
    await Promise.resolve()
    removeToken()
    finish({ access_token: "stale" })

    expect(await pending).toBeNull()
    expect(getToken()).toBeNull()
  })
})

import { describe, it, expect, beforeEach, jest } from "@jest/globals"

/**
 * Tests for refreshTokenApi.
 *
 * When no refresh token is stored, the refresh path must short-circuit to a
 * clean logout instead of posting a null token (which the backend rejects),
 * avoiding the dead-token state that fell through to the public service.
 */

const mockPost = jest.fn() as unknown as jest.Mock<
  Promise<{ data: { access_token: string } }>
>
const mockGetRefreshToken = jest.fn<string | null, []>()
const mockLogout = jest.fn()

jest.mock("utils/axios", () => ({
  __esModule: true,
  default: { post: mockPost },
}))

jest.mock("utils/auth/AuthUtils", () => ({
  getRefreshToken: mockGetRefreshToken,
  logout: mockLogout,
}))

describe("refreshTokenApi", () => {
  beforeEach(() => {
    jest.resetModules()
    jest.clearAllMocks()
  })

  it("short-circuits to logout without a network call when no refresh token is stored", async () => {
    mockGetRefreshToken.mockReturnValue(null)

    const { refreshTokenApi } = await import("api/auth/Auth")

    await expect(refreshTokenApi()).rejects.toThrow()
    expect(mockLogout).toHaveBeenCalledTimes(1)
    expect(mockPost).not.toHaveBeenCalled()
  })

  it("posts the stored refresh token and returns the access token when present", async () => {
    mockGetRefreshToken.mockReturnValue("stored-refresh-token")
    mockPost.mockResolvedValue({ data: { access_token: "new-access-token" } })

    const { refreshTokenApi } = await import("api/auth/Auth")

    const result = await refreshTokenApi()

    expect(mockPost).toHaveBeenCalledWith("/auth/refresh", {
      refresh_token: "stored-refresh-token",
    })
    expect(result).toEqual({ access_token: "new-access-token" })
    expect(mockLogout).not.toHaveBeenCalled()
  })
})

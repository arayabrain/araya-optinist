import { describe, it, expect, jest, beforeEach } from "@jest/globals"

import { refreshAllWorkspacesStorageApi } from "api/workspace/index"
import axios from "utils/axios"


jest.mock("utils/axios")
const mockedAxios = axios as jest.Mocked<typeof axios>

describe("refreshAllWorkspacesStorageApi (Cases 40-42)", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("should call API without signal by default", async () => {
    mockedAxios.post.mockResolvedValue({
      data: {
        success: true,
        refreshed_workspaces: 3,
        total_workspaces: 5,
        message: "Refreshed successfully",
      },
    })

    const result = await refreshAllWorkspacesStorageApi()

    expect(mockedAxios.post).toHaveBeenCalledWith(
      "/workspaces/refresh-storage",
      null,
      { signal: undefined },
    )
    expect(result.success).toBe(true)
  })

  it("should pass AbortSignal to axios when provided", async () => {
    const controller = new AbortController()

    mockedAxios.post.mockResolvedValue({
      data: {
        success: true,
        refreshed_workspaces: 3,
        total_workspaces: 5,
        message: "Refreshed successfully",
      },
    })

    await refreshAllWorkspacesStorageApi({ signal: controller.signal })

    expect(mockedAxios.post).toHaveBeenCalledWith(
      "/workspaces/refresh-storage",
      null,
      { signal: controller.signal },
    )
  })

  it("should throw AbortError when signal is aborted", async () => {
    const controller = new AbortController()

    const abortError = new Error("The operation was aborted")
    abortError.name = "AbortError"

    mockedAxios.post.mockRejectedValue(abortError)

    // Abort immediately
    controller.abort()

    await expect(
      refreshAllWorkspacesStorageApi({ signal: controller.signal }),
    ).rejects.toThrow("The operation was aborted")
  })

  it("should handle successful response with correct data structure", async () => {
    mockedAxios.post.mockResolvedValue({
      data: {
        success: true,
        refreshed_workspaces: 2,
        total_workspaces: 4,
        message: "Storage refreshed for 2 of 4 workspaces",
      },
    })

    const result = await refreshAllWorkspacesStorageApi()

    expect(result).toEqual({
      success: true,
      refreshed_workspaces: 2,
      total_workspaces: 4,
      message: "Storage refreshed for 2 of 4 workspaces",
    })
  })

  it("should propagate network errors", async () => {
    mockedAxios.post.mockRejectedValue(new Error("Network error"))

    await expect(refreshAllWorkspacesStorageApi()).rejects.toThrow(
      "Network error",
    )
  })
})

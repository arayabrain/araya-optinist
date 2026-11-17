import { AxiosError } from "axios"

import { describe, expect, it } from "@jest/globals"

import {
  uploadFile,
  uploadRejectionMessage,
} from "store/slice/FileUploader/FileUploaderActions"
import reducer from "store/slice/FileUploader/FileUploaderSlice"

const arg = {
  workspaceId: 1,
  requestId: "req",
  fileName: "data.tif",
  formData: new FormData(),
}

// uploadFile rejects with the axios error as its payload (rejectWithValue)
const rejected = (status: number, detail: unknown) => {
  const error = new AxiosError(`Request failed with status code ${status}`)
  error.response = { status, data: { detail } } as AxiosError["response"]
  return uploadFile.rejected(error, "id", arg, error)
}

describe("uploadRejectionMessage", () => {
  // The URL upload dialog shows the same detail; it has no reducer case
  it("shows a 507 detail from a URL upload", () => {
    const error = new AxiosError("Request failed with status code 507")
    error.response = {
      status: 507,
      data: {
        detail: "Cannot download file: the server's disk is nearly full.",
      },
    } as AxiosError["response"]
    expect(uploadRejectionMessage(error, "Url does not exist!")).toContain(
      "disk is nearly full",
    )
  })

  it("keeps the fallback for a network error or an array detail", () => {
    expect(uploadRejectionMessage(undefined, "Url does not exist!")).toBe(
      "Url does not exist!",
    )
    const error = new AxiosError("Request failed with status code 422")
    error.response = {
      status: 422,
      data: { detail: [{ msg: "field required" }] },
    } as AxiosError["response"]
    expect(uploadRejectionMessage(error, "Url does not exist!")).toBe(
      "Url does not exist!",
    )
  })
})

describe("uploadFile.rejected", () => {
  it("shows the server's string detail (507 local disk full)", () => {
    const state = reducer(
      undefined,
      rejected(
        507,
        "Cannot upload file: the analysis server's local disk is nearly full.",
      ),
    )
    expect(state.req.error).toContain("local disk is nearly full")
    expect(state.req.pending).toBe(false)
  })

  it("falls back to the message when the detail is not a string", () => {
    // A 422 detail is an array, which the node would otherwise try to render
    const state = reducer(
      undefined,
      rejected(422, [{ loc: ["body", "file"], msg: "field required" }]),
    )
    expect(state.req.error).toBe("Request failed with status code 422")
  })
})

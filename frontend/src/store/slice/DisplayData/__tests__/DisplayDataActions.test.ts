import { AxiosError, AxiosHeaders } from "axios"

import { describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"

import { commitRoiApi, getStatusRoi } from "api/visualizations/Outputs"
import {
  commitRoi,
  getDisplayErrorMessage,
  getStatus,
  RejectPayload,
} from "store/slice/DisplayData/DisplayDataActions"
import reducer from "store/slice/DisplayData/DisplayDataSlice"

jest.mock("api/visualizations/Outputs")

describe("getStatus", () => {
  it("rejects with the status and detail when the experiment is locked", async () => {
    const error = new AxiosError("Request failed with status code 423")
    error.response = {
      status: 423,
      statusText: "Locked",
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: { detail: "Remote data is temporary locked. [1/uid]" },
    }
    jest.mocked(getStatusRoi).mockRejectedValue(error)
    const store = configureStore({ reducer: { displayData: reducer } })

    const result = await store.dispatch(
      getStatus({ path: "/p", workspaceId: 1 }),
    )

    expect(result.payload).toEqual({
      status: 423,
      message: "Remote data is temporary locked. [1/uid]",
    })
    expect(store.getState().displayData.loading).toBe(false)
  })
})

describe("commitRoi", () => {
  it("rejects with the server's reason when the movie is unavailable", async () => {
    const detail =
      "The movie this node read is missing (mc_images.tif). Run the workflow again"
    const error = new AxiosError("Request failed with status code 409")
    error.response = {
      status: 409,
      statusText: "Conflict",
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: { detail },
    }
    jest.mocked(commitRoiApi).mockRejectedValue(error)
    const store = configureStore({ reducer: { displayData: reducer } })

    const result = await store.dispatch(
      commitRoi({ path: "/p", workspaceId: 1 }),
    )

    expect(result.payload).toEqual({ status: 409, message: detail })
    // what ImagePlot's snackbar shows
    expect(
      getDisplayErrorMessage(
        result.payload as RejectPayload,
        "Failed to commit Edit ROI.",
      ),
    ).toBe(detail)
    expect(store.getState().displayData.loading).toBe(false)
  })
})

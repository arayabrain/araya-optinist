import { AxiosError, AxiosHeaders } from "axios"

import { describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"

import { getStatusRoi } from "api/visualizations/Outputs"
import { getStatus } from "store/slice/DisplayData/DisplayDataActions"
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

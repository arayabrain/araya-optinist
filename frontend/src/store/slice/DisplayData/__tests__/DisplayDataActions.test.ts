import { AxiosError, AxiosHeaders } from "axios"

import { describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"

import {
  getStatusRoi,
  getTimeSeriesDataByIdApi,
  getTimeSeriesInitDataApi,
} from "api/visualizations/Outputs"
import {
  getStatus,
  getTimeSeriesDataById,
  getTimeSeriesInitData,
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

describe("getTimeSeriesInitData", () => {
  const path = "/p"
  const xrange = ["0", "1", "2"]
  const record = (a: number) => ({ "0": a, "1": a + 1, "2": a + 2 })

  it("refetches a record another item loaded in full instead of reusing it", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "0": record(1), "1": { "0": 1 }, "2": { "0": 1 } },
      xrange,
      std: {},
    })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockResolvedValueOnce({ data: { "1": record(4) }, xrange, std: {} })
      // the output was rewritten in between, e.g. by a filter
      .mockResolvedValueOnce({
        data: { "1": record(7) },
        xrange,
        std: { "1": record(0) },
      })
    const store = configureStore({ reducer: { displayData: reducer } })

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 1 }))

    const entry = store.getState().displayData.timeSeries[path]
    expect(entry.data["1"]).toEqual(record(7))
    expect(entry.std["1"]).toEqual(record(0))
    expect(entry.data["2"]).toEqual({ "0": 1 })
    expect(entry.fulfilled).toBe(true)
    expect(jest.mocked(getTimeSeriesDataByIdApi).mock.calls).toEqual([
      [path, "1", undefined],
      [path, "1", undefined],
    ])
  })

  const initWithStubs = {
    data: { "0": record(1), "1": { "0": 1 } },
    xrange,
    std: {},
  }

  it("refetches a record a by-id request filled while the init was in flight", async () => {
    let resolveInit: (value: unknown) => void = () => undefined
    jest
      .mocked(getTimeSeriesInitDataApi)
      .mockResolvedValueOnce(initWithStubs)
      .mockReturnValueOnce(new Promise((r) => (resolveInit = r)) as never)
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockResolvedValueOnce({ data: { "1": record(4) }, xrange, std: {} })
      .mockResolvedValueOnce({ data: { "1": record(5) }, xrange, std: {} })
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))

    const init = store.dispatch(getTimeSeriesInitData({ path, itemId: 1 }))
    await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))
    resolveInit(initWithStubs)
    await init

    expect(store.getState().displayData.timeSeries[path].data["1"]).toEqual(
      record(5),
    )
  })

  it("keeps the series already held when its refetch fails", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue(initWithStubs)
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockResolvedValueOnce({ data: { "1": record(4) }, xrange, std: {} })
      .mockRejectedValueOnce(new Error("503"))
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 1 }))

    const entry = store.getState().displayData.timeSeries[path]
    expect(entry.error).toBeNull()
    expect(entry.data["0"]).toEqual(record(1))
    expect(entry.data["1"]).toEqual(record(4))
  })
})

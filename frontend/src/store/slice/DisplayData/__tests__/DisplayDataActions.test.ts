import { AxiosError, AxiosHeaders } from "axios"

import { describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"

import {
  getStatusRoi,
  getTimeSeriesAllDataApi,
  getTimeSeriesDataByIdApi,
  getTimeSeriesInitDataApi,
} from "api/visualizations/Outputs"
import {
  getStatus,
  getTimeSeriesAllData,
  getTimeSeriesDataById,
  getTimeSeriesDataByIdOnce,
  getTimeSeriesInitData,
  retryRejectedTimeSeries,
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

describe("getTimeSeriesDataByIdOnce", () => {
  const path = "/p"
  const xrange = ["0", "1"]

  it("requests a record once however many plots ask for it", async () => {
    let resolve: (value: unknown) => void = () => undefined
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockReturnValue(new Promise((r) => (resolve = r)) as never)
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "3": { "0": 0 } },
      xrange,
      std: {},
    })
    const store = configureStore({ reducer: { displayData: reducer } })
    const byId = () => store.getState().displayData.timeSeries[path].byId

    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "3" }))
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "3" }))
    expect(byId()).toEqual({ "3": "pending" })
    resolve({ data: { "3": { "0": 0, "1": 1 } }, xrange, std: {} })
    await new Promise(process.nextTick)
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "3" }))

    expect(getTimeSeriesDataByIdApi).toHaveBeenCalledTimes(1)
    expect(byId()).toEqual({ "3": "fulfilled" })

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    expect(byId()).toEqual({})
  })

  it("marks the records an init or all-data response holds in full", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "0": { "0": 1, "1": 2 }, "3": { "0": 0 } },
      xrange,
      std: {},
    })
    jest.mocked(getTimeSeriesAllDataApi).mockResolvedValue({
      data: { "0": { "0": 1, "1": 2 }, "3": { "0": 0, "1": 1 } },
      xrange,
      std: {},
    })
    const store = configureStore({ reducer: { displayData: reducer } })
    const byId = () => store.getState().displayData.timeSeries[path].byId

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    expect(byId()).toEqual({ "0": "fulfilled" })
    await store.dispatch(getTimeSeriesAllData({ path }))
    expect(byId()).toEqual({ "0": "fulfilled", "3": "fulfilled" })
  })

  it("keeps the other records when one request fails and does not retry it", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "1": { "0": 0 }, "2": { "0": 0 } },
      xrange,
      std: {},
    })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockResolvedValueOnce({
        data: { "2": { "0": 0, "1": 1 } },
        xrange,
        std: {},
      })
      .mockRejectedValueOnce(new Error("504"))
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))

    // the failure lands last, so nothing after it could clear a plot-wide error
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "2" }))
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "1" }))
    await new Promise(process.nextTick)
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "1" }))

    const entry = store.getState().displayData.timeSeries[path]
    expect(entry.xrange).toEqual(xrange)
    expect(entry.error).toBeNull()
    expect(entry.fulfilled).toBe(true)
    expect(entry.data).toEqual({ "1": { "0": 0 }, "2": { "0": 0, "1": 1 } })
    expect(entry.byId).toEqual({ "1": "rejected", "2": "fulfilled" })
    expect(getTimeSeriesDataByIdApi).toHaveBeenCalledTimes(2)
  })

  it("shows the error of a failed request the user made, keeping the records", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "1": { "0": 0 } },
      xrange,
      std: {},
    })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockRejectedValueOnce(new Error("404"))
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))

    await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))

    const entry = store.getState().displayData.timeSeries[path]
    expect(entry.error).not.toBeNull()
    expect(entry.data).toEqual({ "1": { "0": 0 } })
    expect(entry.byId).toEqual({ "1": "rejected" })
  })

  it("requests a failed record once more after a retry, and only that one", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "0": { "0": 1, "1": 2 }, "1": { "0": 0 } },
      xrange,
      std: {},
    })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockRejectedValueOnce(new Error("504"))
      .mockResolvedValueOnce({
        data: { "1": { "0": 0, "1": 1 } },
        xrange,
        std: {},
      })
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "1" }))
    await new Promise(process.nextTick)

    store.dispatch(retryRejectedTimeSeries({ path }))
    expect(store.getState().displayData.timeSeries[path].byId).toEqual({
      "0": "fulfilled",
    })
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "1" }))
    await new Promise(process.nextTick)

    expect(store.getState().displayData.timeSeries[path].byId).toEqual({
      "0": "fulfilled",
      "1": "fulfilled",
    })
    expect(getTimeSeriesDataByIdApi).toHaveBeenCalledTimes(2)
  })

  it("does not recreate an entry deleted while its request was in flight", () => {
    const state = reducer(undefined, {
      type: getTimeSeriesDataById.rejected.type,
      meta: { arg: { path, index: "1" } },
      error: { message: "504" },
    })

    expect(state.timeSeries[path]).toBeUndefined()
  })

  it("drops a late success for an entry deleted meanwhile", () => {
    const state = reducer(undefined, {
      type: getTimeSeriesDataById.fulfilled.type,
      meta: { arg: { path, index: "1" } },
      payload: { data: { "1": { "0": 0 } }, xrange, std: {} },
    })

    expect(state.timeSeries[path]).toBeUndefined()
  })

  it("keeps a record loaded by a newer response when an older request fails", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "1": { "0": 0 } },
      xrange,
      std: {},
    })
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    const arg = { path, index: "1" }
    store.dispatch({
      type: getTimeSeriesDataById.fulfilled.type,
      meta: { arg },
      payload: { data: { "1": { "0": 0, "1": 1 } }, xrange, std: {} },
    })

    store.dispatch({
      type: getTimeSeriesDataById.rejected.type,
      meta: { arg },
      error: { message: "504" },
    })

    const entry = store.getState().displayData.timeSeries[path]
    expect(entry.byId).toEqual({ "1": "fulfilled" })
    expect(entry.error).toBeNull()
  })

  it("requests no record by id while an all-data request is in flight", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "0": { "0": 1, "1": 2 }, "1": { "0": 0 } },
      xrange,
      std: {},
    })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockResolvedValue({ data: { "1": { "0": 0, "1": 1 } }, xrange, std: {} })
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    store.dispatch({
      type: getTimeSeriesAllData.pending.type,
      meta: { arg: { path } },
    })

    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "1" }))

    expect(getTimeSeriesDataByIdApi).not.toHaveBeenCalled()
    expect(store.getState().displayData.timeSeries[path].byId).toEqual({
      "0": "fulfilled",
      "1": "pending",
    })
  })
})

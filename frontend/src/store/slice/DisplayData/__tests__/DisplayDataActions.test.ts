import { AxiosError, AxiosHeaders } from "axios"

import { describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"

import {
  commitRoiApi,
  getStatusRoi,
  getTimeSeriesAllDataApi,
  getTimeSeriesDataByIdApi,
  getTimeSeriesInitDataApi,
} from "api/visualizations/Outputs"
import {
  commitRoi,
  getDisplayErrorMessage,
  getStatus,
  getTimeSeriesAllData,
  getTimeSeriesDataById,
  getTimeSeriesDataByIdOnce,
  getTimeSeriesInitData,
  RejectPayload,
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

  it("keeps a by-id result that lands while its refetch fails", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue(initWithStubs)
    const store = configureStore({ reducer: { displayData: reducer } })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockResolvedValueOnce({ data: { "1": record(4) }, xrange, std: {} })
      .mockImplementationOnce(async () => {
        await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))
        throw new Error("503")
      })
      .mockResolvedValueOnce({
        data: { "1": record(5) },
        xrange,
        std: { "1": record(0) },
      })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 1 }))

    const entry = store.getState().displayData.timeSeries[path]
    expect(entry.data["1"]).toEqual(record(5))
    expect(entry.std["1"]).toEqual(record(0))
  })

  it("keeps the held series when a by-id failure clears the entry mid-refetch", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue(initWithStubs)
    const store = configureStore({ reducer: { displayData: reducer } })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockResolvedValueOnce({ data: { "1": record(4) }, xrange, std: {} })
      .mockImplementationOnce(async () => {
        await store.dispatch(getTimeSeriesDataById({ path, index: "0" }))
        throw new Error("503")
      })
      .mockRejectedValueOnce(new Error("503"))
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 1 }))

    expect(store.getState().displayData.timeSeries[path].data["1"]).toEqual(
      record(4),
    )
  })

  it("refetches a record a by-id request filled during the first refetch", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "0": record(1), "1": { "0": 1 }, "2": { "0": 1 } },
      xrange,
      std: {},
    })
    const store = configureStore({ reducer: { displayData: reducer } })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockResolvedValueOnce({ data: { "1": record(4) }, xrange, std: {} })
      .mockImplementationOnce(async () => {
        await store.dispatch(getTimeSeriesDataById({ path, index: "2" }))
        return { data: { "1": record(6) }, xrange, std: {} }
      })
      .mockResolvedValueOnce({ data: { "2": record(5) }, xrange, std: {} })
      .mockResolvedValueOnce({ data: { "2": record(7) }, xrange, std: {} })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 1 }))

    const entry = store.getState().displayData.timeSeries[path]
    expect(entry.data["1"]).toEqual(record(6))
    expect(entry.data["2"]).toEqual(record(7))
    expect(jest.mocked(getTimeSeriesDataByIdApi)).toHaveBeenCalledTimes(4)
  })

  it("keeps a by-id result that lands a round after its refetch failed", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "0": record(1), "1": { "0": 1 }, "2": { "0": 1 } },
      xrange,
      std: {},
    })
    const store = configureStore({ reducer: { displayData: reducer } })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockResolvedValueOnce({ data: { "1": record(4) }, xrange, std: {} })
      .mockImplementationOnce(async () => {
        await store.dispatch(getTimeSeriesDataById({ path, index: "2" }))
        throw new Error("503")
      })
      .mockResolvedValueOnce({ data: { "2": record(5) }, xrange, std: {} })
      .mockImplementationOnce(async () => {
        await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))
        return { data: { "2": record(7) }, xrange, std: {} }
      })
      .mockResolvedValueOnce({
        data: { "1": record(6) },
        xrange,
        std: { "1": record(0) },
      })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    await store.dispatch(getTimeSeriesDataById({ path, index: "1" }))

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 1 }))

    const entry = store.getState().displayData.timeSeries[path]
    expect(entry.data["1"]).toEqual(record(6))
    expect(entry.std["1"]).toEqual(record(0))
    expect(entry.data["2"]).toEqual(record(7))
  })

  // alltimedata has no isFull variant, so the filter dialog keeps the by-id path
  it.each([
    [undefined, 2, 0],
    [true, 1, 9],
  ])(
    "refetches after select all (isFull %s): %i all-data, %i by-id requests",
    async (isFull, allCalls, byIdCalls) => {
      const keys = Array.from({ length: 10 }, (_, i) => String(i))
      const all = (a: number) =>
        Object.fromEntries(keys.map((key) => [key, record(a)]))
      jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
        data: {
          ...Object.fromEntries(keys.map((key) => [key, { "0": 1 }])),
          "0": record(1),
        },
        xrange,
        std: {},
      })
      jest
        .mocked(getTimeSeriesAllDataApi)
        .mockResolvedValueOnce({ data: all(2), xrange, std: {} })
        .mockResolvedValueOnce({ data: all(3), xrange, std: {} })
      jest
        .mocked(getTimeSeriesDataByIdApi)
        .mockImplementation(async (_path, index) => ({
          data: { [index]: record(3) },
          xrange,
          std: {},
        }))
      const store = configureStore({ reducer: { displayData: reducer } })
      await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
      await store.dispatch(getTimeSeriesAllData({ path }))

      await store.dispatch(getTimeSeriesInitData({ path, itemId: 1, isFull }))

      expect(store.getState().displayData.timeSeries[path].data).toEqual({
        ...all(3),
        "0": record(1),
      })
      expect(jest.mocked(getTimeSeriesAllDataApi)).toHaveBeenCalledTimes(
        allCalls,
      )
      expect(jest.mocked(getTimeSeriesDataByIdApi)).toHaveBeenCalledTimes(
        byIdCalls,
      )
    },
  )

  it("refetches a sparse selection by id rather than load the whole output", async () => {
    const keys = Array.from({ length: 10 }, (_, i) => String(i))
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: {
        ...Object.fromEntries(keys.map((key) => [key, { "0": 1 }])),
        "0": record(1),
      },
      xrange,
      std: {},
    })
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockImplementation(async (_path, index) => ({
        data: { [index]: record(3) },
        xrange,
        std: {},
      }))
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    for (const index of ["1", "2", "3", "4", "5", "6", "7"]) {
      await store.dispatch(getTimeSeriesDataById({ path, index }))
    }
    jest.mocked(getTimeSeriesDataByIdApi).mockClear()

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 1 }))

    expect(jest.mocked(getTimeSeriesAllDataApi)).not.toHaveBeenCalled()
    expect(jest.mocked(getTimeSeriesDataByIdApi)).toHaveBeenCalledTimes(7)
    expect(store.getState().displayData.timeSeries[path].data["8"]).toEqual({
      "0": 1,
    })
  })
})

describe("time-series results for a deleted path", () => {
  const payload = { data: {}, xrange: [], std: {} }

  it.each([
    getTimeSeriesDataById.fulfilled(payload, "r", { path: "/p", index: "0" }),
    getTimeSeriesAllData.fulfilled(payload, "r", { path: "/p" }),
  ])("are ignored by $type", (action) => {
    const state = reducer(undefined, { type: "init" })

    expect(reducer(state, action).timeSeries).toEqual({})
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
    expect(byId()).toEqual({ "3": "fulfilled" })
    expect(store.getState().displayData.timeSeries[path].data["3"]).toEqual({
      "0": 0,
      "1": 1,
    })
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "3" }))
    expect(getTimeSeriesDataByIdApi).toHaveBeenCalledTimes(2)
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

  it("marks no record loaded from the init of a one-sample series", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "0": { "0": 5 }, "1": { "0": 5 } },
      xrange: ["0"],
      std: {},
    })
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))

    expect(store.getState().displayData.timeSeries[path].byId).toEqual({})
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

  it("leaves the plot's readiness alone when it ignores a stale failure", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "1": { "0": 0 }, "2": { "0": 0 } },
      xrange,
      std: {},
    })
    const store = configureStore({ reducer: { displayData: reducer } })
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    store.dispatch({
      type: getTimeSeriesDataById.fulfilled.type,
      meta: { arg: { path, index: "1" } },
      payload: { data: { "1": { "0": 0, "1": 1 } }, xrange, std: {} },
    })
    store.dispatch({
      type: getTimeSeriesDataById.pending.type,
      meta: { arg: { path, index: "2" } },
    })

    store.dispatch({
      type: getTimeSeriesDataById.rejected.type,
      meta: { arg: { path, index: "1" } },
      error: { message: "504" },
    })

    const entry = store.getState().displayData.timeSeries[path]
    expect(entry.pending).toBe(true)
    expect(entry.fulfilled).toBe(false)
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

  it("keeps a record in flight pending when another box's init lands first", async () => {
    jest.mocked(getTimeSeriesInitDataApi).mockResolvedValue({
      data: { "0": { "0": 1, "1": 2 }, "1": { "0": 0 } },
      xrange,
      std: {},
    })
    let resolve: (value: unknown) => void = () => undefined
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockReturnValue(new Promise((r) => (resolve = r)) as never)
    const store = configureStore({ reducer: { displayData: reducer } })
    const byId = () => store.getState().displayData.timeSeries[path].byId
    await store.dispatch(getTimeSeriesInitData({ path, itemId: 0 }))
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "1" }))

    await store.dispatch(getTimeSeriesInitData({ path, itemId: 1 }))
    expect(byId()).toEqual({ "0": "fulfilled", "1": "pending" })
    store.dispatch(getTimeSeriesDataByIdOnce({ path, index: "1" }))
    resolve({ data: { "1": { "0": 0, "1": 1 } }, xrange, std: {} })
    await new Promise(process.nextTick)

    expect(getTimeSeriesDataByIdApi).toHaveBeenCalledTimes(1)
    expect(byId()).toEqual({ "0": "fulfilled", "1": "fulfilled" })
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

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
  getTimeSeriesInitData,
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

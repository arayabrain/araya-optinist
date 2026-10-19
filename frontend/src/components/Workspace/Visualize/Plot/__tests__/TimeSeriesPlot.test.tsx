import { Provider } from "react-redux"

import { describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"
import { act, render, screen, waitFor } from "@testing-library/react"

import {
  getTimeSeriesDataByIdApi,
  getTimeSeriesInitDataApi,
} from "api/visualizations/Outputs"
import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import {
  SHADE_RECORD_CAP,
  SHADE_TOTAL_CAP,
} from "components/Workspace/Visualize/Plot/eventSpans"
import { TimeSeriesPlot } from "components/Workspace/Visualize/Plot/TimeSeriesPlot"
import displayData from "store/slice/DisplayData/DisplayDataSlice"
import { DATA_TYPE_SET } from "store/slice/DisplayData/DisplayDataType"
import visualaizeItem, {
  setTimeSeriesItemShadeSource,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import { VISUALIZE_ITEM_TYPE_SET } from "store/slice/VisualizeItem/VisualizeItemType"

const mockPlot = jest.fn()
jest.mock("react-plotlyjs-ts", () => ({
  __esModule: true,
  default: (props: unknown) => {
    mockPlot(props)
    return null
  },
}))
jest.mock("api/visualizations/Outputs")

const TARGET = "/output/target"
const SOURCE = "/output/source"
const TARGET_ID = 0
const SOURCE_ID = 1
const OTHER_SOURCE_ID = 2
const stub = { "0": 0 }

const xrangeOf = (frames: number) =>
  Array.from({ length: frames }, (_, i) => String(i))
// an event on every odd frame, so frames / 2 events
const events = (frames: number) =>
  Object.fromEntries(xrangeOf(frames).map((x, i) => [x, i % 2]))

const entry = (frames: number, data: object, byId: object) => ({
  type: "timeSeries",
  xrange: xrangeOf(frames),
  data,
  std: {},
  pending: false,
  fulfilled: true,
  error: null,
  byId,
})

const item = (
  filePath: string,
  drawOrderList: string[],
  shadeSourceItemId: number | null,
) => ({
  itemType: VISUALIZE_ITEM_TYPE_SET.DISPLAY_DATA,
  dataType: DATA_TYPE_SET.TIME_SERIES,
  filePath,
  width: 500,
  height: 500,
  span: 5,
  rangeUnit: "frames",
  xrange: {},
  drawOrderList,
  refImageItemId: null,
  shadeSourceItemId,
})

// The target box shows TARGET and shades from the box showing SOURCE
function renderPlot(
  frames: number,
  data: object,
  byId: object,
  keys: string[],
  extraItems: object = {},
) {
  jest
    .mocked(getTimeSeriesInitDataApi)
    .mockReturnValue(new Promise(() => undefined))
  const store = configureStore({
    reducer: {
      displayData,
      visualaizeItem,
      algorithmNode: (state = {}) => state,
      pipeline: (state = {}) => state,
      experiments: (state = {}) => state,
    },
    preloadedState: {
      displayData: {
        ...displayData(undefined, { type: "" }),
        timeSeries: {
          [TARGET]: entry(4, { "0": events(4) }, { "0": "fulfilled" }),
          [SOURCE]: entry(frames, data, byId),
        },
      },
      visualaizeItem: {
        ...visualaizeItem(undefined, { type: "" }),
        items: {
          [TARGET_ID]: item(TARGET, ["0"], SOURCE_ID),
          [SOURCE_ID]: item(SOURCE, keys, null),
          ...extraItems,
        },
      },
    } as never,
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware({ immutableCheck: false, serializableCheck: false }),
  })
  render(
    <Provider store={store}>
      <DisplayDataContext.Provider
        value={{
          nodeId: null,
          filePath: TARGET,
          dataType: DATA_TYPE_SET.TIME_SERIES,
          itemId: TARGET_ID,
        }}
      >
        <TimeSeriesPlot />
      </DisplayDataContext.Provider>
    </Provider>,
  )
  return store
}

const lastLayout = () => {
  const { calls } = mockPlot.mock
  return (calls[calls.length - 1][0] as { layout: { shapes: object[] } }).layout
}

const requested = () =>
  jest.mocked(getTimeSeriesDataByIdApi).mock.calls.map(([, index]) => index)

type ByIdResponse = Awaited<ReturnType<typeof getTimeSeriesDataByIdApi>>

const settle = (respond: () => void) =>
  act(async () => {
    respond()
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

describe("TimeSeriesPlot shading", () => {
  it("retries the failed records, fetches the rest and reports what fails again", async () => {
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockImplementation(async (_path, index) => {
        if (index === "1") throw new Error("504")
        return { data: { [index]: events(4) }, xrange: xrangeOf(4), std: {} }
      })

    const store = renderPlot(4, { "1": stub, "2": stub }, { "1": "rejected" }, [
      "1",
      "2",
    ])

    await waitFor(() =>
      expect(store.getState().displayData.timeSeries[SOURCE].byId).toEqual({
        "1": "rejected",
        "2": "fulfilled",
      }),
    )
    expect(requested().sort()).toEqual(["1", "2"])
    expect(
      screen.getByText(/Shading could not load 1 trace\(s\)/),
    ).toBeDefined()
    expect(lastLayout().shapes).toHaveLength(2)
  })

  it("retries again when the link moves to another box on the same output", async () => {
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockRejectedValue(new Error("504") as never)
    const store = renderPlot(4, { "1": stub }, { "1": "rejected" }, ["1"], {
      [OTHER_SOURCE_ID]: item(SOURCE, ["1"], null),
    })
    await waitFor(() => expect(requested()).toEqual(["1"]))

    await settle(() =>
      store.dispatch(
        setTimeSeriesItemShadeSource({
          itemId: TARGET_ID,
          shadeSourceItemId: OTHER_SOURCE_ID,
        }),
      ),
    )

    await waitFor(() => expect(requested()).toEqual(["1", "1"]))
  })

  it("notes the cap and fetches nothing more once the shading is full", () => {
    const frames = 2 * SHADE_RECORD_CAP
    const loaded = SHADE_TOTAL_CAP / SHADE_RECORD_CAP
    const keys = xrangeOf(loaded + 1)

    renderPlot(
      frames,
      Object.fromEntries(
        keys.map((key, i) => [key, i < loaded ? events(frames) : stub]),
      ),
      Object.fromEntries(
        keys.slice(0, loaded).map((key) => [key, "fulfilled"]),
      ),
      keys,
    )

    expect(screen.getByText(/Too many events/)).toBeDefined()
    expect(lastLayout().shapes).toHaveLength(SHADE_TOTAL_CAP)
    expect(requested()).toEqual([])
  })

  it("redraws once per settled record, not again for the request it frees", async () => {
    const requests: {
      [index: string]: {
        resolve: (response: ByIdResponse) => void
        reject: (error: Error) => void
      }
    } = {}
    jest
      .mocked(getTimeSeriesDataByIdApi)
      .mockImplementation(
        (_path, index) =>
          new Promise(
            (resolve, reject) => (requests[index] = { resolve, reject }),
          ),
      )
    const keys = ["1", "2", "3", "4", "5", "6"]
    renderPlot(4, Object.fromEntries(keys.map((key) => [key, stub])), {}, keys)
    expect(requested()).toEqual(["1", "2", "3", "4"])
    const draws = mockPlot.mock.calls.length

    await settle(() =>
      requests["1"].resolve({
        data: { "1": events(4) },
        xrange: xrangeOf(4),
        std: {},
      }),
    )
    expect(requested()).toEqual(["1", "2", "3", "4", "5"])
    expect(mockPlot.mock.calls.length).toBe(draws + 1)
    expect(lastLayout().shapes).toHaveLength(2)

    await settle(() => requests["2"].reject(new Error("504")))
    expect(requested()).toEqual(["1", "2", "3", "4", "5", "6"])
    expect(mockPlot.mock.calls.length).toBe(draws + 2)
  })
})

import React from "react"
import { Provider } from "react-redux"

import { AxiosError, AxiosHeaders } from "axios"

import { beforeEach, describe, expect, it, jest } from "@jest/globals"
import { AnyAction, configureStore } from "@reduxjs/toolkit"
import { act, render, waitFor } from "@testing-library/react"

import { getImageDataApi, getRoiDataApi } from "api/visualizations/Outputs"
import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import { ImagePlot } from "components/Workspace/Visualize/Plot/ImagePlot"
import { DATA_TYPE_SET } from "store/slice/DisplayData/DisplayDataType"
import { initialState as pipelineInitialState } from "store/slice/Pipeline/PipelineSlice"
import { getImageItemData } from "store/slice/VisualizeItem/VisualizeItemActions"
import {
  addItemForWorkflowDialog,
  deleteAllItemForWorkflowDialog,
  pushInitialItemToNewRow,
  setImageItemFilePath,
  setRoiItemFilePath,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import { ImageItem } from "store/slice/VisualizeItem/VisualizeItemType"
import { setCurrentWorkspace } from "store/slice/Workspace/WorkspaceSlice"
import { rootReducer } from "store/store"

// Mock react-plotlyjs-ts to avoid d3-interpolate issues
jest.mock("react-plotlyjs-ts", () => ({
  __esModule: true,
  default: () => <div data-testid="plotly-chart" />,
}))
jest.mock("api/visualizations/Outputs")

const path = "/stack.tif"
const eightFrames = { data: Array.from({ length: 8 }, () => [[0]]) }

function tooLarge() {
  const error = new AxiosError("Request failed with status code 422")
  error.response = {
    status: 422,
    statusText: "Unprocessable Entity",
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: { detail: "too many frames", max_frames: 8 },
  }
  return error
}

// Moves the current pipeline to another record and touches nothing else, as
// reproducing a record from the Record tab does for the Visualize items
const SET_CURRENT_UID = "test/setCurrentUid"

function newStore(uid?: string) {
  const store = configureStore({
    reducer: (state: Parameters<typeof rootReducer>[0], action: AnyAction) => {
      const next = rootReducer(state, action)
      return action.type === SET_CURRENT_UID
        ? {
            ...next,
            pipeline: {
              ...next.pipeline,
              currentPipeline: { uid: action.uid },
            },
          }
        : next
    },
    preloadedState: {
      pipeline: {
        ...pipelineInitialState,
        currentPipeline: uid ? { uid } : undefined,
      },
    },
  })
  store.dispatch(setCurrentWorkspace(1))
  return store
}

function addDialogItem(store: ReturnType<typeof newStore>) {
  store.dispatch(
    addItemForWorkflowDialog({
      nodeId: "node",
      filePath: path,
      dataType: DATA_TYPE_SET.IMAGE,
    }),
  )
  const ids = Object.keys(store.getState().visualaizeItem.items).map(Number)
  return Math.max(...ids)
}

async function renderPlotAndWaitForFit(
  store: ReturnType<typeof newStore>,
  itemId: number,
) {
  render(
    <Provider store={store}>
      <DisplayDataContext.Provider
        value={{
          nodeId: "node",
          filePath: path,
          dataType: DATA_TYPE_SET.IMAGE,
          itemId,
        }}
      >
        <ImagePlot />
      </DisplayDataContext.Provider>
    </Provider>,
  )
  await waitFor(() =>
    expect(
      (store.getState().visualaizeItem.items[itemId] as ImageItem).endIndex,
    ).toBe(8),
  )
  await waitFor(() =>
    expect(store.getState().displayData.image[path].fulfilled).toBe(true),
  )
}

const requestedEndIndexes = () =>
  jest.mocked(getImageDataApi).mock.calls.map(([, p]) => p.endIndex)

describe("ImagePlot", () => {
  beforeEach(() => {
    jest.mocked(getImageDataApi).mockReset()
    jest.mocked(getRoiDataApi).mockReset()
  })

  it("fits a dialog item's window to the frames the server can preview", async () => {
    jest
      .mocked(getImageDataApi)
      .mockRejectedValueOnce(tooLarge())
      .mockResolvedValueOnce(eightFrames)
    const store = newStore()
    const itemId = addDialogItem(store)

    await renderPlotAndWaitForFit(store, itemId)

    expect(requestedEndIndexes()).toEqual([10, 8])
  })

  it("fits a dialog item whose file holds a Visualize item's refusal", async () => {
    jest
      .mocked(getImageDataApi)
      .mockRejectedValueOnce(tooLarge())
      .mockRejectedValueOnce(tooLarge())
      .mockResolvedValueOnce(eightFrames)
    const store = newStore()
    store.dispatch(pushInitialItemToNewRow())
    store.dispatch(
      setImageItemFilePath({ itemId: 0, filePath: path, nodeId: null }),
    )
    await store.dispatch(getImageItemData({ itemId: 0, path, workspaceId: 1 }))
    expect(store.getState().displayData.image[path].error).toBe(
      "too many frames",
    )
    const itemId = addDialogItem(store)

    await renderPlotAndWaitForFit(store, itemId)

    expect(requestedEndIndexes()).toEqual([10, 10, 8])
  })

  it("fits a reopened dialog item whose file holds the frames it narrowed to", async () => {
    jest
      .mocked(getImageDataApi)
      .mockRejectedValueOnce(tooLarge())
      .mockResolvedValueOnce(eightFrames)
      .mockRejectedValueOnce(tooLarge())
      .mockResolvedValueOnce(eightFrames)
    const store = newStore()
    const firstId = addDialogItem(store)
    await store.dispatch(
      getImageItemData({ itemId: firstId, path, workspaceId: 1 }),
    )
    // closed without releasing its frames, as before deleteWorkflowDialogItems
    store.dispatch(deleteAllItemForWorkflowDialog())
    const itemId = addDialogItem(store)
    expect(
      (store.getState().visualaizeItem.items[itemId] as ImageItem).endIndex,
    ).toBe(10)

    await renderPlotAndWaitForFit(store, itemId)

    expect(requestedEndIndexes()).toEqual([10, 8, 10, 8])
  })

  it("asks for a dialog item's frames with the record's id, the narrowed window too", async () => {
    jest
      .mocked(getImageDataApi)
      .mockRejectedValueOnce(tooLarge())
      .mockResolvedValueOnce(eightFrames)
    const store = newStore("rec1")
    const itemId = addDialogItem(store)

    await renderPlotAndWaitForFit(store, itemId)

    expect(
      jest.mocked(getImageDataApi).mock.calls.map(([, p]) => p.uniqueId),
    ).toEqual(["rec1", "rec1"])
  })

  it("keeps a Visualize item's ROI data when the current pipeline changes", async () => {
    jest.mocked(getImageDataApi).mockResolvedValue(eightFrames)
    jest.mocked(getRoiDataApi).mockResolvedValue({ data: [[[0]]] })
    const store = newStore("rec1")
    store.dispatch(pushInitialItemToNewRow())
    store.dispatch(
      setImageItemFilePath({ itemId: 0, filePath: path, nodeId: null }),
    )
    store.dispatch(
      setRoiItemFilePath({ itemId: 0, filePath: "/roi.json", nodeId: null }),
    )
    render(
      <Provider store={store}>
        <DisplayDataContext.Provider
          value={{
            nodeId: null,
            filePath: path,
            dataType: DATA_TYPE_SET.IMAGE,
            itemId: 0,
          }}
        >
          <ImagePlot />
        </DisplayDataContext.Provider>
      </Provider>,
    )
    await waitFor(() =>
      expect(store.getState().displayData.image[path].fulfilled).toBe(true),
    )
    const roiFetches = jest.mocked(getRoiDataApi).mock.calls.length
    expect(roiFetches).toBeGreaterThan(0)

    act(() => {
      store.dispatch({ type: SET_CURRENT_UID, uid: "rec2" })
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(jest.mocked(getRoiDataApi).mock.calls.length).toBe(roiFetches)
  })
})

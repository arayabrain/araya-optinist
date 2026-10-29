import { AxiosError, AxiosHeaders } from "axios"

import { beforeEach, describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"

import { getImageDataApi } from "api/visualizations/Outputs"
import { DATA_TYPE_SET } from "store/slice/DisplayData/DisplayDataType"
import {
  deleteWorkflowDialogItems,
  getImageItemData,
} from "store/slice/VisualizeItem/VisualizeItemActions"
import {
  addItemForWorkflowDialog,
  pushInitialItemToNewRow,
  setImageItemFilePath,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import { ImageItem } from "store/slice/VisualizeItem/VisualizeItemType"
import { rootReducer } from "store/store"

jest.mock("api/visualizations/Outputs")

const path = "/stack.tif"
const message =
  "Frames 1-10 are too many to preview at 1024 x 1024; choose at most 8 frames."

function tooLarge(maxFrames: number) {
  const error = new AxiosError("Request failed with status code 422")
  error.response = {
    status: 422,
    statusText: "Unprocessable Entity",
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: { detail: message, max_frames: maxFrames },
  }
  return error
}

function setUp(addItem: "dialog" | "visualize") {
  const store = configureStore({ reducer: rootReducer })
  if (addItem === "dialog") {
    store.dispatch(
      addItemForWorkflowDialog({
        nodeId: "node",
        filePath: path,
        dataType: DATA_TYPE_SET.IMAGE,
      }),
    )
  } else {
    store.dispatch(pushInitialItemToNewRow())
    store.dispatch(
      setImageItemFilePath({ itemId: 0, filePath: path, nodeId: null }),
    )
  }
  const fetch = () =>
    store.dispatch(getImageItemData({ itemId: 0, path, workspaceId: 1 }))
  const item = () => store.getState().visualaizeItem.items[0] as ImageItem
  const image = () => store.getState().displayData.image[path]
  return { store, fetch, item, image }
}

const requestedEndIndexes = () =>
  jest.mocked(getImageDataApi).mock.calls.map(([, params]) => params.endIndex)

// A response the test settles itself, after the request was made
function deferred<T>() {
  let resolve: (value: T) => void = () => undefined
  let reject: (reason: unknown) => void = () => undefined
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function requestsMade(count: number) {
  while (jest.mocked(getImageDataApi).mock.calls.length < count) {
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
}

describe("getImageItemData", () => {
  beforeEach(() => {
    jest.mocked(getImageDataApi).mockReset()
  })

  it("narrows a dialog item's window to the frames the server can preview", async () => {
    jest
      .mocked(getImageDataApi)
      .mockRejectedValueOnce(tooLarge(8))
      .mockResolvedValueOnce({ data: [[[0]]] })
    const { fetch, item, image } = setUp("dialog")

    await fetch()

    expect(requestedEndIndexes()).toEqual([10, 8])
    expect(item().endIndex).toBe(8)
    expect(image().fulfilled).toBe(true)
    expect(image().error).toBeNull()
  })

  it("keeps a Visualize item's window and shows the message", async () => {
    jest.mocked(getImageDataApi).mockRejectedValueOnce(tooLarge(8))
    const { fetch, item, image } = setUp("visualize")

    await fetch()

    expect(requestedEndIndexes()).toEqual([10])
    expect(item().endIndex).toBe(10)
    expect(image().error).toBe(message)
  })

  it("does not fetch again when no frame of the file fits", async () => {
    jest.mocked(getImageDataApi).mockRejectedValueOnce(tooLarge(0))
    const { fetch, item, image } = setUp("dialog")

    await fetch()

    expect(requestedEndIndexes()).toEqual([10])
    expect(item().endIndex).toBe(10)
    expect(image().error).toBe(message)
  })

  it("does not narrow another item that took a closed dialog's id", async () => {
    const refusal = deferred<never>()
    jest.mocked(getImageDataApi).mockReturnValueOnce(refusal.promise)
    const { store, fetch, item } = setUp("dialog")

    const fetching = fetch()
    await requestsMade(1)
    await store.dispatch(deleteWorkflowDialogItems())
    store.dispatch(
      addItemForWorkflowDialog({
        nodeId: "other",
        filePath: "/other.tif",
        dataType: DATA_TYPE_SET.IMAGE,
      }),
    )
    refusal.reject(tooLarge(8))
    await fetching

    expect(item().filePath).toBe("/other.tif")
    expect(item().endIndex).toBe(10)
    expect(requestedEndIndexes()).toEqual([10])
  })

  it("does not keep frames that arrive after the dialog closed", async () => {
    const fitted = deferred<{ data: number[][][] }>()
    jest
      .mocked(getImageDataApi)
      .mockRejectedValueOnce(tooLarge(8))
      .mockReturnValueOnce(fitted.promise)
    const { store, fetch, image } = setUp("dialog")

    const fetching = fetch()
    await requestsMade(2)
    await store.dispatch(deleteWorkflowDialogItems())
    fitted.resolve({ data: [[[0]]] })
    await fetching

    expect(requestedEndIndexes()).toEqual([10, 8])
    expect(image()).toBeUndefined()
  })
})

describe("deleteWorkflowDialogItems", () => {
  it("drops a dialog image item's frames, so a Visualize item fetches its own window", async () => {
    jest.mocked(getImageDataApi).mockResolvedValueOnce({ data: [[[0]]] })
    const store = configureStore({ reducer: rootReducer })
    store.dispatch(pushInitialItemToNewRow())
    store.dispatch(
      addItemForWorkflowDialog({
        nodeId: "node",
        filePath: path,
        dataType: DATA_TYPE_SET.IMAGE,
      }),
    )
    await store.dispatch(getImageItemData({ itemId: 1, path, workspaceId: 1 }))
    expect(store.getState().displayData.image[path].fulfilled).toBe(true)

    await store.dispatch(deleteWorkflowDialogItems())

    expect(Object.keys(store.getState().visualaizeItem.items)).toEqual(["0"])
    expect(store.getState().displayData.image[path]).toBeUndefined()
  })
})

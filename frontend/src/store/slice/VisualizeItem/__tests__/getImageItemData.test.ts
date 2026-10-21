import { AxiosError, AxiosHeaders } from "axios"

import { beforeEach, describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"

import { getImageDataApi } from "api/visualizations/Outputs"
import { DATA_TYPE_SET } from "store/slice/DisplayData/DisplayDataType"
import { getImageItemData } from "store/slice/VisualizeItem/VisualizeItemActions"
import {
  addItemForWorkflowDialog,
  pushInitialItemToNewRow,
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
  store.dispatch(
    addItem === "dialog"
      ? addItemForWorkflowDialog({
          nodeId: "node",
          filePath: path,
          dataType: DATA_TYPE_SET.IMAGE,
        })
      : pushInitialItemToNewRow(),
  )
  const fetch = () =>
    store.dispatch(getImageItemData({ itemId: 0, path, workspaceId: 1 }))
  const item = () => store.getState().visualaizeItem.items[0] as ImageItem
  const image = () => store.getState().displayData.image[path]
  return { fetch, item, image }
}

const requestedEndIndexes = () =>
  jest.mocked(getImageDataApi).mock.calls.map(([, params]) => params.endIndex)

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
})

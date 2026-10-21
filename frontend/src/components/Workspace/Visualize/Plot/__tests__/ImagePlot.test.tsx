import React from "react"
import { Provider } from "react-redux"

import { AxiosError, AxiosHeaders } from "axios"

import { describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"
import { render, waitFor } from "@testing-library/react"

import { getImageDataApi } from "api/visualizations/Outputs"
import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import { ImagePlot } from "components/Workspace/Visualize/Plot/ImagePlot"
import { DATA_TYPE_SET } from "store/slice/DisplayData/DisplayDataType"
import { addItemForWorkflowDialog } from "store/slice/VisualizeItem/VisualizeItemSlice"
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

describe("ImagePlot", () => {
  it("fits a dialog item's window to the frames the server can preview", async () => {
    const tooLarge = new AxiosError("Request failed with status code 422")
    tooLarge.response = {
      status: 422,
      statusText: "Unprocessable Entity",
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: { detail: "too many frames", max_frames: 8 },
    }
    jest
      .mocked(getImageDataApi)
      .mockRejectedValueOnce(tooLarge)
      .mockResolvedValueOnce({ data: [[[0]]] })
    const store = configureStore({ reducer: rootReducer })
    store.dispatch(setCurrentWorkspace(1))
    store.dispatch(
      addItemForWorkflowDialog({
        nodeId: "node",
        filePath: path,
        dataType: DATA_TYPE_SET.IMAGE,
      }),
    )

    render(
      <Provider store={store}>
        <DisplayDataContext.Provider
          value={{
            nodeId: "node",
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
      expect(
        (store.getState().visualaizeItem.items[0] as ImageItem).endIndex,
      ).toBe(8),
    )
    await waitFor(() =>
      expect(store.getState().displayData.image[path].fulfilled).toBe(true),
    )
    expect(
      jest.mocked(getImageDataApi).mock.calls.map(([, p]) => p.endIndex),
    ).toEqual([10, 8])
  })
})

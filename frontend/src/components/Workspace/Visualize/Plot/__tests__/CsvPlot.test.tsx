import React from "react"
import { Provider } from "react-redux"

import { describe, expect, it, jest } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"
import { render, waitFor } from "@testing-library/react"

import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import { CsvPlot } from "components/Workspace/Visualize/Plot/CsvPlot"
import { BASE_URL } from "const/API"
import { DATA_TYPE_SET } from "store/slice/DisplayData/DisplayDataType"
import { initialState as pipelineInitialState } from "store/slice/Pipeline/PipelineSlice"
import { setCurrentWorkspace } from "store/slice/Workspace/WorkspaceSlice"
import { rootReducer } from "store/store"
import axios from "utils/axios"

jest.mock("utils/axios")

const path = "input.csv"

describe("CsvPlot", () => {
  it("asks for the file with the record's id", async () => {
    // Left pending: the request is the subject, not the table
    jest.mocked(axios.get).mockReturnValueOnce(new Promise(() => undefined))
    const store = configureStore({
      reducer: rootReducer,
      preloadedState: {
        pipeline: { ...pipelineInitialState, currentPipeline: { uid: "rec1" } },
      },
    })
    store.dispatch(setCurrentWorkspace(1))

    render(
      <Provider store={store}>
        <DisplayDataContext.Provider
          value={{
            nodeId: "node",
            filePath: path,
            dataType: DATA_TYPE_SET.CSV,
            itemId: 0,
          }}
        >
          <CsvPlot />
        </DisplayDataContext.Provider>
      </Provider>,
    )

    await waitFor(() =>
      expect(axios.get).toHaveBeenCalledWith(
        `${BASE_URL}/api/visualizations/csv/${path}`,
        { params: { workspace_id: 1, unique_id: "rec1" } },
      ),
    )
  })
})

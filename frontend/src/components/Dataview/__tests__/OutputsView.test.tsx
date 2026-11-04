import React from "react"
import { Provider } from "react-redux"

import configureStore from "redux-mock-store"
import thunk from "redux-thunk"

import { describe, it, expect, jest, beforeEach } from "@jest/globals"
import { render, screen, fireEvent } from "@testing-library/react"

import { VisualizationItemData } from "components/Dataview/BaseNodesView"
import OutputsView from "components/Dataview/OutputsView"

const mockStore = configureStore([thunk])

const mockPutHiddenOutputs = jest.fn()

jest.mock("store/slice/Dataview/DataviewActions", () => ({
  putHiddenOutputs: (arg: unknown) => {
    mockPutHiddenOutputs(arg)
    const thunkAction = () =>
      Object.assign(Promise.resolve([]), { unwrap: () => Promise.resolve([]) })
    return thunkAction
  },
}))

jest.mock("components/Dataview/BaseNodesView", () => ({
  __esModule: true,
  default: ({
    data,
    renderData,
  }: {
    data: unknown[]
    renderData: () => React.ReactElement[]
  }) => (
    <div data-testid="base-nodes-view">{data.length > 0 && renderData()}</div>
  ),
  renderVisualizationItems: (
    items: VisualizationItemData[],
    renderAction?: (item: VisualizationItemData) => React.ReactNode,
  ) =>
    items.map((item) => (
      <div
        key={item.itemKey}
        data-testid={`visualization-item-${item.itemKey}`}
      >
        {item.title}
        {renderAction?.(item)}
      </div>
    )),
  useDataviewVisualizationCleanup: () => undefined,
}))

const WORKSPACE_ID = 1
const UID = "uid-1"
const HIDDEN_PATH = "1/uid-1/func1/heatmap"
const VISIBLE_PATH = "1/uid-1/func1/fluorescence"

const buildState = (hiddenOutputs: string[]) => ({
  dataview: {
    data: {
      private: {
        items: [
          {
            id: 7,
            uid: UID,
            workspace: { id: WORKSPACE_ID },
            hidden_outputs: hiddenOutputs,
          },
        ],
        total: 1,
        offset: 0,
        limit: 50,
      },
      public: { items: [], total: 0, offset: 0, limit: 50 },
    },
    loading: false,
    error: { public: null, private: null },
  },
  flowElement: {
    flowNodes: [{ id: "func1", data: { label: "Function 1" } }],
    flowEdges: [],
  },
  pipeline: {
    run: {
      uid: UID,
      status: "Finished",
      runPostData: {},
      runResult: {
        func1: {
          status: "success",
          name: "func1",
          outputPaths: {
            heatmap: { path: HIDDEN_PATH, type: "heatMap", max_index: 1 },
            fluorescence: {
              path: VISIBLE_PATH,
              type: "timeSeries",
              max_index: 1,
            },
          },
        },
      },
    },
  },
})

const renderView = (is_public: boolean, hiddenOutputs = [HIDDEN_PATH]) => {
  const store = mockStore(buildState(hiddenOutputs))
  render(
    <Provider store={store}>
      <OutputsView
        open
        is_public={is_public}
        workspaceId={WORKSPACE_ID}
        uid={UID}
        handleClose={jest.fn()}
      />
    </Provider>,
  )
  return store
}

describe("OutputsView publish switch", () => {
  beforeEach(() => mockPutHiddenOutputs.mockClear())

  it("shows a switch per output for the owner, off for hidden outputs", () => {
    renderView(false)
    expect(screen.getByLabelText("Publish heatmap")).not.toBeChecked()
    expect(screen.getByLabelText("Publish fluorescence")).toBeChecked()
  })

  it("shows no switch to public viewers", () => {
    renderView(true)
    expect(screen.getByTestId("visualization-item-func1-heatmap")).toBeTruthy()
    expect(screen.queryByRole("checkbox")).toBeNull()
  })

  it("hiding an output sends the full hidden list", () => {
    renderView(false)
    fireEvent.click(screen.getByLabelText("Publish fluorescence"))
    expect(mockPutHiddenOutputs).toHaveBeenCalledWith({
      id: 7,
      hiddenOutputs: [HIDDEN_PATH, VISIBLE_PATH],
    })
  })

  it("re-publishing removes the output from the hidden list", () => {
    renderView(false)
    fireEvent.click(screen.getByLabelText("Publish heatmap"))
    expect(mockPutHiddenOutputs).toHaveBeenCalledWith({
      id: 7,
      hiddenOutputs: [],
    })
  })
})

import React from "react"
import { Provider } from "react-redux"

import configureStore from "redux-mock-store"
import thunk from "redux-thunk"

import { describe, it, expect, jest, beforeEach } from "@jest/globals"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"

import { VisualizationItemData } from "components/Dataview/BaseNodesView"
import OutputsView from "components/Dataview/OutputsView"

const mockStore = configureStore([thunk])

const mockEnqueueSnackbar = jest.fn()
jest.mock("notistack", () => ({
  enqueueSnackbar: (...args: unknown[]) => mockEnqueueSnackbar(...args),
}))

const mockPutHiddenOutputs = jest.fn()
const mockGetHiddenOutputs = jest.fn()

jest.mock("store/slice/Dataview/DataviewActions", () => ({
  getHiddenOutputs: (arg: unknown) => {
    const failed = mockGetHiddenOutputs(arg) === "fail"
    const thunkAction = () =>
      Object.assign(Promise.resolve([]), {
        unwrap: () =>
          failed ? Promise.reject(new Error()) : Promise.resolve([]),
      })
    return thunkAction
  },
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
  renderVisualizationItems: (items: VisualizationItemData[]) =>
    items.map((item) => (
      <div
        key={item.itemKey}
        data-testid={`visualization-item-${item.itemKey}`}
      >
        {item.title}
      </div>
    )),
  BaseDisplayDataView: ({ filePath }: { filePath: string }) => (
    <div data-testid="plot">{filePath}</div>
  ),
  useDataviewVisualizationCleanup: () => undefined,
}))

const WORKSPACE_ID = 1
const UID = "uid-1"
const HIDDEN_PATH = "1/uid-1/func1/heatmap"
const VISIBLE_PATH = "1/uid-1/func1/fluorescence"

const buildState = (
  hiddenOutputs: string[],
  heatmapPath = HIDDEN_PATH,
  withOutputs = true,
  pipelineUid = UID,
) => ({
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
    currentPipeline: { uid: pipelineUid },
    run: {
      uid: UID,
      status: "Finished",
      runPostData: {},
      runResult: withOutputs
        ? {
            func1: {
              status: "success",
              name: "func1",
              outputPaths: {
                heatmap: { path: heatmapPath, type: "heatMap", max_index: 1 },
                fluorescence: {
                  path: VISIBLE_PATH,
                  type: "timeSeries",
                  max_index: 1,
                },
              },
            },
          }
        : {},
    },
  },
})

const renderView = (
  is_public: boolean,
  hiddenOutputs = [HIDDEN_PATH],
  ...rest: [string?, boolean?, string?]
) => {
  const store = mockStore(buildState(hiddenOutputs, ...rest))
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

const switchFor = async (title: string) => {
  const input = screen.getByLabelText(`Publish ${title}`)
  await waitFor(() => expect(input).not.toBeDisabled())
  return input
}

describe("OutputsView publish switch", () => {
  beforeEach(() => {
    mockPutHiddenOutputs.mockClear()
    mockGetHiddenOutputs.mockReset()
    mockEnqueueSnackbar.mockClear()
  })

  it("matches a hidden entry against a non-canonical output path", async () => {
    renderView(
      false,
      [HIDDEN_PATH],
      "/app/studio_data/output/1/uid-1/func1//./heatmap",
    )
    expect(await switchFor("heatmap")).not.toBeChecked()
    expect(await switchFor("fluorescence")).toBeChecked()
  })

  it("keeps the switches disabled when the visibility fails to load", async () => {
    mockGetHiddenOutputs.mockReturnValue("fail")
    renderView(false, [])
    await waitFor(() => expect(mockEnqueueSnackbar).toHaveBeenCalled())
    expect(screen.getByLabelText("Publish heatmap")).toBeDisabled()
  })

  it("loads the visibility only once the outputs have loaded", () => {
    renderView(false, [], HIDDEN_PATH, false)
    expect(mockGetHiddenOutputs).not.toHaveBeenCalled()
  })

  it("does not load the visibility over another record's outputs", () => {
    renderView(false, [], HIDDEN_PATH, true, "previous-uid")
    expect(mockGetHiddenOutputs).not.toHaveBeenCalled()
  })

  it("lists every output with a switch for the owner, off when hidden", async () => {
    renderView(false)
    expect(await switchFor("heatmap")).not.toBeChecked()
    expect(await switchFor("fluorescence")).toBeChecked()
    expect(screen.getByText("Public 1 / 2")).toBeTruthy()
  })

  it("loads the owner's visibility, with the defaults, when it opens", () => {
    renderView(false)
    expect(mockGetHiddenOutputs).toHaveBeenCalledWith({ id: 7 })
  })

  it("keeps the switches disabled until the visibility has loaded", () => {
    renderView(false)
    expect(screen.getByLabelText("Publish heatmap")).toBeDisabled()
  })

  it("shows the selected output's plot", async () => {
    renderView(false)
    fireEvent.click(screen.getByText("fluorescence"))
    expect(screen.getByTestId("plot").textContent).toBe(VISIBLE_PATH)
  })

  it("shows the cards and no switch to public viewers", () => {
    renderView(true)
    expect(screen.getByTestId("visualization-item-func1-heatmap")).toBeTruthy()
    expect(screen.queryByRole("checkbox")).toBeNull()
    expect(mockGetHiddenOutputs).not.toHaveBeenCalled()
  })

  it("hiding an output sends only that change", async () => {
    renderView(false)
    fireEvent.click(await switchFor("fluorescence"))
    expect(mockPutHiddenOutputs).toHaveBeenCalledWith({
      id: 7,
      path: VISIBLE_PATH,
      hidden: true,
    })
  })

  it("re-publishing sends hidden false for that output", async () => {
    renderView(false)
    fireEvent.click(await switchFor("heatmap"))
    expect(mockPutHiddenOutputs).toHaveBeenCalledWith({
      id: 7,
      path: HIDDEN_PATH,
      hidden: false,
    })
  })

  it("disables the switches while a change is saving", async () => {
    renderView(false)
    fireEvent.click(await switchFor("heatmap"))
    expect(screen.getByLabelText("Publish fluorescence")).toBeDisabled()
  })
})

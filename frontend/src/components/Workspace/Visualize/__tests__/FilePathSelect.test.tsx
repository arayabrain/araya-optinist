/* eslint-disable no-undef */
import { Provider } from "react-redux"

import configureStore from "redux-mock-store"

import { afterEach, describe, it, jest } from "@jest/globals"
import { act, fireEvent, render, screen } from "@testing-library/react"

import { FilePathSelect } from "components/Workspace/Visualize/FilePathSelect"
import {
  DATA_TYPE,
  DATA_TYPE_SET,
} from "store/slice/DisplayData/DisplayDataType"

const mockStore = configureStore([])

const ETA_A = "eta_gvgs8v0lta"
const ETA_B = "eta_abwqe9qgh1"

const output = (path: string, type: DATA_TYPE) => ({
  path,
  type,
  data_shape: [],
})

const etaOutputs = (nodeId: string) => ({
  status: "success",
  name: "eta",
  outputPaths: {
    mean: output(`/output/${nodeId}/mean.json`, DATA_TYPE_SET.TIME_SERIES),
    mean_heatmap: output(
      `/output/${nodeId}/mean_heatmap.json`,
      DATA_TYPE_SET.HEAT_MAP,
    ),
  },
})

const buildState = ({
  flowNodes = [] as { id: string; data: { label: string; type: string } }[],
  runResult = {} as Record<string, unknown>,
  inputNode = {} as Record<string, unknown>,
} = {}) => ({
  inputNode,
  flowElement: { flowNodes, flowEdges: [] },
  pipeline: {
    currentPipeline: { uid: "uid-1" },
    run: {
      uid: "uid-1",
      status: "Finished",
      runPostData: {},
      runResult,
    },
    runBtn: 1,
  },
})

const twoEtaNodesState = buildState({
  flowNodes: [
    { id: ETA_A, data: { label: "eta", type: "algorithm" } },
    { id: ETA_B, data: { label: "eta", type: "algorithm" } },
  ],
  runResult: {
    [ETA_A]: etaOutputs(ETA_A),
    [ETA_B]: etaOutputs(ETA_B),
    // every run carries this synthetic result; it has no node on the canvas
    post_process_0: {
      status: "success",
      name: "post_process",
      outputPaths: {},
    },
  },
})

const renderSelect = (
  state: ReturnType<typeof buildState>,
  props: Partial<Parameters<typeof FilePathSelect>[0]> = {},
) => {
  const onSelect = jest.fn()
  const view = render(
    <Provider store={mockStore(state)}>
      <FilePathSelect
        selectedNodeId={null}
        selectedFilePath={null}
        onSelect={onSelect}
        {...props}
      />
    </Provider>,
  )
  return { onSelect, view }
}

const openMenu = () => fireEvent.mouseDown(screen.getByRole("combobox"))

describe("FilePathSelect", () => {
  afterEach(() => {
    jest.restoreAllMocks()
  })

  it("distinguishes two nodes sharing a label by their unique node id", () => {
    renderSelect(twoEtaNodesState)
    openMenu()

    expect(screen.getByText(ETA_A)).toBeInTheDocument()
    expect(screen.getByText(ETA_B)).toBeInTheDocument()
    expect(screen.getAllByText("mean")).toHaveLength(2)
    expect(screen.getAllByText("mean_heatmap")).toHaveLength(2)
  })

  it("omits the synthetic post_process result, which has no outputs", () => {
    const { view } = renderSelect(twoEtaNodesState)
    openMenu()

    expect(screen.queryByText("post_process_0")).not.toBeInTheDocument()
    expect(
      view.baseElement.querySelectorAll(".MuiListSubheader-root"),
    ).toHaveLength(2)
  })

  it("labels the select for assistive technology", () => {
    renderSelect(twoEtaNodesState, { label: "Select Roi" })

    expect(
      screen.getByRole("combobox", { name: "Select Roi" }),
    ).toBeInTheDocument()
  })

  it("keeps the id suffix of the selected output out of the truncated text", () => {
    const { onSelect, view } = renderSelect(twoEtaNodesState)
    openMenu()

    // second `mean`, i.e. the one belonging to ETA_B
    fireEvent.click(screen.getAllByRole("option", { name: "mean" })[1])

    expect(onSelect).toHaveBeenCalledWith(
      ETA_B,
      `/output/${ETA_B}/mean.json`,
      DATA_TYPE_SET.TIME_SERIES,
      "mean",
    )

    // the mock store does not reduce, so feed the selection back as props
    view.rerender(
      <Provider store={mockStore(twoEtaNodesState)}>
        <FilePathSelect
          selectedNodeId={ETA_B}
          selectedFilePath={`/output/${ETA_B}/mean.json`}
          onSelect={onSelect}
        />
      </Provider>,
    )

    const combobox = screen.getByRole("combobox")
    expect(combobox).toHaveTextContent(`mean (${ETA_B})`)
    expect(combobox).toHaveAttribute("title", `mean (${ETA_B})`)

    // jsdom has no layout, so this pins the flex rules, not the rendered pixels
    const uid = screen.getByText("abwqe9qgh1)")
    expect(getComputedStyle(uid).flexShrink).toBe("0")
    const text = screen.getByText("mean (eta_")
    expect(getComputedStyle(text).textOverflow).toBe("ellipsis")
    expect(getComputedStyle(text).overflow).toBe("hidden")
  })

  it("keeps an unprefixed id in the truncatable text with the file name first", () => {
    const state = buildState({
      flowNodes: [
        { id: "input_kt62vwavq2", data: { label: "data.csv", type: "input" } },
      ],
      inputNode: {
        input_kt62vwavq2: {
          fileType: "csv",
          selectedFilePath: "/input/data.csv",
          param: {},
        },
      },
    })
    renderSelect(state, {
      selectedNodeId: "input_kt62vwavq2",
      selectedFilePath: "/input/data.csv",
    })

    // one box: the file name is clipped last, the id is what gets cut off
    expect(screen.getByText("data.csv (input_kt62vwavq2)")).toBeInTheDocument()
    expect(screen.queryByText(/^kt62vwavq2\)$/)).not.toBeInTheDocument()
  })

  it("tells two input nodes holding the same file apart", () => {
    const state = buildState({
      flowNodes: [
        { id: "input_aaaaaaaaaa", data: { label: "dup.csv", type: "input" } },
        { id: "input_bbbbbbbbbb", data: { label: "dup.csv", type: "input" } },
      ],
      inputNode: {
        input_aaaaaaaaaa: {
          fileType: "csv",
          selectedFilePath: "/input/dup.csv",
          param: {},
        },
        input_bbbbbbbbbb: {
          fileType: "csv",
          selectedFilePath: "/input/dup.csv",
          param: {},
        },
      },
    })
    const { onSelect, view } = renderSelect(state)
    openMenu()

    expect(screen.getByText("dup.csv (input_aaaaaaaaaa)")).toBeInTheDocument()
    expect(screen.getByText("dup.csv (input_bbbbbbbbbb)")).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole("option", { name: "dup.csv" })[1])
    expect(onSelect).toHaveBeenCalledWith(
      "input_bbbbbbbbbb",
      "/input/dup.csv",
      DATA_TYPE_SET.CSV,
      undefined,
    )

    view.rerender(
      <Provider store={mockStore(state)}>
        <FilePathSelect
          selectedNodeId="input_bbbbbbbbbb"
          selectedFilePath="/input/dup.csv"
          onSelect={onSelect}
        />
      </Provider>,
    )
    expect(screen.getByRole("combobox")).toHaveTextContent(
      "dup.csv (input_bbbbbbbbbb)",
    )
  })

  it("heads a result whose node is no longer on the canvas with its bare id", () => {
    const state = buildState({
      flowNodes: [],
      runResult: { orphan_9: etaOutputs("orphan_9") },
    })
    renderSelect(state)
    openMenu()

    expect(screen.getByText("orphan_9")).toBeInTheDocument()
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument()
  })

  it("heads an input node with its name and id and keeps the file name on the item", () => {
    const state = buildState({
      flowNodes: [
        {
          id: "input_kt62vwavq2",
          data: { label: "sample_mouse2p_image.tiff", type: "input" },
        },
      ],
      inputNode: {
        input_kt62vwavq2: {
          fileType: "csv",
          selectedFilePath: "/input/sample_mouse2p_image.tiff",
          param: {},
        },
      },
    })
    const { onSelect } = renderSelect(state, {
      selectedNodeId: "input_kt62vwavq2",
      selectedFilePath: "/input/sample_mouse2p_image.tiff",
    })

    const combobox = screen.getByRole("combobox")
    expect(combobox).toHaveTextContent(
      "sample_mouse2p_image.tiff (input_kt62vwavq2)",
    )
    expect(combobox).toHaveAttribute(
      "title",
      "sample_mouse2p_image.tiff (input_kt62vwavq2)",
    )

    openMenu()
    // header in the menu plus the closed control behind it
    expect(
      screen.getAllByText("sample_mouse2p_image.tiff (input_kt62vwavq2)"),
    ).toHaveLength(2)
    fireEvent.click(
      screen.getByRole("option", { name: "sample_mouse2p_image.tiff" }),
    )
    expect(onSelect).toHaveBeenCalledWith(
      "input_kt62vwavq2",
      "/input/sample_mouse2p_image.tiff",
      DATA_TYPE_SET.CSV,
      undefined,
    )
  })

  it("lists every file of a multi-file input node", () => {
    const state = buildState({
      flowNodes: [
        {
          id: "input_zz1",
          data: { label: "image1.tiff ... and 1 files", type: "input" },
        },
      ],
      inputNode: {
        input_zz1: {
          fileType: "image",
          selectedFilePath: ["/input/image1.tiff", "/input/image2.tiff"],
          param: {},
        },
      },
    })
    const consoleError = jest
      .spyOn(console, "error")
      .mockImplementation(() => undefined)
    const { view } = renderSelect(state)
    openMenu()

    expect(
      view.baseElement.querySelectorAll(".MuiListSubheader-root"),
    ).toHaveLength(1)
    expect(
      screen.getByText("image1.tiff ... and 1 files (input_zz1)"),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("option", { name: "image1.tiff" }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("option", { name: "image2.tiff" }),
    ).toBeInTheDocument()
    expect(
      consoleError.mock.calls.some((args) =>
        String(args[0]).includes("same key"),
      ),
    ).toBe(false)
  })

  it("omits an input node whose file list is empty", () => {
    const state = buildState({
      flowNodes: [{ id: "input_empty", data: { label: "", type: "input" } }],
      inputNode: {
        input_empty: { fileType: "image", selectedFilePath: [], param: {} },
      },
    })
    const { view } = renderSelect(state)

    expect(screen.getByText("no data")).toBeInTheDocument()
    openMenu()
    expect(
      view.baseElement.querySelectorAll(".MuiListSubheader-root"),
    ).toHaveLength(0)
  })

  it("omits nodes whose outputs are all filtered out by dataType", () => {
    renderSelect(twoEtaNodesState, { dataType: DATA_TYPE_SET.ROI })

    expect(screen.getByText("no data")).toBeInTheDocument()

    openMenu()
    expect(screen.queryAllByRole("option")).toHaveLength(0)
    expect(screen.queryByText("eta")).not.toBeInTheDocument()
  })

  it("keeps a node whose outputs partially match the dataType", () => {
    renderSelect(twoEtaNodesState, { dataType: DATA_TYPE_SET.HEAT_MAP })
    openMenu()

    expect(screen.getByText(ETA_A)).toBeInTheDocument()
    expect(screen.getByText(ETA_B)).toBeInTheDocument()
    expect(screen.getAllByText("mean_heatmap")).toHaveLength(2)
    expect(screen.queryByText("mean")).not.toBeInTheDocument()
  })

  it("renders an empty value when the selection is no longer in the store", () => {
    // MUI warns about the out-of-range value, which is the point of the test
    jest.spyOn(console, "warn").mockImplementation(() => undefined)
    renderSelect(twoEtaNodesState, {
      selectedNodeId: "eta_fromanotherworkflow",
      selectedFilePath: "/output/stale/mean.json",
    })

    expect(screen.getByRole("combobox")).not.toHaveAttribute("title")
    expect(screen.getByRole("combobox")).not.toHaveTextContent("mean")
  })

  it("follows the node id when another input node takes over the same file", () => {
    const stateFor = (nodeId: string) =>
      buildState({
        flowNodes: [{ id: nodeId, data: { label: "data.csv", type: "input" } }],
        inputNode: {
          [nodeId]: {
            fileType: "csv",
            selectedFilePath: "/input/data.csv",
            param: {},
          },
        },
      })
    let state = stateFor("input_deleted")
    const store = mockStore(() => state)
    const onSelect = jest.fn()
    render(
      <Provider store={store}>
        <FilePathSelect
          selectedNodeId={null}
          selectedFilePath={null}
          onSelect={onSelect}
        />
      </Provider>,
    )

    state = stateFor("input_recreated")
    act(() => {
      store.dispatch({ type: "test/refresh" })
    })

    openMenu()
    fireEvent.click(screen.getByRole("option", { name: "data.csv" }))

    expect(onSelect).toHaveBeenCalledWith(
      "input_recreated",
      "/input/data.csv",
      DATA_TYPE_SET.CSV,
      undefined,
    )
  })

  it("keeps the node name visible when the id has no name prefix", () => {
    const legacyId = "V1StGXR8_Z5jdHi6B"
    const state = buildState({
      flowNodes: [{ id: legacyId, data: { label: "eta", type: "algorithm" } }],
      runResult: { [legacyId]: etaOutputs(legacyId) },
    })
    const { onSelect, view } = renderSelect(state)
    openMenu()

    expect(screen.getByText(`eta (${legacyId})`)).toBeInTheDocument()

    view.rerender(
      <Provider store={mockStore(state)}>
        <FilePathSelect
          selectedNodeId={legacyId}
          selectedFilePath={`/output/${legacyId}/mean.json`}
          onSelect={onSelect}
        />
      </Provider>,
    )
    // no name prefix to split on, so the whole id stays in the one text box
    expect(screen.getByText(`mean (${legacyId})`)).toBeInTheDocument()
  })
})

import { expect, describe, test } from "@jest/globals"

import { REACT_FLOW_NODE_TYPE_KEY } from "config/fileTypes.config"
import { INITIAL_IMAGE_ELEMENT_ID } from "const/flowchart"
import { WORKSPACE_TYPE } from "const/Workspace"
import { uploadFile } from "store/slice/FileUploader/FileUploaderActions"
import { addInputNode } from "store/slice/FlowElement/FlowElementActions"
import { selectNodeById } from "store/slice/FlowElement/FlowElementSelectors"
import { deleteFlowNodeById } from "store/slice/FlowElement/FlowElementSlice"
import { NODE_TYPE_SET } from "store/slice/FlowElement/FlowElementType"
import { setInputNodeFilePath } from "store/slice/InputNode/InputNodeActions"
import {
  selectFilePathIsUndefined,
  selectInputNodeById,
} from "store/slice/InputNode/InputNodeSelectors"
import {
  FILE_TYPE_SET,
  WORKSPACE_TYPE_KEY,
} from "store/slice/InputNode/InputNodeType"
import {
  isHDF5InputNode,
  isMatlabInputNode,
} from "store/slice/InputNode/InputNodeUtils"
import {
  fetchWorkflow,
  importWorkflowConfig,
} from "store/slice/Workflow/WorkflowActions"
import { getWorkspace } from "store/slice/Workspace/WorkspaceActions"
import { store, rootReducer } from "store/store"

describe("InputNode", () => {
  const initialRootState = store.getState()

  const nodeId = "input_cxg72pynkd"
  const nodeType = REACT_FLOW_NODE_TYPE_KEY.ImageFileNode
  const label = "image node"
  const nodeDataType = NODE_TYPE_SET.INPUT
  const fileType = FILE_TYPE_SET.IMAGE

  const addImageInputNodeAction = addInputNode({
    node: {
      id: nodeId,
      type: nodeType,
      data: { label, type: nodeDataType },
    },
    fileType,
  })

  // 追加
  test(addInputNode.type, () => {
    const targetState = rootReducer(initialRootState, addImageInputNodeAction)
    // FlowElement
    expect(selectNodeById(nodeId)(targetState)).toBeDefined()
    expect(selectNodeById(nodeId)(targetState)?.id).toBe(nodeId)
    expect(selectNodeById(nodeId)(targetState)?.type).toBe(nodeType)
    expect(selectNodeById(nodeId)(targetState)?.data).toEqual({
      label,
      type: nodeDataType,
    })
    // InputNode
    expect(selectInputNodeById(nodeId)(targetState).fileType).toBe(fileType)
  })

  // 削除
  test(deleteFlowNodeById.type, () => {
    const deleteFlowNodeByIdAction = deleteFlowNodeById(nodeId)
    const targetState = rootReducer(
      rootReducer(initialRootState, addImageInputNodeAction),
      deleteFlowNodeByIdAction,
    )
    // FlowElement
    expect(selectNodeById(nodeId)(targetState)).toBeUndefined()
    // InputNode
    expect(selectInputNodeById(nodeId)(targetState)).toBeUndefined()
  })

  // filePath変更
  test(`${setInputNodeFilePath.type} by image`, () => {
    const filePath = [
      "/tmp/optinist/input/hoge/hoge.tif",
      "/tmp/optinist/input/copy_image1/copy_image1.tif",
    ]
    const setInputNodeFilePathAction = setInputNodeFilePath({
      nodeId,
      filePath,
    })
    const targetState = rootReducer(
      rootReducer(initialRootState, addImageInputNodeAction),
      setInputNodeFilePathAction,
    )
    expect(selectInputNodeById(nodeId)(targetState).selectedFilePath).toEqual(
      filePath,
    )
  })

  test(`${setInputNodeFilePath.type} with empty string clears selection`, () => {
    const csvNodeId = "input_csv1"
    const withCsvNode = rootReducer(
      initialRootState,
      addInputNode({
        node: {
          id: csvNodeId,
          type: REACT_FLOW_NODE_TYPE_KEY.CsvFileNode,
          data: { label: "csv node", type: NODE_TYPE_SET.INPUT },
        },
        fileType: FILE_TYPE_SET.CSV,
      }),
    )
    const selectedState = rootReducer(
      withCsvNode,
      setInputNodeFilePath({ nodeId: csvNodeId, filePath: "c.csv" }),
    )
    expect(selectInputNodeById(csvNodeId)(selectedState).selectedFilePath).toBe(
      "c.csv",
    )
    const clearedState = rootReducer(
      selectedState,
      setInputNodeFilePath({ nodeId: csvNodeId, filePath: "" }),
    )
    expect(
      selectInputNodeById(csvNodeId)(clearedState).selectedFilePath,
    ).toBeUndefined()
  })

  test(`${uploadFile.fulfilled.type} sets path per filePathType`, () => {
    const csvNodeId = "input_csv2"
    const withNodes = rootReducer(
      rootReducer(initialRootState, addImageInputNodeAction),
      addInputNode({
        node: {
          id: csvNodeId,
          type: REACT_FLOW_NODE_TYPE_KEY.CsvFileNode,
          data: { label: "csv node", type: NODE_TYPE_SET.INPUT },
        },
        fileType: FILE_TYPE_SET.CSV,
      }),
    )

    const uploadTo = (
      state: ReturnType<typeof rootReducer>,
      targetNodeId: string,
      uploadFileType: string,
      resultPath: string,
    ) =>
      rootReducer(
        rootReducer(state, {
          type: uploadFile.pending.type,
          meta: {
            arg: {
              requestId: "req1",
              nodeId: targetNodeId,
              fileType: uploadFileType,
            },
          },
        }),
        {
          type: uploadFile.fulfilled.type,
          payload: { resultPath },
          meta: {
            arg: {
              requestId: "req1",
              nodeId: targetNodeId,
              fileType: uploadFileType,
            },
          },
        },
      )

    const afterImageUpload = uploadTo(
      withNodes,
      nodeId,
      FILE_TYPE_SET.IMAGE,
      "up.tif",
    )
    expect(
      selectInputNodeById(nodeId)(afterImageUpload).selectedFilePath,
    ).toEqual(["up.tif"])

    const afterCsvUpload = uploadTo(
      withNodes,
      csvNodeId,
      FILE_TYPE_SET.CSV,
      "up.csv",
    )
    expect(
      selectInputNodeById(csvNodeId)(afterCsvUpload).selectedFilePath,
    ).toBe("up.csv")
  })
})

describe("InputNode importWorkflowConfig", () => {
  const initialRootState = store.getState()

  // backend shape: param/hdf5Path/matPath always present, null when unset
  const inputNodePostData = (
    id: string,
    fileType: string,
    data: Record<string, unknown> = {},
  ) => ({
    id,
    type: "FileNode",
    data: {
      label: id,
      type: NODE_TYPE_SET.INPUT,
      fileType,
      param: {},
      hdf5Path: null,
      matPath: null,
      ...data,
    },
    position: { x: 0, y: 0 },
    style: { border: "1px solid #777", height: 120 },
  })

  const importPayload = {
    nodeDict: {
      image_array: inputNodePostData("image_array", FILE_TYPE_SET.IMAGE, {
        path: ["dir/a.tif"],
      }),
      image_string: inputNodePostData("image_string", FILE_TYPE_SET.IMAGE, {
        path: "b.tif",
      }),
      hdf5_full: inputNodePostData("hdf5_full", FILE_TYPE_SET.HDF5, {
        path: "x.nwb",
        hdf5Path: "acquisition/data",
      }),
      csv_node: inputNodePostData("csv_node", FILE_TYPE_SET.CSV, {
        path: "c.csv",
        param: { setHeader: 0, setIndex: true, transpose: true },
      }),
      fluo_node: inputNodePostData("fluo_node", FILE_TYPE_SET.FLUO, {
        path: "f.csv",
        param: { setHeader: null, setIndex: false, transpose: true },
      }),
      matlab_node: inputNodePostData("matlab_node", FILE_TYPE_SET.MATLAB, {
        path: "m.mat",
        matPath: "field1",
      }),
      microscope_node: inputNodePostData(
        "microscope_node",
        FILE_TYPE_SET.MICROSCOPE,
        { path: "scope.nd2" },
      ),
      algo_node: {
        id: "algo_node",
        type: "AlgorithmNode",
        data: {
          label: "algo",
          type: NODE_TYPE_SET.ALGORITHM,
          path: "dummy/dummy_image2image",
          param: {},
        },
        position: { x: 0, y: 0 },
      },
    },
    edgeDict: {},
  }

  const importAction = {
    type: importWorkflowConfig.fulfilled.type,
    payload: importPayload,
  }

  test("restores selectedFilePath, hdf5Path, matPath and params", () => {
    const state = rootReducer(initialRootState, importAction)

    expect(selectInputNodeById("image_array")(state).selectedFilePath).toEqual([
      "dir/a.tif",
    ])
    // string path on an array-type node is normalized to an array
    expect(selectInputNodeById("image_string")(state).selectedFilePath).toEqual(
      ["b.tif"],
    )

    const hdf5Node = selectInputNodeById("hdf5_full")(state)
    expect(hdf5Node.selectedFilePath).toBe("x.nwb")
    expect(isHDF5InputNode(hdf5Node) ? hdf5Node.hdf5Path : undefined).toBe(
      "acquisition/data",
    )

    expect(selectInputNodeById("csv_node")(state).selectedFilePath).toBe(
      "c.csv",
    )
    expect(selectInputNodeById("csv_node")(state).param).toEqual({
      setHeader: 0,
      setIndex: true,
      transpose: true,
    })

    // fluo maps to csv in the factory; its saved param must survive
    expect(selectInputNodeById("fluo_node")(state).param).toEqual({
      setHeader: null,
      setIndex: false,
      transpose: true,
    })

    const matlabNode = selectInputNodeById("matlab_node")(state)
    expect(matlabNode.selectedFilePath).toBe("m.mat")
    expect(isMatlabInputNode(matlabNode) ? matlabNode.matPath : undefined).toBe(
      "field1",
    )

    expect(selectInputNodeById("microscope_node")(state).selectedFilePath).toBe(
      "scope.nd2",
    )

    expect(selectInputNodeById("algo_node")(state)).toBeUndefined()
  })

  test("opens the run gate when all inputs have paths", () => {
    expect(selectFilePathIsUndefined(initialRootState)).toBe(true)
    const state = rootReducer(initialRootState, importAction)
    expect(selectFilePathIsUndefined(state)).toBe(false)
  })

  test("null/empty paths stay unselected and keep the run gate closed", () => {
    const state = rootReducer(initialRootState, {
      type: importWorkflowConfig.fulfilled.type,
      payload: {
        nodeDict: {
          csv_null: inputNodePostData("csv_null", FILE_TYPE_SET.CSV, {
            path: null,
          }),
          csv_empty: inputNodePostData("csv_empty", FILE_TYPE_SET.CSV, {
            path: "",
          }),
          image_empty: inputNodePostData("image_empty", FILE_TYPE_SET.IMAGE, {
            path: "",
          }),
          csv_blank_array: inputNodePostData(
            "csv_blank_array",
            FILE_TYPE_SET.CSV,
            { path: [""] },
          ),
          image_blank_entry: inputNodePostData(
            "image_blank_entry",
            FILE_TYPE_SET.IMAGE,
            { path: [""] },
          ),
          hdf5_null_special: inputNodePostData(
            "hdf5_null_special",
            FILE_TYPE_SET.HDF5,
            { path: "x.nwb", hdf5Path: null },
          ),
          hdf5_empty_special: inputNodePostData(
            "hdf5_empty_special",
            FILE_TYPE_SET.HDF5,
            { path: "y.nwb", hdf5Path: "" },
          ),
        },
        edgeDict: {},
      },
    })

    expect(
      selectInputNodeById("csv_null")(state).selectedFilePath,
    ).toBeUndefined()
    expect(
      selectInputNodeById("csv_empty")(state).selectedFilePath,
    ).toBeUndefined()
    expect(
      selectInputNodeById("image_empty")(state).selectedFilePath,
    ).toBeUndefined()
    expect(
      selectInputNodeById("csv_blank_array")(state).selectedFilePath,
    ).toBeUndefined()
    expect(
      selectInputNodeById("image_blank_entry")(state).selectedFilePath,
    ).toBeUndefined()
    // param {} from a hand-edited yaml is filled with the csv defaults
    expect(selectInputNodeById("csv_empty")(state).param).toEqual({
      setHeader: null,
      setIndex: false,
      transpose: false,
    })
    const hdf5Node = selectInputNodeById("hdf5_null_special")(state)
    expect(
      isHDF5InputNode(hdf5Node) ? hdf5Node.hdf5Path : "not hdf5",
    ).toBeUndefined()
    const hdf5EmptyNode = selectInputNodeById("hdf5_empty_special")(state)
    expect(
      isHDF5InputNode(hdf5EmptyNode) ? hdf5EmptyNode.hdf5Path : "not hdf5",
    ).toBeUndefined()
    expect(selectFilePathIsUndefined(state)).toBe(true)
  })

  test("unsupported fileType is dropped from input node state", () => {
    const state = rootReducer(initialRootState, {
      type: importWorkflowConfig.fulfilled.type,
      payload: {
        nodeDict: {
          bogus: inputNodePostData("bogus", "unsupported_type"),
        },
        edgeDict: {},
      },
    })
    expect(selectInputNodeById("bogus")(state)).toBeUndefined()
  })

  test("replaces previous nodes and preserves workspace type", () => {
    const seededState = rootReducer(
      rootReducer(initialRootState, {
        type: getWorkspace.fulfilled.type,
        payload: { id: 1, name: "ws", user: { id: 1 } },
      }),
      addInputNode({
        node: {
          id: "input_old",
          type: REACT_FLOW_NODE_TYPE_KEY.ImageFileNode,
          data: { label: "old", type: NODE_TYPE_SET.INPUT },
        },
        fileType: FILE_TYPE_SET.IMAGE,
      }),
    )
    expect(selectInputNodeById("input_old")(seededState)).toBeDefined()

    const state = rootReducer(seededState, importAction)
    expect(selectInputNodeById("input_old")(state)).toBeUndefined()
    expect(selectInputNodeById(INITIAL_IMAGE_ELEMENT_ID)(state)).toBeUndefined()
    expect(state.inputNode[WORKSPACE_TYPE_KEY]).toBe(WORKSPACE_TYPE.DEFAULT)
  })

  test("import and fetch produce identical input node state", () => {
    const importedState = rootReducer(initialRootState, importAction)
    const fetchedState = rootReducer(initialRootState, {
      type: fetchWorkflow.fulfilled.type,
      payload: {
        ...importPayload,
        unique_id: "u1",
        name: "parity",
        success: "success",
        started_at: "2026-01-01 00:00:00",
        finished_at: "2026-01-01 00:00:10",
        function: {},
      },
    })
    expect(importedState.inputNode).toEqual(fetchedState.inputNode)
  })

  test("empty-string path via fetchWorkflow keeps the run gate closed", () => {
    const state = rootReducer(initialRootState, {
      type: fetchWorkflow.fulfilled.type,
      payload: {
        nodeDict: {
          csv_empty: inputNodePostData("csv_empty", FILE_TYPE_SET.CSV, {
            path: "",
          }),
        },
        edgeDict: {},
        unique_id: "u2",
        name: "gate",
        success: "success",
        started_at: "2026-01-01 00:00:00",
        finished_at: "2026-01-01 00:00:10",
        function: {},
      },
    })
    expect(
      selectInputNodeById("csv_empty")(state).selectedFilePath,
    ).toBeUndefined()
    expect(selectFilePathIsUndefined(state)).toBe(true)
  })
})

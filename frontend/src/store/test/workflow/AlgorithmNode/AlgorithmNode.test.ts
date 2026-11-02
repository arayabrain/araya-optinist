import { expect, describe, test } from "@jest/globals"

import { REACT_FLOW_NODE_TYPE_KEY } from "config/fileTypes.config"
import {
  selectAlgorithmIsUpdated,
  selectAlgorithmNodeById,
  selectAlgorithmParamsValue,
} from "store/slice/AlgorithmNode/AlgorithmNodeSelectors"
import {
  markDownstreamStale,
  updateParam,
} from "store/slice/AlgorithmNode/AlgorithmNodeSlice"
import { addAlgorithmNode } from "store/slice/FlowElement/FlowElementActions"
import {
  isParentNodeUpdatedParams,
  selectNodeById,
} from "store/slice/FlowElement/FlowElementSelectors"
import { deleteFlowNodeById } from "store/slice/FlowElement/FlowElementSlice"
import { NODE_TYPE_SET } from "store/slice/FlowElement/FlowElementType"
import { runByCurrentUid } from "store/slice/Pipeline/PipelineActions"
import { selectRunPostData } from "store/slice/Run/RunSelectors"
import { importWorkflowConfig } from "store/slice/Workflow/WorkflowActions"
import { store, rootReducer } from "store/store"
import { convertToParamMap } from "utils/param/ParamUtils"

describe("AlgorithmNode", () => {
  const initialRootState = store.getState()

  const nodeId = "suite2p_roi_7tkgtwjw4x"
  const nodeType = REACT_FLOW_NODE_TYPE_KEY.AlgorithmNode
  const label = "suite2p_roi"
  const nodeDataType = NODE_TYPE_SET.ALGORITHM
  const name = "suite2p_roi"
  const functionPath = "suite2p/suite2p_roi"
  const algorithmParams = {
    param1: 1,
    param2: "param2",
    param3: {
      param4: 4,
    },
  }
  const addAlgorithmNodeAction = {
    type: addAlgorithmNode.fulfilled.type,
    meta: {
      arg: {
        node: {
          id: nodeId,
          type: nodeType,
          data: { label, type: nodeDataType },
        },
        name,
        functionPath,
      },
      requestId: "tXOx5oiVyChyZ1hEsqdcR",
      requestStatus: "fulfilled",
    },
    payload: algorithmParams,
  }

  // 追加
  test(addAlgorithmNode.fulfilled.type, () => {
    const targetState = rootReducer(initialRootState, addAlgorithmNodeAction)
    // FlowElement
    expect(selectNodeById(nodeId)(targetState)).toBeDefined()
    expect(selectNodeById(nodeId)(targetState)?.id).toBe(nodeId)
    expect(selectNodeById(nodeId)(targetState)?.type).toBe(nodeType)
    expect(selectNodeById(nodeId)(targetState)?.data).toEqual({
      label,
      type: nodeDataType,
    })
    // AlgorithmNode
    expect(selectAlgorithmNodeById(nodeId)(targetState).functionPath).toBe(
      functionPath,
    )
    expect(selectAlgorithmNodeById(nodeId)(targetState).name).toBe(name)
    expect(selectAlgorithmNodeById(nodeId)(targetState).originalValue).toBe(
      undefined,
    )
    expect(selectAlgorithmNodeById(nodeId)(targetState).params).toEqual({
      param1: {
        type: "child",
        value: algorithmParams.param1,
        path: "param1",
      },
      param2: {
        type: "child",
        value: algorithmParams.param2,
        path: "param2",
      },
      param3: {
        type: "parent",
        children: {
          param4: {
            type: "child",
            value: algorithmParams.param3.param4,
            path: "param3/param4",
          },
        },
      },
    })
  })

  // 削除
  test(deleteFlowNodeById.type, () => {
    const deleteFlowNodeByIdAction = deleteFlowNodeById(nodeId)
    const targetState = rootReducer(
      rootReducer(initialRootState, addAlgorithmNodeAction),
      deleteFlowNodeByIdAction,
    )
    // FlowElement
    expect(selectNodeById(nodeId)(targetState)).toBeUndefined()
    // AlgorithmNode
    expect(selectAlgorithmNodeById(nodeId)(targetState)).toBeUndefined()
  })

  // params編集
  test(updateParam.type, () => {
    const path = "param3/param4"
    const newValue = 10
    const initValue = 9
    const updateParamAction = updateParam({ nodeId, path, newValue, initValue })
    const targetState = rootReducer(
      rootReducer(initialRootState, addAlgorithmNodeAction),
      updateParamAction,
    )
    expect(selectAlgorithmNodeById(nodeId)(targetState).originalValue).toBe(
      undefined,
    )
    expect(selectAlgorithmParamsValue(nodeId, path)(targetState)).toBe(newValue)
  })

  // Edit ROI commit: descendants highlight, the node itself is not force-run
  test(markDownstreamStale.type, () => {
    const childId = "eta_child"
    const withChild = (state: ReturnType<typeof rootReducer>) => ({
      ...state,
      flowElement: {
        ...state.flowElement,
        flowEdges: [{ id: "e", source: nodeId, target: childId }],
      },
    })
    const added = rootReducer(initialRootState, addAlgorithmNodeAction)
    expect(isParentNodeUpdatedParams(childId)(withChild(added))).toBe(false)

    const stale = rootReducer(added, markDownstreamStale({ nodeId }))
    expect(isParentNodeUpdatedParams(childId)(withChild(stale))).toBe(true)
    expect(selectAlgorithmNodeById(nodeId)(stale).isUpdate).toBe(false)
    // never reloaded, so originalValue is unset: still not a forced rerun
    expect(selectAlgorithmIsUpdated(nodeId)(stale)).toBe(false)

    const ran = rootReducer(stale, {
      type: runByCurrentUid.fulfilled.type,
      meta: {
        arg: {
          runPostData: {
            nodeDict: {
              [nodeId]: { id: nodeId, data: { type: nodeDataType, label } },
            },
          },
        },
      },
    })
    expect(isParentNodeUpdatedParams(childId)(withChild(ran))).toBe(false)

    const unknown = rootReducer(
      initialRootState,
      markDownstreamStale({ nodeId }),
    )
    expect(selectAlgorithmNodeById(nodeId)(unknown)).toBeUndefined()
  })

  // Loading a workflow whose nodes were reconciled against the current yaml
  describe(importWorkflowConfig.fulfilled.type, () => {
    const algoNode = (id: string, param: Record<string, unknown>) => ({
      id,
      type: nodeType,
      position: { x: 0, y: 0 },
      data: {
        label,
        type: nodeDataType,
        path: functionPath,
        param: convertToParamMap(param),
      },
    })
    const loaded = rootReducer(initialRootState, {
      type: importWorkflowConfig.fulfilled.type,
      payload: {
        nodeDict: {
          gained: algoNode("gained", { a: 1, g: { b: 2, c: 3 } }),
          dropped: algoNode("dropped", { a: 1 }),
        },
        edgeDict: {},
        paramChanges: [
          { nodeId: "gained", name: label, added: ["g/c"], removed: [] },
          { nodeId: "dropped", name: label, added: [], removed: ["old"] },
        ],
      },
    })
    const withChild = (state: ReturnType<typeof rootReducer>) => ({
      ...state,
      flowElement: {
        ...state.flowElement,
        flowEdges: [{ id: "e", source: "gained", target: "child" }],
      },
    })
    const forced = [{ nodeId: "gained", name: label }]

    test("a node that gained keys is updated and force-run", () => {
      expect(selectAlgorithmIsUpdated("gained")(loaded)).toBe(true)
      expect(selectRunPostData(loaded).forceRunList).toEqual(forced)
    })

    test("a node that only lost keys is not: the backend ignored them", () => {
      expect(selectAlgorithmIsUpdated("dropped")(loaded)).toBe(false)
    })

    test("the descendants of a node that gained keys are highlighted", () => {
      expect(isParentNodeUpdatedParams("child")(withChild(loaded))).toBe(true)
    })

    test("an edit that is set back keeps the mark", () => {
      const edit = (value: number) =>
        updateParam({
          nodeId: "gained",
          path: "a",
          newValue: value,
          initValue: 1,
        })
      const editedBack = rootReducer(rootReducer(loaded, edit(2)), edit(1))
      expect(selectAlgorithmIsUpdated("gained")(editedBack)).toBe(true)
      expect(selectRunPostData(editedBack).forceRunList).toEqual(forced)
    })

    test("a run clears the mark", () => {
      const ran = rootReducer(loaded, {
        type: runByCurrentUid.fulfilled.type,
        meta: {
          arg: {
            runPostData: {
              nodeDict: {
                gained: { id: "gained", data: { type: nodeDataType, label } },
                dropped: { id: "dropped", data: { type: nodeDataType, label } },
              },
            },
          },
        },
      })
      expect(selectAlgorithmIsUpdated("gained")(ran)).toBe(false)
      expect(isParentNodeUpdatedParams("child")(withChild(ran))).toBe(false)
    })
  })
})

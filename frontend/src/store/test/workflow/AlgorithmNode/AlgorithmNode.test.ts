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
import { store, rootReducer } from "store/store"

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
})

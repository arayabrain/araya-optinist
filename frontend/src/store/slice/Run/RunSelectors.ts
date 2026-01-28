import { Node } from "reactflow"

import { createSelector } from "reselect"

import {
  AlgorithmNodePostData,
  EdgeDict,
  InputNodePostData,
  NodeDict,
  RunPostData,
} from "api/run/Run"
import {
  selectAlgorithmFilterParams,
  selectAlgorithmFunctionPath,
  selectAlgorithmIsUpdated,
  selectAlgorithmName,
  selectAlgorithmParams,
} from "store/slice/AlgorithmNode/AlgorithmNodeSelectors"
import {
  selectFlowEdges,
  selectFlowNodes,
} from "store/slice/FlowElement/FlowElementSelectors"
import { NODE_TYPE_SET } from "store/slice/FlowElement/FlowElementType"
import { isAlgorithmNodeData } from "store/slice/FlowElement/FlowElementUtils"
import {
  selectInputNodeFileType,
  selectInputNodeHDF5Path,
  selectInputNodeMatlabPath,
  selectInputNodeParam,
  selectInputNodeSelectedFilePath,
} from "store/slice/InputNode/InputNodeSelectors"
import { selectNwbParams } from "store/slice/NWB/NWBSelectors"
import { selectPipelineNodeResultStatus } from "store/slice/Pipeline/PipelineSelectors"
import { NODE_RESULT_STATUS } from "store/slice/Pipeline/PipelineType"
import { selectSnakemakeParams } from "store/slice/Snakemake/SnakemakeSelectors"
import { RootState } from "store/store"

/**
 * 前回の結果で、エラーまたはParamに変更があるnodeのリストを返す
 */
const selectForceRunList = (state: RootState) => {
  const nodes = selectFlowNodes(state)
  return nodes
    .filter(isAlgorithmNodeData)
    .filter((node) => {
      const isUpdated = selectAlgorithmIsUpdated(node.id)(state)
      const status = selectPipelineNodeResultStatus(node.id)(state)
      return isUpdated || status === NODE_RESULT_STATUS.ERROR
    })
    .map((node) => ({
      nodeId: node.id,
      name: selectAlgorithmName(node.id)(state),
    }))
}

const selectNodeDictForRun = (state: RootState): NodeDict => {
  const nodes = selectFlowNodes(state)
  const nodeDict: NodeDict = {}
  nodes.forEach((node) => {
    if (isAlgorithmNodeData(node)) {
      const param = selectAlgorithmParams(node.id)(state) ?? {}
      const dataFilterParam = selectAlgorithmFilterParams(node.id)(state)
      const functionPath = selectAlgorithmFunctionPath(node.id)(state)
      const algorithmNodePostData: Node<AlgorithmNodePostData> = {
        ...node,
        data: {
          ...node.data,
          label: node.data?.label ?? "",
          type: NODE_TYPE_SET.ALGORITHM,
          path: functionPath,
          param,
          dataFilterParam,
          draftDataFilterParam: dataFilterParam,
        },
      }
      nodeDict[node.id] = algorithmNodePostData
    } else {
      const filePath = selectInputNodeSelectedFilePath(node.id)(state)
      const fileType = selectInputNodeFileType(node.id)(state)
      const param = selectInputNodeParam(node.id)(state)
      const hdf5Path = selectInputNodeHDF5Path(node.id)(state)
      const matPath = selectInputNodeMatlabPath(node.id)(state)
      const inputNodePosyData: Node<InputNodePostData> = {
        ...node,
        data: {
          ...node.data,
          label: node.data?.label ?? "",
          type: NODE_TYPE_SET.INPUT,
          path: filePath ?? "",
          param,
          matPath: matPath,
          hdf5Path: hdf5Path,
          fileType,
        },
      }
      nodeDict[node.id] = inputNodePosyData
    }
  })
  return nodeDict
}

const selectEdgeDictForRun = (state: RootState) => {
  const edgeDict: EdgeDict = {}
  selectFlowEdges(state).forEach((edge) => {
    edgeDict[edge.id] = edge
  })
  return edgeDict
}

/**
 * Timezone constants for frontend/backend synchronization.
 * Backend equivalent: studio/app/common/core/utils/datetime_utils.py
 * Keep these values in sync when updating either file.
 */
const TIMEZONE_UTC = "UTC" // Matches TIMEZONE_UTC in datetime_utils.py
const TIMEZONE_KEY = "timezone" // Matches TIMEZONE_KEY in datetime_utils.py

/**
 * Get the user's browser timezone (IANA format).
 * This is used for user-facing timestamps (NWB files, experiment logs)
 * so researchers can correlate experiment times with their local records.
 */
const getBrowserTimezone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    // Fallback to UTC if browser doesn't support Intl API
    return TIMEZONE_UTC
  }
}

export const selectRunPostData = createSelector(
  selectNwbParams,
  selectSnakemakeParams,
  selectEdgeDictForRun,
  selectNodeDictForRun,
  selectForceRunList,
  (
    nwbParams,
    snakemakeParams,
    edgeDictForRun,
    nodeDictForRun,
    forceRunList,
  ) => {
    // Include browser timezone in nwbParam for user-facing timestamps
    // Wrapped in ParamChild structure to match ParamMap type
    const nwbParamWithTimezone = {
      ...nwbParams,
      [TIMEZONE_KEY]: {
        type: "child" as const,
        value: getBrowserTimezone(),
        path: TIMEZONE_KEY,
      },
    }

    const runPostData: Omit<RunPostData, "name"> = {
      nwbParam: nwbParamWithTimezone,
      snakemakeParam: snakemakeParams,
      edgeDict: edgeDictForRun,
      nodeDict: nodeDictForRun,
      forceRunList,
    }
    return runPostData
  },
)

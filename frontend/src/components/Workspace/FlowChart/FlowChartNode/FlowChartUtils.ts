import { Connection } from "reactflow"

import { TreeNodeType } from "components/Workspace/FlowChart/FlowChartNode/BaseStructuredFileNode"
import {
  isHDF5InputNode,
  isMatlabInputNode,
} from "store/slice/InputNode/InputNodeUtils"
import { RootState, store } from "store/store"

export const STRUCTURED_HANDLE_TYPES = ["HDF5Data", "MatlabData"]

export function toHandleId(nodeId: string, name: string, type: string) {
  return `${nodeId}--${name}--${type}`
}

export function getHandleType(handleId: string) {
  return handleId.split("--")[2]
}

export function getHandleNodeId(handleId: string) {
  return handleId.split("--")[0]
}

export function findDatasetShape(
  tree: TreeNodeType[] | undefined,
  path: string | undefined,
): number[] | undefined {
  if (!tree || !path) return undefined
  for (const node of tree) {
    if (node.isDir) {
      const found = findDatasetShape(node.nodes, path)
      if (found) return found
    } else if (node.path === path && node.shape) {
      return node.shape
    }
  }
  return undefined
}

export function handleTypeForRank(ndim: number | undefined, fallback: string) {
  if (ndim == null) return fallback
  if (ndim >= 3) return "ImageData"
  if (ndim === 2) return "FluoData"
  return "IscellData"
}

// ponytail: rank is the only thing the file tells us, same rule as the backend precheck
export function isRankCompatible(
  ndim: number | undefined,
  targetType: string,
): boolean {
  if (ndim == null || targetType === "BaseData") return true
  return ndim >= 3 === (targetType === "ImageData")
}

export function selectStructuredDatasetNdim(nodeId: string) {
  return (state: RootState): number | undefined => {
    const inputNode = state.inputNode[nodeId]
    if (inputNode == null) return undefined
    if (isHDF5InputNode(inputNode)) {
      const filePath = inputNode.selectedFilePath
      const tree = filePath ? state.hdf5.trees[filePath]?.tree : undefined
      return findDatasetShape(tree, inputNode.hdf5Path)?.length
    }
    if (isMatlabInputNode(inputNode)) {
      const filePath = inputNode.selectedFilePath
      const tree = filePath ? state.matlab.trees[filePath]?.tree : undefined
      return findDatasetShape(tree, inputNode.matPath)?.length
    }
    return undefined
  }
}

export function isValidConnection(connection: Connection) {
  if (connection.sourceHandle != null && connection.targetHandle != null) {
    const source = getHandleType(connection.sourceHandle)
    const target = getHandleType(connection.targetHandle)
    if (STRUCTURED_HANDLE_TYPES.includes(source)) {
      const ndim = selectStructuredDatasetNdim(
        getHandleNodeId(connection.sourceHandle),
      )(store.getState())
      return isRankCompatible(ndim, target)
    }
    // NOTE: SpikingActivityData is the same as FluoData. Just renamed for suite2p_spike_deconv.
    if (source === "SpikingActivityData") {
      return target === "FluoData" || target === "SpikingActivityData"
    } else if (target === "BaseData" || source === "BaseData") {
      return true
    } else {
      return source === target
    }
  } else {
    return true
  }
}

import { FILE_TREE_TYPE, SyncStatus } from "api/files/Files"
import { TreeNodeType } from "store/slice/FilesTree/FilesTreeType"
import { RootState } from "store/store"

export const selectFilesTree =
  (fileType: FILE_TREE_TYPE) => (state: RootState) => {
    if (state.filesTree[fileType] != null) {
      return state.filesTree[fileType]
    } else {
      return undefined
    }
  }

export const selectFilesTreeNodes =
  (fileType: FILE_TREE_TYPE) => (state: RootState) =>
    selectFilesTree(fileType)(state)?.tree

export const selectFilesIsLatest =
  (fileType: FILE_TREE_TYPE) => (state: RootState) =>
    selectFilesTree(fileType)(state)?.isLatest ?? false

export const selectFilesIsLoading =
  (fileType: FILE_TREE_TYPE) => (state: RootState) =>
    selectFilesTree(fileType)(state)?.isLoading ?? false

// Helper function to find a node by path in the tree
function findNodeByPath(
  tree: TreeNodeType[] | undefined,
  path: string,
): TreeNodeType | undefined {
  if (!tree) return undefined
  for (const node of tree) {
    if (node.path === path) return node
    if (node.isDir) {
      const found = findNodeByPath(node.nodes, path)
      if (found) return found
    }
  }
  return undefined
}

export const selectFileSyncStatus =
  (fileType: FILE_TREE_TYPE, filePath: string) =>
  (state: RootState): SyncStatus | undefined => {
    const tree = selectFilesTreeNodes(fileType)(state)
    const node = findNodeByPath(tree, filePath)
    return node?.syncStatus
  }

import {
  SyncStatus,
  TreeNodeTypeDTO,
  TreeNodeWithSyncDTO,
} from "api/files/Files"
import { TreeNodeType } from "store/slice/FilesTree/FilesTreeType"

// Helper to convert snake_case sync_status to camelCase syncStatus
function convertSyncStatus(dto: TreeNodeWithSyncDTO): SyncStatus | undefined {
  return (dto as TreeNodeWithSyncDTO).sync_status as SyncStatus | undefined
}

export function convertToTreeNodeType(
  dto: TreeNodeTypeDTO[] | TreeNodeWithSyncDTO[],
): TreeNodeType[] {
  return dto.map((node) => {
    const syncStatus = convertSyncStatus(node as TreeNodeWithSyncDTO)
    const size = (node as TreeNodeWithSyncDTO).size

    return node.isdir
      ? {
          path: node.path,
          name: node.name,
          isDir: true,
          nodes: convertToTreeNodeType(node.nodes as TreeNodeWithSyncDTO[]),
          shape: node.shape,
          syncStatus,
          size,
        }
      : {
          path: node.path,
          name: node.name,
          isDir: false,
          shape: node.shape,
          syncStatus,
          size,
        }
  })
}

export function isDirNodeByPath(path: string, tree: TreeNodeType[]): boolean {
  const node = getNodeByPath(path, tree)
  if (node != null) {
    return node.isDir
  } else {
    throw new Error(`failed to get node: ${path}`)
  }
}

export function getNodeByPath(
  path: string,
  tree: TreeNodeType[],
): TreeNodeType | null {
  let targetNode: TreeNodeType | null = null
  for (const node of tree) {
    if (path === node.path) {
      targetNode = node
      break
    } else {
      if (node.isDir) {
        targetNode = getNodeByPath(path, node.nodes)
        if (targetNode != null) {
          break
        }
      }
    }
  }
  return targetNode
}

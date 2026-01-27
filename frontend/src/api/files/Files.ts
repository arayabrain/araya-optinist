import { AxiosProgressEvent } from "axios"

import { FILE_TREE_TYPE_SET } from "config/fileTypes.config"
import { API_TIMEOUT, BASE_URL } from "const/API"
import { SyncStatus } from "store/slice/FilesTree/FilesTreeType"
import axios from "utils/axios"

// Re-export for convenience - FILE_TREE_TYPE depends on FILE_TREE_TYPE_SET
export { FILE_TREE_TYPE_SET }

export type FILE_TREE_TYPE =
  (typeof FILE_TREE_TYPE_SET)[keyof typeof FILE_TREE_TYPE_SET]

export type TreeNodeTypeDTO = DirNodeDTO | FileNodeDTO

export interface NodeBaseDTO {
  path: string
  name: string
  isdir: boolean
  shape: []
}

export interface DirNodeDTO extends NodeBaseDTO {
  isdir: true
  nodes: TreeNodeTypeDTO[]
}

export interface FileNodeDTO extends NodeBaseDTO {
  isdir: false
}

// Extended types with sync status for merged endpoint
export type TreeNodeWithSyncDTO = DirNodeWithSyncDTO | FileNodeWithSyncDTO

export interface NodeWithSyncBaseDTO extends NodeBaseDTO {
  sync_status: SyncStatus
  size?: number
}

export interface DirNodeWithSyncDTO extends NodeWithSyncBaseDTO {
  isdir: true
  nodes: TreeNodeWithSyncDTO[]
}

export interface FileNodeWithSyncDTO extends NodeWithSyncBaseDTO {
  isdir: false
}

export type GetStatusViaUrl = {
  total: number
  current: number
  error: string | null
}

export async function getFilesTreeApi(
  workspaceId: number,
  fileType: FILE_TREE_TYPE,
): Promise<TreeNodeTypeDTO[]> {
  const response = await axios.get(`${BASE_URL}/files/${workspaceId}`, {
    params: {
      file_type: fileType,
    },
  })
  return response.data
}

export async function getFilesTreeMergedApi(
  workspaceId: number,
  fileType: FILE_TREE_TYPE,
): Promise<TreeNodeWithSyncDTO[]> {
  const response = await axios.get(`${BASE_URL}/files/${workspaceId}/merged`, {
    params: {
      file_type: fileType,
    },
  })
  return response.data
}

export async function syncInputFileApi(
  workspaceId: number,
  fileName: string,
): Promise<{ file_path: string }> {
  const response = await axios.post(
    `${BASE_URL}/files/${workspaceId}/sync/${encodeURIComponent(fileName)}`,
  )
  return response.data
}

export async function uploadFileApi(
  workspaceId: number,
  fileName: string,
  config: {
    onUploadProgress: (progressEvent: AxiosProgressEvent) => void
  },
  formData: FormData,
): Promise<{ file_path: string }> {
  const response = await axios.post(
    `${BASE_URL}/files/${workspaceId}/upload/${fileName}`,
    formData,
    {
      ...config,
      // Use extended timeout for file uploads to support large files
      timeout: API_TIMEOUT.UPLOAD_DOWNLOAD,
      headers: {
        // Let axios auto-detect Content-Type for multipart/form-data with boundary
        "Content-Type": undefined,
      },
    },
  )
  return response.data
}

export async function deleteFileApi(
  workspaceId: number,
  fileName: string,
): Promise<boolean> {
  const response = await axios.delete(
    `${BASE_URL}/files/${workspaceId}/delete/${fileName}`,
  )
  return response.data
}

export async function updateShapeApi(
  workspaceId: number,
  fileName: string,
): Promise<boolean> {
  const response = await axios.post(
    `${BASE_URL}/files/${workspaceId}/shape/${fileName}`,
  )
  return response.data
}

export const uploadViaUrlApi = async (
  workspaceId: number,
  url: string,
): Promise<{ file_name: string }> => {
  const res = await axios.post(
    `${BASE_URL}/files/${workspaceId}/download`,
    { url },
    {
      // Use extended timeout for URL-based file downloads to support large remote files
      timeout: API_TIMEOUT.UPLOAD_DOWNLOAD,
    },
  )
  return res.data
}

export const getStatusLoadViaUrlApi = async (
  workspaceId: number,
  file_name: string,
): Promise<GetStatusViaUrl> => {
  const res = await axios.get(
    `${BASE_URL}/files/${workspaceId}/download/status?file_name=${file_name}`,
  )
  return res.data
}

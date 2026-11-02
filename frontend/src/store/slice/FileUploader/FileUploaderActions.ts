import { AxiosProgressEvent } from "axios"

import { createAsyncThunk, createAction } from "@reduxjs/toolkit"

import {
  getStatusLoadViaUrlApi,
  GetStatusViaUrl,
  updateShapeApi,
  uploadFileApi,
  uploadViaUrlApi,
  UploadViaUrlStarted,
  FILE_TREE_TYPE_SET,
} from "api/files/Files"
import { getFilesTree } from "store/slice/FilesTree/FilesTreeAction"
import { FILE_UPLOADER_SLICE_NAME } from "store/slice/FileUploader/FileUploaderType"
import { FILE_TYPE } from "store/slice/InputNode/InputNodeType"

export const setUploadProgress = createAction<{
  requestId: string
  progress: number
  total: number
}>(`${FILE_UPLOADER_SLICE_NAME}/setUploadProgress`)

export const setDownloadProgress = createAction<{
  requestId: string
  progress: number
}>(`${FILE_UPLOADER_SLICE_NAME}/setDownloadProgress`)

export const uploadFile = createAsyncThunk<
  {
    resultPath: string
  },
  {
    workspaceId: number
    requestId: string
    nodeId?: string
    fileName: string
    formData: FormData
    fileType?: FILE_TYPE
  }
>(
  `${FILE_UPLOADER_SLICE_NAME}/uploadFile`,
  async ({ workspaceId, requestId, fileName, formData }, thunkAPI) => {
    try {
      const config = getUploadConfig((percent, total) => {
        thunkAPI.dispatch(
          setUploadProgress({
            requestId,
            progress: percent,
            total,
          }),
        )
      })
      const response = await uploadFileApi(
        workspaceId,
        fileName,
        config,
        formData,
      )
      return {
        resultPath: response.file_path,
      }
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

function getUploadConfig(
  onUpdateProgressFn: (percent: number, totalSize: number) => void,
) {
  return {
    onUploadProgress: function (progressEvent: AxiosProgressEvent) {
      if (!progressEvent.total) {
        onUpdateProgressFn(0, 100)
      } else {
        const percentCompleted = Math.round(
          (progressEvent.loaded * 100) / progressEvent.total,
        )
        onUpdateProgressFn(percentCompleted, progressEvent.total)
      }
    },
  }
}

export const updateShape = createAsyncThunk<
  boolean,
  {
    workspaceId: number
    fileName: string
  }
>(
  `${FILE_UPLOADER_SLICE_NAME}/updateShape`,
  async ({ workspaceId, fileName }, thunkAPI) => {
    const { dispatch } = thunkAPI
    try {
      const response = await updateShapeApi(workspaceId, fileName)
      dispatch(
        getFilesTree({ workspaceId, fileType: FILE_TREE_TYPE_SET.IMAGE }),
      )
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const uploadViaUrl = createAsyncThunk<
  UploadViaUrlStarted,
  { workspaceId: number; url: string; requestId: string }
>(`${FILE_UPLOADER_SLICE_NAME}/uploadFileViaUrl`, async (data, thunkAPI) => {
  const { workspaceId, url } = data
  try {
    const data = await uploadViaUrlApi(workspaceId, url)
    return data
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

type UrlUploadArg = {
  workspaceId: number
  file_name: string
  upload_id: string
  requestId: string
}

export const getStatusLoadViaUrl = createAsyncThunk<
  GetStatusViaUrl,
  UrlUploadArg
>(`${FILE_UPLOADER_SLICE_NAME}/getStatusLoadViaUrl`, async (data, thunkAPI) => {
  const { workspaceId, file_name, upload_id, requestId } = data
  try {
    const data = await getStatusLoadViaUrlApi(workspaceId, file_name, upload_id)
    thunkAPI.dispatch(
      setUploadProgress({
        requestId,
        // total is 0 when the server sent no Content-Length
        progress:
          data.total > 0 ? Math.min(data.current / data.total, 1) * 100 : 0,
        total: data.total,
      }),
    )
    return data
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const URL_UPLOAD_POLL_INTERVAL_MS = 1000
// A poll that reaches a worker other than the downloading one gets a 404
export const URL_UPLOAD_MAX_FAILED_POLLS = 30

export const waitForUploadViaUrl = createAsyncThunk<
  void,
  UrlUploadArg,
  { rejectValue: string }
>(`${FILE_UPLOADER_SLICE_NAME}/waitForUploadViaUrl`, async (arg, thunkAPI) => {
  let failedPolls = 0
  for (;;) {
    const result = await thunkAPI.dispatch(getStatusLoadViaUrl(arg))
    if (getStatusLoadViaUrl.fulfilled.match(result)) {
      failedPolls = 0
      const { done, error } = result.payload
      if (error) return thunkAPI.rejectWithValue(error)
      if (done) return
    } else if (++failedPolls >= URL_UPLOAD_MAX_FAILED_POLLS) {
      return thunkAPI.rejectWithValue("The upload status is not available")
    }
    await new Promise((resolve) =>
      setTimeout(resolve, URL_UPLOAD_POLL_INTERVAL_MS),
    )
  }
})

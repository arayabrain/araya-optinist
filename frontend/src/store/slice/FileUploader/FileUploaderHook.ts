import { useCallback, useRef, useEffect } from "react"
import { useDispatch, useSelector } from "react-redux"

import { nanoid } from "@reduxjs/toolkit"

import { uploadFile } from "store/slice/FileUploader/FileUploaderActions"
import {
  selectFileUploadIsPending,
  selectUploadFilePath,
  selectFileUploadIsFulfilled,
  selectFileUploadProgress,
  selectFileUploadIsUninitialized,
  selectFileUploadError,
} from "store/slice/FileUploader/FileUploaderSelectors"
import { FILE_TYPE } from "store/slice/InputNode/InputNodeType"
import { selectCurrentWorkspaceId } from "store/slice/Workspace/WorkspaceSelector"
import { AppDispatch } from "store/store"
import {
  acquireWorkspaceLock,
  releaseWorkspaceLock,
  refreshLock,
} from "utils/operationLock"

type UseFileUploaderProps = {
  fileType?: FILE_TYPE
  nodeId?: string
}

const LOCK_REFRESH_INTERVAL_MS = 60000

export function useFileUploader({ fileType, nodeId }: UseFileUploaderProps) {
  const dispatch = useDispatch<AppDispatch>()
  const id = useRef(nanoid())
  const workspaceId = useSelector(selectCurrentWorkspaceId)
  const lockRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const hasAcquiredLockRef = useRef(false)

  const onUploadFile = useCallback(
    (formData: FormData, fileName: string) => {
      if (workspaceId) {
        acquireWorkspaceLock(String(workspaceId), "upload")
        hasAcquiredLockRef.current = true

        lockRefreshRef.current = setInterval(() => {
          refreshLock(String(workspaceId))
        }, LOCK_REFRESH_INTERVAL_MS)

        dispatch(
          uploadFile({
            workspaceId,
            requestId: id.current,
            nodeId,
            fileName,
            formData,
            fileType,
          }),
        )
      } else {
        throw new Error("workspaceId is undefined")
      }
    },
    [dispatch, workspaceId, fileType, nodeId],
  )

  const uninitialized = useSelector(selectFileUploadIsUninitialized(id.current))
  const filePath = useSelector(selectUploadFilePath(id.current))
  const pending = useSelector(selectFileUploadIsPending(id.current))
  const fulfilled = useSelector(selectFileUploadIsFulfilled(id.current))
  const progress = useSelector(selectFileUploadProgress(id.current))
  const error = useSelector(selectFileUploadError(id.current))

  useEffect(() => {
    if ((fulfilled || error) && workspaceId && hasAcquiredLockRef.current) {
      if (lockRefreshRef.current) {
        clearInterval(lockRefreshRef.current)
        lockRefreshRef.current = null
      }
      releaseWorkspaceLock(String(workspaceId))
      hasAcquiredLockRef.current = false
    }
  }, [fulfilled, error, workspaceId])

  useEffect(() => {
    return () => {
      if (lockRefreshRef.current) {
        clearInterval(lockRefreshRef.current)
      }
      if (workspaceId && hasAcquiredLockRef.current) {
        releaseWorkspaceLock(String(workspaceId))
        hasAcquiredLockRef.current = false
      }
    }
  }, [workspaceId])

  return {
    filePath,
    uninitialized,
    pending,
    fulfilled,
    progress,
    error,
    onUploadFile,
    id,
  }
}

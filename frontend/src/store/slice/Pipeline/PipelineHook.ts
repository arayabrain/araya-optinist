import { useCallback, useEffect, useState, useRef } from "react"
import { useDispatch, useSelector } from "react-redux"
import { useLocation, useNavigate, useParams } from "react-router-dom"

import { useSnackbar, VariantType } from "notistack"

import { isRejected } from "@reduxjs/toolkit"

import { STANDALONE_WORKSPACE_ID } from "const/Mode"
import { getAlgoList } from "store/slice/AlgorithmList/AlgorithmListActions"
import { selectAlgorithmNodeNotExist } from "store/slice/AlgorithmNode/AlgorithmNodeSelectors"
import { getExperiments } from "store/slice/Experiments/ExperimentsActions"
import { clearExperiments } from "store/slice/Experiments/ExperimentsSlice"
import { selectFilePathIsUndefined } from "store/slice/InputNode/InputNodeSelectors"
import {
  run,
  pollRunResult,
  runByCurrentUid,
  cancelResult,
  batchRun,
} from "store/slice/Pipeline/PipelineActions"
import {
  selectPipelineIsCanceled,
  selectPipelineIsStartedSuccess,
  selectPipelineLatestUid,
  selectPipelineStatus,
  selectPipelineIsBatchRun,
} from "store/slice/Pipeline/PipelineSelectors"
import { RUN_STATUS } from "store/slice/Pipeline/PipelineType"
import { handleWorkflowYamlError } from "store/slice/Pipeline/PipelineUtils"
import { selectRunPostData } from "store/slice/Run/RunSelectors"
import { selectModeStandalone } from "store/slice/Standalone/StandaloneSeclector"
import {
  fetchWorkflow,
  reproduceWorkflow,
} from "store/slice/Workflow/WorkflowActions"
import { getWorkspace } from "store/slice/Workspace/WorkspaceActions"
import {
  selectIsWorkspaceOwner,
  selectCurrentWorkspaceId,
} from "store/slice/Workspace/WorkspaceSelector"
import {
  clearCurrentWorkspace,
  setActiveTab,
  setCurrentWorkspace,
} from "store/slice/Workspace/WorkspaceSlice"
import { AppDispatch } from "store/store"
import {
  acquireWorkspaceLock,
  releaseWorkspaceLock,
  refreshLock,
} from "utils/operationLock"

const POLLING_INTERVAL = 10000
const LOCK_REFRESH_INTERVAL_MS = 60000

export type UseRunPipelineReturnType = ReturnType<typeof useRunPipeline>

export function useRunPipeline() {
  const dispatch = useDispatch<AppDispatch>()
  const appDispatch: AppDispatch = useDispatch()
  const isStandalone = useSelector(selectModeStandalone)
  const navigate = useNavigate()
  const location = useLocation()
  const lockRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const hasAcquiredLockRef = useRef(false)

  const { workspaceId } = useParams<{ workspaceId: string }>()
  const _workspaceId = Number(workspaceId)

  useEffect(() => {
    if (isStandalone) {
      dispatch(setCurrentWorkspace(STANDALONE_WORKSPACE_ID))
      dispatch(fetchWorkflow(STANDALONE_WORKSPACE_ID))
    } else {
      appDispatch(getWorkspace({ id: _workspaceId }))
        .unwrap()
        .then((_) => {
          dispatch(fetchWorkflow(_workspaceId))
          const selectedTab = location.state?.tab
          selectedTab && dispatch(setActiveTab(selectedTab))
        })
        .catch((_) => {
          navigate("/workspaces")
        })
    }
    return () => {
      dispatch(clearExperiments())
      dispatch(clearCurrentWorkspace())
      if (lockRefreshRef.current) {
        clearInterval(lockRefreshRef.current)
        lockRefreshRef.current = null
      }
      if (workspaceId && hasAcquiredLockRef.current) {
        releaseWorkspaceLock(workspaceId)
        hasAcquiredLockRef.current = false
      }
    }
  }, [
    dispatch,
    appDispatch,
    navigate,
    _workspaceId,
    location.state,
    isStandalone,
    workspaceId,
  ])

  const uid = useSelector(selectPipelineLatestUid)
  const isCanceled = useSelector(selectPipelineIsCanceled)
  const isStartedSuccess = useSelector(selectPipelineIsStartedSuccess)
  const runDisabled = useIsRunDisabled()
  const isBatchRun = useSelector(selectPipelineIsBatchRun)
  const currentWorkspaceId = useSelector(selectCurrentWorkspaceId)

  const filePathIsUndefined = useSelector(selectFilePathIsUndefined)
  const algorithmNodeNotExist = useSelector(selectAlgorithmNodeNotExist)
  const runPostData = useSelector(selectRunPostData)
  const { enqueueSnackbar } = useSnackbar()

  const prepareRunPostData = useCallback(
    (name: string) => {
      if (workspaceId) {
        if (!acquireWorkspaceLock(workspaceId, "run")) {
          return
        }
        hasAcquiredLockRef.current = true

        if (lockRefreshRef.current) {
          clearInterval(lockRefreshRef.current)
        }
        lockRefreshRef.current = setInterval(() => {
          refreshLock(workspaceId)
        }, LOCK_REFRESH_INTERVAL_MS)
      }

      const newNodeDict = { ...runPostData.nodeDict }
      Object.keys(newNodeDict).forEach((key) => {
        delete newNodeDict[key].data.dataFilterParam
        delete newNodeDict[key].data.draftDataFilterParam
      })
      return {
        name,
        ...runPostData,
        nodeDict: newNodeDict,
        forceRunList: [],
      }
    },
    [runPostData, workspaceId],
  )

  const handleRunPipeline = useCallback(
    (name: string) => {
      const runPostData = prepareRunPostData(name)
      if (!runPostData) return
      dispatch(
        run({
          runPostData,
        }),
      )
        .unwrap()
        .catch((error) => {
          if (workspaceId && hasAcquiredLockRef.current) {
            if (lockRefreshRef.current) {
              clearInterval(lockRefreshRef.current)
              lockRefreshRef.current = null
            }
            releaseWorkspaceLock(workspaceId)
            hasAcquiredLockRef.current = false
          }
          handleWorkflowYamlError(error, enqueueSnackbar)
        })
    },
    [dispatch, enqueueSnackbar, prepareRunPostData, workspaceId],
  )

  const handleBatchRunPipeline = useCallback(
    (name: string) => {
      const runPostData = prepareRunPostData(name)
      if (!runPostData) return
      dispatch(
        batchRun({
          runPostData,
        }),
      )
        .unwrap()
        .catch((error) => {
          handleWorkflowYamlError(error, enqueueSnackbar)
        })
    },
    [dispatch, enqueueSnackbar, prepareRunPostData],
  )

  const handleRunPipelineByUid = useCallback(() => {
    if (workspaceId) {
      if (!acquireWorkspaceLock(workspaceId, "run")) {
        return
      }
      hasAcquiredLockRef.current = true

      if (lockRefreshRef.current) {
        clearInterval(lockRefreshRef.current)
      }
      lockRefreshRef.current = setInterval(() => {
        refreshLock(workspaceId)
      }, LOCK_REFRESH_INTERVAL_MS)
    }

    dispatch(runByCurrentUid({ runPostData }))
      .unwrap()
      .catch((error) => {
        if (workspaceId && hasAcquiredLockRef.current) {
          if (lockRefreshRef.current) {
            clearInterval(lockRefreshRef.current)
            lockRefreshRef.current = null
          }
          releaseWorkspaceLock(workspaceId)
          hasAcquiredLockRef.current = false
        }
        handleWorkflowYamlError(error, enqueueSnackbar)
      })
  }, [dispatch, enqueueSnackbar, runPostData, workspaceId])

  const handleClickVariant = (variant: VariantType, mess: string) => {
    enqueueSnackbar(mess, { variant })
  }

  const handleCancelPipeline = useCallback(async () => {
    if (uid != null) {
      const data = await dispatch(cancelResult({ uid }))
      if (isRejected(data)) {
        handleClickVariant(
          "error",
          "Failed to cancel workflow. Please try again.",
        )
      }
    }
    //eslint-disable-next-line
  }, [dispatch, uid])

  useEffect(() => {
    const intervalId = setInterval(() => {
      if (isStartedSuccess && !isCanceled && uid != null) {
        dispatch(pollRunResult({ uid: uid }))
      }
    }, POLLING_INTERVAL)
    return () => {
      clearInterval(intervalId)
    }
  }, [dispatch, uid, isCanceled, isStartedSuccess])

  const status = useSelector(selectPipelineStatus)
  // タブ移動による再レンダリングするたびにスナックバーが実行されてしまう挙動を回避するために前回の値を保持
  const [prevStatus, setPrevStatus] = useState(status)

  // Handle batch run completion separately
  const handleBatchRunCompletion = useCallback(() => {
    if (uid && currentWorkspaceId) {
      dispatch(reproduceWorkflow({ workspaceId: currentWorkspaceId, uid }))
    }
  }, [uid, currentWorkspaceId, dispatch])

  useEffect(() => {
    if (prevStatus !== status) {
      let isRunFinished = false

      if (status === RUN_STATUS.START_SUCCESS) {
        dispatch(getExperiments())
      } else if (status === RUN_STATUS.FINISHED) {
        // Show different message based on batch run flag
        const message = isBatchRun ? "Batch run started" : "Workflow finished"
        enqueueSnackbar(message, { variant: "success" })

        // Update flowchart for batch run
        if (isBatchRun) {
          handleBatchRunCompletion()
        }

        isRunFinished = true
        dispatch(getExperiments())
      } else if (status === RUN_STATUS.ABORTED) {
        enqueueSnackbar("Workflow aborted", { variant: "error" })
        isRunFinished = true
        dispatch(getExperiments())
      } else if (status === RUN_STATUS.CANCELED) {
        enqueueSnackbar("Workflow canceled", { variant: "success" })
        isRunFinished = true
        dispatch(getExperiments())
      }

      if (isRunFinished) {
        dispatch(getAlgoList())

        if (workspaceId && hasAcquiredLockRef.current) {
          if (lockRefreshRef.current) {
            clearInterval(lockRefreshRef.current)
            lockRefreshRef.current = null
          }
          releaseWorkspaceLock(workspaceId)
          hasAcquiredLockRef.current = false
        }
      }

      setPrevStatus(status)
    }
  }, [
    dispatch,
    status,
    prevStatus,
    enqueueSnackbar,
    workspaceId,
    isBatchRun,
    handleBatchRunCompletion,
  ])

  return {
    filePathIsUndefined,
    algorithmNodeNotExist,
    uid,
    status,
    runDisabled,
    handleRunPipeline,
    handleBatchRunPipeline,
    handleRunPipelineByUid,
    handleCancelPipeline,
  }
}

export function useIsRunDisabled() {
  const isStartedSuccess = useSelector(selectPipelineIsStartedSuccess)
  const isOwner = useSelector(selectIsWorkspaceOwner)
  const runDisabled = isOwner ? isStartedSuccess : true

  return runDisabled
}

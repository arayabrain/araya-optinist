import {
  ChangeEvent,
  memo,
  MouseEvent,
  useState,
  useRef,
  useCallback,
} from "react"
import { useSelector, useDispatch } from "react-redux"

import { useSnackbar } from "notistack"

import {
  PlayArrow,
  FastForward,
  Warning as WarningIcon,
} from "@mui/icons-material"
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown"
import BlockIcon from "@mui/icons-material/Block"
import ReplayIcon from "@mui/icons-material/Replay"
import { IconButton, Tooltip, DialogContentText } from "@mui/material"
import Button from "@mui/material/Button"
import ButtonGroup from "@mui/material/ButtonGroup"
import ClickAwayListener from "@mui/material/ClickAwayListener"
import Dialog from "@mui/material/Dialog"
import DialogActions from "@mui/material/DialogActions"
import DialogContent from "@mui/material/DialogContent"
import DialogTitle from "@mui/material/DialogTitle"
import Grow from "@mui/material/Grow"
import MenuItem from "@mui/material/MenuItem"
import MenuList from "@mui/material/MenuList"
import Paper from "@mui/material/Paper"
import Popper from "@mui/material/Popper"
import TextField from "@mui/material/TextField"

import { getMyStorageAlertApi } from "api/storage/StorageAlerts"
import { WORKSPACE_TYPE } from "const/Workspace"
import { selectFlowNodes } from "store/slice/FlowElement/FlowElementSelectors"
import { selectInputNode } from "store/slice/InputNode/InputNodeSelectors"
import { isBatchAnyInputNode } from "store/slice/InputNode/InputNodeUtils"
import { UseRunPipelineReturnType } from "store/slice/Pipeline/PipelineHook"
import {
  selectPipelineIsStartedSuccess,
  selectPipelineRunBtn,
} from "store/slice/Pipeline/PipelineSelectors"
import { setRunBtnOption } from "store/slice/Pipeline/PipelineSlice"
import {
  RUN_BTN_LABELS,
  RUN_BTN_OPTIONS,
  RUN_BTN_TYPE,
} from "store/slice/Pipeline/PipelineType"
import { selectCurrentWorkspaceType } from "store/slice/Workspace/WorkspaceSelector"

// Storage check result values
export enum StorageCheckResult {
  PROCEED = "proceed",
  BLOCKED = "blocked",
  CONFIRM_NEEDED = "confirm_needed",
}

const RUN_REQUEST_DEBOUNCE_MS = 3000

export const RunButtons = memo(function RunButtons(
  props: UseRunPipelineReturnType,
) {
  const {
    uid,
    runDisabled,
    filePathIsUndefined,
    algorithmNodeNotExist,
    handleCancelPipeline,
    handleRunPipeline,
    handleBatchRunPipeline,
    handleRunPipelineByUid,
  } = props

  const dispatch = useDispatch()

  const runBtnOption = useSelector(selectPipelineRunBtn)
  const isStartedSuccess = useSelector(selectPipelineIsStartedSuccess)
  const workspaceType = useSelector(selectCurrentWorkspaceType)
  const flowNodes = useSelector(selectFlowNodes)
  const inputNodes = useSelector(selectInputNode)

  const sendingRunRequest = useRef(false)

  const [dialogOpen, setDialogOpen] = useState(false)
  const [batchDialogOpen, setBatchDialogOpen] = useState(false)
  const [storageChecking, setStorageChecking] = useState(false)
  const [storageCheckFailedDialogOpen, setStorageCheckFailedDialogOpen] =
    useState(false)
  const pendingRunActionRef = useRef<(() => void) | null>(null)
  const { enqueueSnackbar } = useSnackbar()

  const checkStorageBeforeRun =
    useCallback(async (): Promise<StorageCheckResult> => {
      try {
        setStorageChecking(true)
        const storageResponse = await getMyStorageAlertApi()

        if (storageResponse.has_alert && storageResponse.alert) {
          const alert = storageResponse.alert
          switch (alert.alert_level) {
            case "danger":
              enqueueSnackbar(
                "Cannot run job: Storage quota exceeded " +
                  `(${alert.storage_usage_percent.toFixed(1)}% used). ` +
                  "Please free up space before running jobs.",
                { variant: "error", autoHideDuration: 10000 },
              )
              return StorageCheckResult.BLOCKED
            case "critical":
              enqueueSnackbar(
                "Warning: Storage usage is high " +
                  `(${alert.storage_usage_percent.toFixed(1)}% used). ` +
                  "Consider freeing up space.",
                { variant: "warning", autoHideDuration: 8000 },
              )
              return StorageCheckResult.PROCEED
            default:
              return StorageCheckResult.PROCEED
          }
        }
        return StorageCheckResult.PROCEED
      } catch (error) {
        // eslint-disable-next-line no-console
        console.error("Failed to check storage:", error)
        return StorageCheckResult.CONFIRM_NEEDED
      } finally {
        setStorageChecking(false)
      }
    }, [enqueueSnackbar])

  const handleStorageCheckFailedProceed = useCallback(() => {
    setStorageCheckFailedDialogOpen(false)
    if (pendingRunActionRef.current) {
      pendingRunActionRef.current()
      pendingRunActionRef.current = null
    }
  }, [])

  const handleStorageCheckFailedCancel = useCallback(() => {
    setStorageCheckFailedDialogOpen(false)
    pendingRunActionRef.current = null
  }, [])

  const executeRunByUid = useCallback(() => {
    if (sendingRunRequest.current) return
    sendingRunRequest.current = true
    handleRunPipelineByUid()
    setTimeout(() => {
      sendingRunRequest.current = false
    }, RUN_REQUEST_DEBOUNCE_MS)
  }, [handleRunPipelineByUid])

  const handleClick = async () => {
    let errorMessage: string | null = null
    if (algorithmNodeNotExist) {
      errorMessage = "please add some algorithm nodes to the flowchart"
    }
    if (filePathIsUndefined) {
      errorMessage = "please select input file"
    }
    if (errorMessage != null) {
      enqueueSnackbar(errorMessage, {
        variant: "error",
      })
      return
    }

    const checkResult = await checkStorageBeforeRun()

    if (checkResult === StorageCheckResult.BLOCKED) {
      return
    }

    if (checkResult === StorageCheckResult.CONFIRM_NEEDED) {
      if (runBtnOption === RUN_BTN_OPTIONS.RUN_NEW) {
        pendingRunActionRef.current = () => setDialogOpen(true)
      } else {
        pendingRunActionRef.current = executeRunByUid
      }
      setStorageCheckFailedDialogOpen(true)
      return
    }

    if (runBtnOption === RUN_BTN_OPTIONS.RUN_NEW) {
      setDialogOpen(true)
    } else {
      executeRunByUid()
    }
  }

  const onClickDialogRun = async (name: string) => {
    if (sendingRunRequest.current) return

    const checkResult = await checkStorageBeforeRun()

    if (checkResult === StorageCheckResult.BLOCKED) {
      setDialogOpen(false)
      return
    }

    if (checkResult === StorageCheckResult.CONFIRM_NEEDED) {
      pendingRunActionRef.current = () => {
        sendingRunRequest.current = true
        handleRunPipeline(name)
        setTimeout(() => {
          sendingRunRequest.current = false
        }, RUN_REQUEST_DEBOUNCE_MS)
        setDialogOpen(false)
      }
      setStorageCheckFailedDialogOpen(true)
      return
    }

    sendingRunRequest.current = true
    handleRunPipeline(name)
    setTimeout(() => {
      sendingRunRequest.current = false
    }, RUN_REQUEST_DEBOUNCE_MS)
    setDialogOpen(false)
  }
  const onClickCancel = () => {
    handleCancelPipeline()
  }
  /**
   * Validates batch input nodes in the flowchart
   * @returns Error message if validation fails, null otherwise
   */
  const validateBatchInputNodes = (): string | null => {
    // Find all batch input nodes in the flowchart
    const batchInputNodes = flowNodes
      .filter((node) => {
        const inputNode = inputNodes[node.id]
        return inputNode && isBatchAnyInputNode(inputNode)
      })
      .map((node) => ({
        nodeId: node.id,
        inputNode: inputNodes[node.id],
      }))

    // Check if there are any batch nodes
    if (batchInputNodes.length === 0) {
      return "There are no batch input nodes."
    }

    // Check file count consistency across all batch nodes
    const fileCounts = batchInputNodes.map((node) => {
      const filePath = node.inputNode.selectedFilePath
      if (Array.isArray(filePath)) {
        return filePath.length
      }
      return filePath ? 1 : 0
    })

    const minCount = Math.min(...fileCounts)
    const maxCount = Math.max(...fileCounts)

    if (minCount !== maxCount) {
      return `Number of batch input files does not match. [${minCount} - ${maxCount}]`
    }

    return null
  }

  const onClickBatchRun = () => {
    let errorMessage: string | null = null
    if (algorithmNodeNotExist) {
      errorMessage = "please add some algorithm nodes to the flowchart"
    }
    if (filePathIsUndefined) {
      errorMessage = "please select input file"
    }

    // Validate batch nodes
    if (errorMessage == null) {
      errorMessage = validateBatchInputNodes()
    }

    if (errorMessage != null) {
      enqueueSnackbar(errorMessage, {
        variant: "error",
      })
    } else {
      setBatchDialogOpen(true)
    }
  }
  const onClickDialogBatchRun = (name: string) => {
    if (sendingRunRequest.current) return
    sendingRunRequest.current = true
    handleBatchRunPipeline(name)
    setTimeout(() => {
      sendingRunRequest.current = false
    }, 3000)
    setBatchDialogOpen(false)
  }
  const [menuOpen, setMenuOpen] = useState(false)
  const anchorRef = useRef<HTMLDivElement>(null)

  const handleMenuItemClick = (
    event: MouseEvent<HTMLLIElement>,
    option: RUN_BTN_TYPE,
  ) => {
    dispatch(setRunBtnOption({ runBtnOption: option }))
    setMenuOpen(false)
  }
  const handleToggle = () => {
    setMenuOpen((prevOpen) => !prevOpen)
  }
  const handleClose = (event: Event) => {
    if (
      anchorRef.current &&
      anchorRef.current.contains(event.target as HTMLElement)
    ) {
      return
    }
    setMenuOpen(false)
  }
  const uidExists = uid != null
  const isBatchWorkspace = workspaceType === WORKSPACE_TYPE.BATCH

  return (
    <>
      {/* Show Run All/Run buttons only for non-batch workspaces */}
      {!isBatchWorkspace && (
        <>
          <ButtonGroup
            sx={{
              margin: 1,
            }}
            variant="contained"
            ref={anchorRef}
            disabled={runDisabled || storageChecking}
          >
            <Button
              onClick={handleClick}
              startIcon={
                storageChecking ? undefined : runBtnOption ===
                  RUN_BTN_OPTIONS.RUN_ALREADY ? (
                  <ReplayIcon />
                ) : (
                  <PlayArrow />
                )
              }
            >
              {storageChecking
                ? "Checking storage..."
                : RUN_BTN_LABELS[runBtnOption]}
            </Button>
            <Button size="small" onClick={handleToggle}>
              <ArrowDropDownIcon />
            </Button>
          </ButtonGroup>
          <Popper
            open={menuOpen}
            anchorEl={anchorRef.current}
            role={undefined}
            transition
            disablePortal
          >
            {({ TransitionProps, placement }) => (
              <Grow
                {...TransitionProps}
                style={{
                  transformOrigin:
                    placement === "bottom" ? "center top" : "center bottom",
                }}
              >
                <Paper>
                  <ClickAwayListener onClickAway={handleClose}>
                    <MenuList>
                      {Object.values(RUN_BTN_OPTIONS).map((option) => (
                        <MenuItem
                          key={option}
                          disabled={
                            !uidExists && option === RUN_BTN_OPTIONS.RUN_ALREADY
                          }
                          selected={option === runBtnOption}
                          onClick={(event) =>
                            handleMenuItemClick(event, option)
                          }
                        >
                          {RUN_BTN_LABELS[option]}
                        </MenuItem>
                      ))}
                    </MenuList>
                  </ClickAwayListener>
                </Paper>
              </Grow>
            )}
          </Popper>
        </>
      )}

      {/* Show Cancel button for all workspace types when workflow is running */}
      {isStartedSuccess && (
        <Tooltip title="Cancel Workflow">
          <IconButton onClick={onClickCancel}>
            <BlockIcon color="error" />
          </IconButton>
        </Tooltip>
      )}

      {/* Show Batch Run button only for batch workspaces */}
      {isBatchWorkspace && (
        <Button
          variant="contained"
          sx={{ margin: 1 }}
          onClick={onClickBatchRun}
          disabled={runDisabled}
          startIcon={<FastForward />}
        >
          Batch Run
        </Button>
      )}
      <RunDialog
        open={dialogOpen}
        handleRun={onClickDialogRun}
        handleClose={() => setDialogOpen(false)}
      />
      <BatchRunDialog
        open={batchDialogOpen}
        handleRun={onClickDialogBatchRun}
        handleClose={() => setBatchDialogOpen(false)}
      />
      {/* Case 39 fix: Confirmation dialog when storage check fails */}
      <StorageCheckFailedDialog
        open={storageCheckFailedDialogOpen}
        onProceed={handleStorageCheckFailedProceed}
        onCancel={handleStorageCheckFailedCancel}
      />
    </>
  )
})

interface RunDialogProps {
  open: boolean
  handleRun: (name: string) => void
  handleClose: () => void
  title?: string
  defaultName?: string
}

const RunDialog = memo(function RunDialog({
  open,
  handleClose,
  handleRun,
  title = "Name and run workflow",
  defaultName = "New flow",
}: RunDialogProps) {
  const [name, setName] = useState(defaultName)
  const [error, setError] = useState<string | null>(null)
  const onClickRun = () => {
    if (name !== "") {
      handleRun(name)
    } else {
      setError("name is empty")
    }
  }
  const onChangeName = (event: ChangeEvent<HTMLInputElement>) => {
    setName(event.target.value)
    if (event.target.value !== "") {
      setError(null)
    }
  }
  return (
    <Dialog open={open} onClose={handleClose}>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <TextField
          label="name"
          autoFocus
          margin="dense"
          fullWidth
          variant="standard"
          onChange={onChangeName}
          error={error != null}
          helperText={error}
          value={name}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} variant="outlined">
          Cancel
        </Button>
        <Button onClick={onClickRun} variant="contained">
          Run
        </Button>
      </DialogActions>
    </Dialog>
  )
})

interface BatchRunDialogProps {
  open: boolean
  handleRun: (name: string) => void
  handleClose: () => void
}

const BatchRunDialog = memo(function BatchRunDialog({
  open,
  handleClose,
  handleRun,
}: BatchRunDialogProps) {
  return (
    <RunDialog
      open={open}
      handleRun={handleRun}
      handleClose={handleClose}
      title="Name and run batch workflow"
      defaultName="New batch flow"
    />
  )
})

interface StorageCheckFailedDialogProps {
  open: boolean
  onProceed: () => void
  onCancel: () => void
}

const StorageCheckFailedDialog = memo(function StorageCheckFailedDialog({
  open,
  onProceed,
  onCancel,
}: StorageCheckFailedDialogProps) {
  return (
    <Dialog open={open} onClose={onCancel} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <WarningIcon color="warning" />
        Storage Check Failed
      </DialogTitle>
      <DialogContent>
        <DialogContentText>
          Unable to verify your storage quota. The workflow may fail if you have
          exceeded your storage limit. Do you want to proceed anyway?
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} variant="outlined">
          Cancel
        </Button>
        <Button onClick={onProceed} variant="contained" color="warning">
          Proceed Anyway
        </Button>
      </DialogActions>
    </Dialog>
  )
})

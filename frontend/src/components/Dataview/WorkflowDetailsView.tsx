import { useEffect, useState } from "react"
import { useDispatch, useSelector } from "react-redux"

import AssignmentOutlinedIcon from "@mui/icons-material/AssignmentOutlined"
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline"
import HourglassEmptyIcon from "@mui/icons-material/HourglassEmpty"
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material"

import Loading from "components/common/Loading"
import { publicDataviewReproduceWorkflow } from "store/slice/Dataview/DataviewActions"
import { DataviewType } from "store/slice/Dataview/DataviewType"
import { selectFlowNodes } from "store/slice/FlowElement/FlowElementSelectors"
import { clearFlowElements } from "store/slice/FlowElement/FlowElementSlice"
import { NODE_TYPE_SET } from "store/slice/FlowElement/FlowElementType"
import { clearCurrentPipeline } from "store/slice/Pipeline/PipelineSlice"
import { reproduceWorkflow } from "store/slice/Workflow/WorkflowActions"
import { AppDispatch } from "store/store"
import { formatParamsForDisplay } from "utils/param/ParamUtils"

interface WorkflowDetailsViewProps {
  dataviewRecord: DataviewType | null
  open: boolean
  onClose: () => void
  is_public: boolean
}

interface NodeDetails {
  nodeId: string
  functionName: string
  params: Record<string, unknown> | undefined
}

// Extended type for algorithm nodes with params
interface AlgorithmNodeDataWithParam {
  label?: string
  type?: string
  param?: Record<string, unknown>
}

export const WorkflowDetailsView = ({
  dataviewRecord,
  open,
  onClose,
  is_public,
}: WorkflowDetailsViewProps) => {
  const dispatch = useDispatch<AppDispatch>()
  const flowNodes = useSelector(selectFlowNodes)
  const [loading, setLoading] = useState(false)
  const [syncStatus, setSyncStatus] = useState<{
    pending: boolean
    error: boolean
    message: string
  }>({ pending: false, error: false, message: "" })
  const [retryCount, setRetryCount] = useState(0)
  const [paramsDialog, setParamsDialog] = useState<{
    open: boolean
    params: Record<string, unknown> | null
    nodeId: string
    functionName: string
  }>({
    open: false,
    params: null,
    nodeId: "",
    functionName: "",
  })

  useEffect(() => {
    if (open && dataviewRecord) {
      setLoading(true)
      setSyncStatus({ pending: false, error: false, message: "" })
      const api = is_public
        ? publicDataviewReproduceWorkflow
        : reproduceWorkflow
      dispatch(
        api({
          workspaceId: dataviewRecord.workspace.id,
          uid: dataviewRecord.uid,
        }),
      )
        .unwrap()
        .then(() => {
          setLoading(false)
        })
        .catch((error) => {
          setLoading(false)
          // Handle 202 (pending sync) and 503 (sync error) for public dataview
          if (is_public && error?.response) {
            const status = error.response.status
            const data = error.response.data

            if (status === 202) {
              // Experiment is published but not yet synced
              setSyncStatus({
                pending: true,
                error: false,
                message:
                  data?.message ||
                  "Publishing in progress, check back in a few minutes.",
              })
              // Auto-retry after 30 seconds (max 10 retries = 5 minutes)
              if (retryCount < 10) {
                setTimeout(() => {
                  setRetryCount(retryCount + 1)
                }, 30000)
              }
            } else if (status === 503) {
              // Sync failed or download error
              setSyncStatus({
                pending: false,
                error: true,
                message:
                  data?.message ||
                  "Experiment temporarily unavailable, please try again later.",
              })
            }
          }
        })
    }
  }, [open, dataviewRecord, is_public, dispatch, retryCount])

  // Extract algorithm nodes from flowNodes
  const nodeDetails: NodeDetails[] = flowNodes
    .filter((node) => node.data?.type === NODE_TYPE_SET.ALGORITHM)
    .map((node) => ({
      nodeId: node.id,
      functionName: node.data?.label || "Unknown",
      params: (node.data as AlgorithmNodeDataWithParam)?.param,
    }))

  const handleClose = () => {
    // Clean up the flow elements when closing
    dispatch(clearFlowElements())
    dispatch(clearCurrentPipeline())
    setParamsDialog({ open: false, params: null, nodeId: "", functionName: "" })
    onClose()
  }

  const handleOpenParamsDialog = (
    params: Record<string, unknown>,
    nodeId: string,
    functionName: string,
  ) => {
    // Format params before displaying
    const formattedParams = formatParamsForDisplay(params)
    setParamsDialog({
      open: true,
      params: formattedParams,
      nodeId,
      functionName,
    })
  }

  const handleCloseParamsDialog = () => {
    setParamsDialog({ ...paramsDialog, open: false })
  }

  // Node Params Dialog Component
  const NodeParamsDialog = (
    <Dialog
      open={paramsDialog.open}
      onClose={handleCloseParamsDialog}
      maxWidth="sm"
      fullWidth
      sx={{
        "& .MuiDialog-paper": {
          width: "90%",
          maxWidth: "600px",
        },
      }}
    >
      <DialogTitle>
        <Box display="flex" alignItems="center" gap={1}>
          <Typography variant="h6">Node Params</Typography>
          <Chip
            label={`Node: ${paramsDialog.nodeId}`}
            color="primary"
            variant="outlined"
            size="small"
          />
        </Box>
      </DialogTitle>
      <DialogContent dividers>
        <TextField
          multiline
          fullWidth
          value={JSON.stringify(paramsDialog.params, null, 2)}
          InputProps={{
            readOnly: true,
          }}
          sx={{
            "& .MuiInputBase-input": {
              fontFamily: "monospace",
              fontSize: "0.875rem",
              backgroundColor: "#f5f5f5",
              padding: 2,
            },
          }}
          minRows={10}
          maxRows={20}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={handleCloseParamsDialog} variant="outlined">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  )

  if (!dataviewRecord) return null

  return (
    <>
      <Dialog
        open={open}
        onClose={handleClose}
        aria-labelledby="dataview-details-dialog"
        maxWidth="md"
        fullWidth
      >
        <DialogTitle id="dataview-details-dialog">
          <Box display="flex" alignItems="center" gap={1}>
            <Typography variant="h6">Workflow Details</Typography>
            <Chip
              label={`ID: ${dataviewRecord.uid}`}
              color="primary"
              variant="outlined"
              size="medium"
              sx={{ fontSize: "0.9rem" }}
            />
          </Box>
        </DialogTitle>
        <DialogContent dividers>
          {syncStatus.pending ? (
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                py: 4,
                gap: 2,
              }}
            >
              <HourglassEmptyIcon
                sx={{ fontSize: 48, color: "warning.main" }}
              />
              <Alert severity="info" sx={{ width: "100%" }}>
                <Typography variant="body1" gutterBottom>
                  {syncStatus.message}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Experiments are typically available within 5 minutes. This
                  page will auto-retry.
                </Typography>
              </Alert>
            </Box>
          ) : syncStatus.error ? (
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                py: 4,
                gap: 2,
              }}
            >
              <ErrorOutlineIcon sx={{ fontSize: 48, color: "error.main" }} />
              <Alert severity="error" sx={{ width: "100%" }}>
                <Typography variant="body1">{syncStatus.message}</Typography>
              </Alert>
              <Button
                variant="outlined"
                onClick={() => {
                  setRetryCount(0)
                  setSyncStatus({ pending: false, error: false, message: "" })
                }}
              >
                Retry
              </Button>
            </Box>
          ) : loading || flowNodes.length === 0 ? (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <Loading loading={loading} />
            </Box>
          ) : nodeDetails.length > 0 ? (
            <Table size="medium" aria-label="nodes details">
              <TableHead
                sx={{
                  "& .MuiTableCell-head": {
                    fontWeight: "bold",
                    fontSize: "1rem",
                  },
                }}
              >
                <TableRow>
                  <TableCell>Function</TableCell>
                  <TableCell>Node ID</TableCell>
                  <TableCell>Params</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {nodeDetails.map((detail) => (
                  <TableRow key={detail.nodeId}>
                    <TableCell>{detail.functionName}</TableCell>
                    <TableCell>{detail.nodeId}</TableCell>
                    <TableCell>
                      {detail.params ? (
                        <IconButton
                          color="primary"
                          onClick={() =>
                            handleOpenParamsDialog(
                              detail.params!,
                              detail.nodeId,
                              detail.functionName,
                            )
                          }
                          size="small"
                        >
                          <AssignmentOutlinedIcon />
                        </IconButton>
                      ) : (
                        <Typography variant="body2" color="text.secondary">
                          No params
                        </Typography>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Typography variant="body2" color="text.secondary">
              No Workflow details available
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose} variant="outlined">
            Close
          </Button>
        </DialogActions>
      </Dialog>

      {NodeParamsDialog}
    </>
  )
}

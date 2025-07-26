import { ReactElement, memo, useEffect } from "react"
import { useSelector, useDispatch } from "react-redux"

import {
  Box,
  Grid,
  Paper,
  Typography,
  Divider as MuiDivider,
} from "@mui/material"

import BaseNodesView from "components/Dataview/BaseNodesView"
import { DisplayDataItem } from "components/Workspace/Visualize/DisplayDataItem"
import { getExperiments } from "store/slice/Experiments/ExperimentsActions"
import {
  selectExperimentsStatusIsFulfilled,
  selectExperimentsStatusIsUninitialized,
} from "store/slice/Experiments/ExperimentsSelectors"
import { selectNodeLabelById } from "store/slice/FlowElement/FlowElementSelectors"
import {
  selectPipelineNodeResultSuccessList,
  selectPipelineNodeResultOutputFilePath,
  selectPipelineNodeResultOutputFileDataType,
} from "store/slice/Pipeline/PipelineSelectors"
import { selectVisualizeItemIdForWorkflowDialog } from "store/slice/VisualizeItem/VisualizeItemSelectors"
import {
  addItemForWorkflowDialog,
  deleteAllItemForWorkflowDialog,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import { selectCurrentWorkspaceId } from "store/slice/Workspace/WorkspaceSelector"
import { RootState, AppDispatch } from "store/store"

type OutputsViewProps = {
  open: boolean
  workspaceId: number | undefined
  uid: string | undefined
  handleClose: () => void
}

const OutputsView = ({
  open,
  workspaceId,
  uid,
  handleClose,
}: OutputsViewProps) => {
  const dispatch = useDispatch<AppDispatch>()
  const currentWorkspaceId = useSelector(selectCurrentWorkspaceId)

  useEffect(() => {
    return () => {
      if (!open) {
        dispatch(deleteAllItemForWorkflowDialog())
      }
    }
  }, [dispatch, open])

  const experimentsIsUninitialized = useSelector(
    selectExperimentsStatusIsUninitialized,
  )
  const experimentsIsFulfilled = useSelector(selectExperimentsStatusIsFulfilled)

  // Initialize experiments if needed
  useEffect(() => {
    if (open && experimentsIsUninitialized && currentWorkspaceId) {
      dispatch(getExperiments())
    }
  }, [dispatch, open, experimentsIsUninitialized, currentWorkspaceId])

  const algorithmNodeOutputPathInfoList = useSelector((state: RootState) => {
    if (uid != null && experimentsIsFulfilled) {
      try {
        const runResult = selectPipelineNodeResultSuccessList(state)
        return runResult.map(({ nodeId, nodeResult }) => {
          return {
            nodeId,
            nodeName: selectNodeLabelById(nodeId)(state) || nodeId,
            paths: Object.entries(nodeResult.outputPaths).map(
              ([outputKey, value]) => {
                return {
                  outputKey,
                  filePath: value.path,
                  type: value.type,
                }
              },
            ),
          }
        })
      } catch (error) {
        // eslint-disable-next-line no-console
        console.warn("Error loading output data:", error)
        return []
      }
    } else {
      return []
    }
  })

  const renderAlgorithmNodeOutputList = (): ReactElement[] => {
    const visualizationSections: ReactElement[] = []

    // Render Algorithm Node Output Data
    algorithmNodeOutputPathInfoList.forEach((pathInfo) => {
      const nodeVisualizations: ReactElement[] = []

      pathInfo.paths.forEach((outputPath, _index) => {
        nodeVisualizations.push(
          <Grid
            item
            xs={12}
            md={6}
            lg={4}
            key={`${pathInfo.nodeId}-${outputPath.outputKey}`}
          >
            <Paper
              elevation={2}
              sx={{
                p: 2,
                height: 400,
                display: "flex",
                flexDirection: "column",
              }}
            >
              <Typography variant="h6" gutterBottom>
                {outputPath.outputKey}
              </Typography>
              <Typography variant="body2" color="textSecondary" gutterBottom>
                Type: {outputPath.type}
              </Typography>
              <Typography
                variant="body2"
                color="textSecondary"
                gutterBottom
                noWrap
              >
                {outputPath.filePath}
              </Typography>
              <Box sx={{ flexGrow: 1, minHeight: 280 }}>
                <DisplayOutputDataView
                  nodeId={pathInfo.nodeId}
                  outputKey={outputPath.outputKey}
                />
              </Box>
            </Paper>
          </Grid>,
        )
      })

      if (nodeVisualizations.length > 0) {
        visualizationSections.push(
          <Box key={`node-section-${pathInfo.nodeId}`} sx={{ mb: 4 }}>
            <Typography variant="h5" gutterBottom sx={{ mb: 2 }}>
              {pathInfo.nodeName}
            </Typography>
            <MuiDivider sx={{ mb: 3 }} />
            <Grid container spacing={2}>
              {nodeVisualizations}
            </Grid>
          </Box>,
        )
      }
    })

    return visualizationSections
  }

  if (!experimentsIsFulfilled) {
    return (
      <BaseNodesView
        open={open}
        workspaceId={workspaceId}
        uid={uid}
        handleClose={handleClose}
        title="Algorithm Node Outputs"
        data={[]}
        renderData={() => []}
        emptyMessage="Loading experiments data..."
      />
    )
  }

  if (algorithmNodeOutputPathInfoList.length === 0) {
    return (
      <BaseNodesView
        open={open}
        workspaceId={workspaceId}
        uid={uid}
        handleClose={handleClose}
        title="Algorithm Node Outputs"
        data={algorithmNodeOutputPathInfoList}
        renderData={() => []}
        emptyMessage="No output data available"
      />
    )
  }

  return (
    <BaseNodesView
      open={open}
      workspaceId={workspaceId}
      uid={uid}
      handleClose={handleClose}
      title="Algorithm Node Outputs"
      data={algorithmNodeOutputPathInfoList}
      renderData={() => [
        <Box key="visualization-container" sx={{ p: 2 }}>
          {renderAlgorithmNodeOutputList()}
        </Box>,
      ]}
      emptyMessage="No output data available"
    />
  )
}

interface DisplayOutputDataViewProps {
  nodeId: string
  outputKey: string
}

const DisplayOutputDataView = memo(function DisplayOutputDataView({
  nodeId,
  outputKey,
}: DisplayOutputDataViewProps) {
  const dispatch = useDispatch<AppDispatch>()
  const filePath = useSelector(
    selectPipelineNodeResultOutputFilePath(nodeId, outputKey),
  )
  const dataType = useSelector(
    selectPipelineNodeResultOutputFileDataType(nodeId, outputKey),
  )
  const itemId = useSelector(
    selectVisualizeItemIdForWorkflowDialog(nodeId, filePath, dataType),
  )

  useEffect(() => {
    if (itemId === null) {
      dispatch(addItemForWorkflowDialog({ nodeId, filePath, dataType }))
    }
  }, [dispatch, nodeId, filePath, dataType, itemId])

  if (itemId != null) {
    return <DisplayDataItem itemId={itemId} />
  } else {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <Typography color="textSecondary">Loading...</Typography>
      </Box>
    )
  }
})

export default OutputsView

import { ReactElement, useEffect } from "react"
import { useSelector, useDispatch } from "react-redux"

import {
  Box,
  Grid,
  Typography,
  Divider as MuiDivider,
  Chip,
} from "@mui/material"

import BaseNodesView, {
  renderVisualizationItems,
  useVisualizationCleanup,
  VisualizationItemData,
} from "components/Dataview/BaseNodesView"
import { getExperiments } from "store/slice/Experiments/ExperimentsActions"
import {
  selectExperimentsStatusIsFulfilled,
  selectExperimentsStatusIsUninitialized,
} from "store/slice/Experiments/ExperimentsSelectors"
import { selectNodeLabelById } from "store/slice/FlowElement/FlowElementSelectors"
import { selectPipelineNodeResultSuccessList } from "store/slice/Pipeline/PipelineSelectors"
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

  useVisualizationCleanup(open)

  const experimentsIsUninitialized = useSelector(
    selectExperimentsStatusIsUninitialized,
  )
  const experimentsIsFulfilled = useSelector(selectExperimentsStatusIsFulfilled)

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
            items: Object.entries(nodeResult.outputPaths).map(
              ([outputKey, value]) =>
                ({
                  nodeId,
                  filePath: value.path,
                  dataType: value.type,
                  title: outputKey,
                  subtitle: `Type: ${value.type}`,
                  itemKey: `${nodeId}-${outputKey}`,
                }) as VisualizationItemData,
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

  const renderData = (): ReactElement[] => {
    if (algorithmNodeOutputPathInfoList.length === 0) {
      return []
    }

    const visualizationSections: ReactElement[] = []

    algorithmNodeOutputPathInfoList.forEach((pathInfo) => {
      if (pathInfo.items.length > 0) {
        visualizationSections.push(
          <Box key={`node-section-${pathInfo.nodeId}`} sx={{ mb: 4 }}>
            <Typography variant="h5" gutterBottom sx={{ mb: 2 }}>
              {pathInfo.nodeName}
              <Chip
                label={pathInfo.nodeId}
                color="info"
                variant="outlined"
                sx={{ ml: 2 }}
              />
            </Typography>
            <MuiDivider sx={{ mb: 3 }} />
            <Grid container spacing={2}>
              {renderVisualizationItems(pathInfo.items)}
            </Grid>
          </Box>,
        )
      }
    })

    return [
      <Box key="visualization-container" sx={{ p: 2 }}>
        {visualizationSections}
      </Box>,
    ]
  }

  return (
    <BaseNodesView
      open={open}
      workspaceId={workspaceId}
      uid={uid}
      handleClose={handleClose}
      title="Algorithm Outputs"
      data={algorithmNodeOutputPathInfoList}
      renderData={renderData}
      emptyMessage={
        !experimentsIsFulfilled
          ? "Loading experiments data..."
          : "No output data available"
      }
    />
  )
}

export default OutputsView

import { ReactElement } from "react"
import { useSelector } from "react-redux"

import {
  Box,
  Grid,
  Typography,
  Divider as MuiDivider,
  Chip,
} from "@mui/material"

import BaseNodesView, {
  renderVisualizationItems,
  useDataviewVisualizationCleanup,
} from "components/Dataview/BaseNodesView"
import { selectOutputVisualizationItems } from "store/slice/Dataview/DataviewSelectors"

type OutputsViewProps = {
  open: boolean
  is_public: boolean
  workspaceId: number | undefined
  uid: string | undefined
  handleClose: () => void
}

const OutputsView = ({
  open,
  is_public,
  workspaceId,
  uid,
  handleClose,
}: OutputsViewProps) => {
  useDataviewVisualizationCleanup(open)

  const outputVisualizationItems = useSelector(
    selectOutputVisualizationItems(uid),
  )

  const renderData = (): ReactElement[] => {
    if (outputVisualizationItems.length === 0) {
      return []
    }

    const visualizationSections: ReactElement[] = []

    outputVisualizationItems.forEach((pathInfo) => {
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
      is_public={is_public}
      workspaceId={workspaceId}
      uid={uid}
      handleClose={handleClose}
      title="Algorithm Outputs"
      data={outputVisualizationItems}
      renderData={renderData}
      emptyMessage="No output data available"
    />
  )
}

export default OutputsView

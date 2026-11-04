import { ReactElement } from "react"
import { useDispatch, useSelector } from "react-redux"

import { enqueueSnackbar } from "notistack"

import {
  Box,
  Grid,
  Typography,
  Divider as MuiDivider,
  Chip,
  FormControlLabel,
  Switch,
} from "@mui/material"

import BaseNodesView, {
  renderVisualizationItems,
  useDataviewVisualizationCleanup,
  VisualizationItemData,
} from "components/Dataview/BaseNodesView"
import { putHiddenOutputs } from "store/slice/Dataview/DataviewActions"
import {
  selectDataviewPrivateRecord,
  selectOutputVisualizationItems,
} from "store/slice/Dataview/DataviewSelectors"
import { AppDispatch } from "store/store"

type OutputsViewProps = {
  open: boolean
  is_public: boolean
  workspaceId: number | undefined
  uid: string | undefined
  handleClose: () => void
}

// hidden_outputs are relative to the output dir; legacy records carry absolute paths
const isSamePath = (hidden: string, path: string) =>
  path === hidden || path.endsWith(`/${hidden}`)

const OutputsView = ({
  open,
  is_public,
  workspaceId,
  uid,
  handleClose,
}: OutputsViewProps) => {
  useDataviewVisualizationCleanup(open)
  const dispatch = useDispatch<AppDispatch>()

  const outputVisualizationItems = useSelector(
    selectOutputVisualizationItems(uid),
  )
  const record = useSelector(selectDataviewPrivateRecord(workspaceId, uid))
  const hiddenOutputs = record?.hidden_outputs ?? []

  const handleToggle = (item: VisualizationItemData, isPublic: boolean) => {
    if (!record) return
    const hidden = isPublic
      ? hiddenOutputs.filter((h) => !isSamePath(h, item.filePath))
      : [...hiddenOutputs, item.filePath]
    dispatch(putHiddenOutputs({ id: record.id, hiddenOutputs: hidden }))
      .unwrap()
      .catch(() =>
        enqueueSnackbar(`Failed to update visibility of ${item.title}`, {
          variant: "error",
        }),
      )
  }

  const renderPublishSwitch =
    !is_public && record
      ? (item: VisualizationItemData) => (
          <FormControlLabel
            label="Public"
            labelPlacement="start"
            control={
              <Switch
                size="small"
                checked={
                  !hiddenOutputs.some((h) => isSamePath(h, item.filePath))
                }
                onChange={(_, checked) => handleToggle(item, checked)}
                inputProps={{ "aria-label": `Publish ${item.title}` }}
              />
            }
          />
        )
      : undefined

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
              {renderVisualizationItems(pathInfo.items, renderPublishSwitch)}
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
      title="Workflow Outputs"
      data={outputVisualizationItems}
      renderData={renderData}
      emptyMessage="No output data available"
    />
  )
}

export default OutputsView

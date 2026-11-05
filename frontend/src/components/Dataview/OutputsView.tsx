import { ReactElement, useEffect, useState } from "react"
import { useDispatch, useSelector } from "react-redux"

import { enqueueSnackbar } from "notistack"

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
  VisualizationItemData,
} from "components/Dataview/BaseNodesView"
import OutputsOutline from "components/Dataview/OutputsOutline"
import {
  getHiddenOutputs,
  putHiddenOutputs,
} from "store/slice/Dataview/DataviewActions"
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
  const recordId = record?.id
  const hiddenOutputs = record?.hidden_outputs ?? []
  const editable = !is_public && recordId != null
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)

  // The stored list, or the defaults if the owner has not chosen yet
  useEffect(() => {
    if (!open || !editable || recordId == null) return
    setLoaded(false)
    dispatch(getHiddenOutputs({ id: recordId }))
      .unwrap()
      .catch(() =>
        enqueueSnackbar("Failed to load output visibility", {
          variant: "error",
        }),
      )
      .finally(() => setLoaded(true))
  }, [dispatch, open, editable, recordId])

  const isPublic = (item: VisualizationItemData) =>
    !hiddenOutputs.some((h) => isSamePath(h, item.filePath))

  const handleToggle = (item: VisualizationItemData, toPublic: boolean) => {
    if (recordId == null) return
    setSaving(true)
    dispatch(
      putHiddenOutputs({
        id: recordId,
        path: item.filePath,
        hidden: !toPublic,
      }),
    )
      .unwrap()
      .catch(() =>
        enqueueSnackbar(`Failed to update visibility of ${item.title}`, {
          variant: "error",
        }),
      )
      .finally(() => setSaving(false))
  }

  const renderData = (): ReactElement[] => {
    const sections = outputVisualizationItems.filter(
      (pathInfo) => pathInfo.items.length > 0,
    )
    if (sections.length === 0) {
      return []
    }

    if (editable) {
      return [
        <OutputsOutline
          key="outputs-outline"
          sections={sections}
          isPublic={isPublic}
          onToggle={handleToggle}
          disabled={saving || !loaded}
        />,
      ]
    }

    return [
      <Box key="visualization-container" sx={{ p: 2 }}>
        {sections.map((pathInfo) => (
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
          </Box>
        ))}
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

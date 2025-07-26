import { useEffect, MouseEvent, ReactElement, memo } from "react"
import { useDispatch, useSelector } from "react-redux"

import CloseIcon from "@mui/icons-material/Close"
import {
  Box,
  styled,
  Divider,
  ListSubheader,
  List,
  ListItem,
  ListItemText,
  Grid,
  Paper,
  Typography,
} from "@mui/material"

import { DisplayDataItem } from "components/Workspace/Visualize/DisplayDataItem"
import { DATA_TYPE } from "store/slice/DisplayData/DisplayDataType"
import { selectVisualizeItemIdForWorkflowDialog } from "store/slice/VisualizeItem/VisualizeItemSelectors"
import {
  addItemForWorkflowDialog,
  deleteAllItemForWorkflowDialog,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import { reproduceWorkflow } from "store/slice/Workflow/WorkflowActions"
import { AppDispatch } from "store/store"

export type NodesViewProps = {
  open: boolean
  workspaceId: number | undefined
  uid: string | undefined
  handleClose: () => void
  title: string
  data: unknown[]
  renderData: () => ReactElement[]
  emptyMessage: string
}

export interface VisualizationItemData {
  nodeId: string
  filePath: string
  dataType: DATA_TYPE
  title: string
  subtitle?: string
  itemKey: string
}

interface BaseDisplayDataViewProps {
  nodeId: string
  filePath: string
  dataType: DATA_TYPE
}

export const BaseDisplayDataView = memo(function BaseDisplayDataView({
  nodeId,
  filePath,
  dataType,
}: BaseDisplayDataViewProps) {
  const dispatch = useDispatch<AppDispatch>()
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

export const renderVisualizationItems = (
  items: VisualizationItemData[],
): ReactElement[] => {
  return items.map((item) => (
    <Grid item xs={12} md={6} lg={4} key={item.itemKey}>
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
          {item.title}
        </Typography>
        {item.subtitle && (
          <Typography variant="body2" color="textSecondary" gutterBottom>
            {item.subtitle}
          </Typography>
        )}
        <Typography variant="body2" color="textSecondary" gutterBottom noWrap>
          {item.filePath}
        </Typography>
        <Box sx={{ flexGrow: 1, minHeight: 280 }}>
          <BaseDisplayDataView
            nodeId={item.nodeId}
            filePath={item.filePath}
            dataType={item.dataType}
          />
        </Box>
      </Paper>
    </Grid>
  ))
}

export const useVisualizationCleanup = (open: boolean) => {
  const dispatch = useDispatch<AppDispatch>()

  useEffect(() => {
    return () => {
      if (!open) {
        dispatch(deleteAllItemForWorkflowDialog())
      }
    }
  }, [dispatch, open])
}

const BaseNodesView = ({
  open,
  workspaceId,
  uid,
  handleClose,
  title,
  data,
  renderData,
  emptyMessage,
}: NodesViewProps) => {
  const dispatch = useDispatch<AppDispatch>()

  useEffect(() => {
    if (open && uid && workspaceId) {
      dispatch(reproduceWorkflow({ workspaceId, uid }))
    }
  }, [open, uid, workspaceId, dispatch])

  useEffect(() => {
    const handleClosePopup = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        handleClose()
        return
      }
    }

    document.addEventListener("keydown", handleClosePopup)
    return () => {
      document.removeEventListener("keydown", handleClosePopup)
    }
    //eslint-disable-next-line
  }, [])

  const handleCloseWrapper = (event: MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    if (event.target === event.currentTarget) handleClose()
    return
  }

  return (
    <Box>
      {open ? (
        <NodesViewWrapper
          sx={{ position: "absolute", zIndex: 1 }}
          onClick={handleCloseWrapper}
        >
          <NodesViewContentWrapper sx={{ position: "absolute", zIndex: 10000 }}>
            <Box
              sx={{
                padding: 2,
                width: "100%",
                height: "100%",
                overflow: "auto",
              }}
            >
              <Box sx={{ marginBottom: 2, fontWeight: "bold" }}>
                {title} [uid: {uid}]
              </Box>
              {data.length > 0 ? (
                <List>{renderData()}</List>
              ) : (
                <Box sx={{ textAlign: "center", color: "gray" }}>
                  {emptyMessage}
                </Box>
              )}
            </Box>
            <ButtonClose onClick={handleClose}>
              <CloseIcon />
            </ButtonClose>
          </NodesViewContentWrapper>
        </NodesViewWrapper>
      ) : null}
    </Box>
  )
}

const NodesViewWrapper = styled(Box)(() => ({
  position: "fixed",
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  background: "rgba(255,255,255,0.7)",
  display: "flex",
  justifyContent: "center",
  alignItems: "center",
}))

const NodesViewContentWrapper = styled(Box)(() => ({
  position: "relative",
  display: "flex",
  background: "#FFF",
  justifyContent: "center",
  alignItems: "center",
  width: "80%",
  height: "80%",
  border: "1px solid #000",
  color: "#333333",
}))

const ButtonClose = styled("button")(() => ({
  border: "1px solid #000",
  position: "absolute",
  display: "block",
  top: -20,
  right: -20,
  width: 40,
  height: 40,
  cursor: "pointer",
  borderRadius: 50,
  "&:hover": {
    background: "#8f8a8a",
  },
}))

export default BaseNodesView
export { Divider, ListSubheader, ListItem, ListItemText }

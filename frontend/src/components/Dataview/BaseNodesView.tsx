import { useEffect, ReactElement, memo } from "react"
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
  Chip,
  Dialog,
  DialogContent,
  IconButton,
  Fade,
} from "@mui/material"

import { DisplayDataItem } from "components/Workspace/Visualize/DisplayDataItem"
import { publicDataviewReproduceWorkflow } from "store/slice/Dataview/DataviewActions"
import { DATA_TYPE } from "store/slice/DisplayData/DisplayDataType"
import { clearFlowElements } from "store/slice/FlowElement/FlowElementSlice"
import { clearCurrentPipeline } from "store/slice/Pipeline/PipelineSlice"
import { selectVisualizeItemIdForWorkflowDialog } from "store/slice/VisualizeItem/VisualizeItemSelectors"
import {
  addItemForWorkflowDialog,
  deleteAllItemForWorkflowDialog,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import { reproduceWorkflow } from "store/slice/Workflow/WorkflowActions"
import { AppDispatch } from "store/store"

export type NodesViewProps = {
  open: boolean
  is_public: boolean
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
      <LoadingContainer>
        <Typography color="textSecondary">Loading...</Typography>
      </LoadingContainer>
    )
  }
})

export const renderVisualizationItems = (
  items: VisualizationItemData[],
): ReactElement[] => {
  return items.map((item) => (
    <VisualizationGrid item xs={12} md={6} lg={5} xl={4} key={item.itemKey}>
      <VisualizationPaper elevation={2}>
        <Typography variant="h6" gutterBottom>
          {item.title}
        </Typography>
        {item.subtitle && (
          <Typography variant="body2" color="textSecondary" gutterBottom>
            <Chip label={item.subtitle} />
          </Typography>
        )}
        <DisplayDataContainer>
          <DisplayDataWrapper>
            <BaseDisplayDataView
              nodeId={item.nodeId}
              filePath={item.filePath}
              dataType={item.dataType}
            />
          </DisplayDataWrapper>
        </DisplayDataContainer>
      </VisualizationPaper>
    </VisualizationGrid>
  ))
}

export const useDataviewVisualizationCleanup = (open: boolean) => {
  const dispatch = useDispatch<AppDispatch>()

  useEffect(() => {
    return () => {
      if (!open) {
        // Visualize components cleanup
        dispatch(deleteAllItemForWorkflowDialog())

        // Reproduced workflow store cleanup
        dispatch(clearFlowElements())
        dispatch(clearCurrentPipeline())
      }
    }
  }, [dispatch, open])
}

const BaseNodesView = ({
  open,
  is_public,
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
      const api = is_public
        ? publicDataviewReproduceWorkflow
        : reproduceWorkflow
      dispatch(api({ workspaceId, uid }))
    }
  }, [open, is_public, uid, workspaceId, dispatch])

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

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: {
          width: "95%",
          height: "90%",
          maxHeight: "90vh",
          maxWidth: "1600px",
          margin: "2.5vh auto",
        },
      }}
      TransitionComponent={Fade}
      TransitionProps={{ timeout: 300 }}
    >
      <DialogContent
        sx={{
          position: "relative",
          padding: 0,
          height: "100%",
          overflow: "hidden",
        }}
      >
        <ContentArea>
          <TitleHeader>
            <Typography
              variant="h5"
              gutterBottom
              sx={{ mb: 2, fontWeight: "bold" }}
            >
              {title}
            </Typography>
            {uid && (
              <Chip
                label={`ID: ${uid}`}
                color="primary"
                variant="outlined"
                size="medium"
                sx={{ fontSize: "0.9rem" }}
              />
            )}
          </TitleHeader>
          {data.length > 0 ? (
            <List>{renderData()}</List>
          ) : (
            <EmptyMessage>{emptyMessage}</EmptyMessage>
          )}
        </ContentArea>
        <IconButton
          onClick={handleClose}
          aria-label="close"
          sx={{
            position: "absolute",
            top: 8,
            right: 20,
            border: 1,
            borderColor: "divider",
            backgroundColor: "background.paper",
            "&:hover": {
              backgroundColor: "action.hover",
            },
          }}
        >
          <CloseIcon />
        </IconButton>
      </DialogContent>
    </Dialog>
  )
}

// Styled Components

const ContentArea = styled(Box)(() => ({
  padding: "60px 24px 24px 24px",
  width: "100%",
  height: "100%",
  overflow: "auto",
  minWidth: "800px",
  boxSizing: "border-box",
}))

// Content Components
const TitleHeader = styled(Box)(() => ({
  marginBottom: 16,
  display: "flex",
  alignItems: "baseline",
  gap: 16,
}))

const EmptyMessage = styled(Box)(() => ({
  textAlign: "center",
  color: "gray",
}))

const LoadingContainer = styled(Box)(() => ({
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
}))

// Visualization Item Components
const VisualizationGrid = styled(Grid)(() => ({
  minWidth: 580,
  margin: "12px",
  maxWidth: "calc(50% - 24px)",
}))

const VisualizationPaper = styled(Paper)(() => ({
  padding: 24,
  minHeight: 400,
  minWidth: 580,
  display: "flex",
  flexDirection: "column",
  margin: "8px",
  boxSizing: "border-box",
}))

const DisplayDataContainer = styled(Box)(() => ({
  flexGrow: 1,
  display: "flex",
  flexDirection: "column",
  overflow: "auto",
  maxHeight: "500px",
  padding: "8px",
  minWidth: 0,
}))

const DisplayDataWrapper = styled(Box)(() => ({
  width: "100%",
  overflow: "auto",
  padding: "20px",
}))

export default BaseNodesView
export { Divider, ListSubheader, ListItem, ListItemText }

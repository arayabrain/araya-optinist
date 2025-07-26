import { ReactElement, memo, useEffect } from "react"
import { useSelector, useDispatch } from "react-redux"

import { Box, Grid, Paper, Typography } from "@mui/material"

import BaseNodesView from "components/Dataview/BaseNodesView"
import { DisplayDataItem } from "components/Workspace/Visualize/DisplayDataItem"
import { selectNodeLabelById } from "store/slice/FlowElement/FlowElementSelectors"
import { getFileName } from "store/slice/FlowElement/FlowElementUtils"
import {
  selectInputNode,
  selectInputNodeFileType,
} from "store/slice/InputNode/InputNodeSelectors"
import { selectVisualizeItemIdForWorkflowDialog } from "store/slice/VisualizeItem/VisualizeItemSelectors"
import {
  addItemForWorkflowDialog,
  deleteAllItemForWorkflowDialog,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import { RootState, AppDispatch } from "store/store"
import { toDataTypeFromFileType } from "utils/DataTypeUtils"

type InputsViewProps = {
  open: boolean
  workspaceId: number | undefined
  uid: string | undefined
  handleClose: () => void
}

const InputsView = ({
  open,
  workspaceId,
  uid,
  handleClose,
}: InputsViewProps) => {
  const dispatch = useDispatch<AppDispatch>()

  useEffect(() => {
    return () => {
      if (!open) {
        dispatch(deleteAllItemForWorkflowDialog())
      }
    }
  }, [dispatch, open])

  const inputNodeData = useSelector((state: RootState) => {
    const inputNodes = selectInputNode(state)
    const filteredInputNodes = Object.entries(inputNodes)
      .map(([nodeId, inputNode]) => ({
        nodeId,
        filePath: inputNode.selectedFilePath,
        fileType: inputNode.fileType,
        nodeName: selectNodeLabelById(nodeId)(state) || nodeId,
      }))
      .filter(({ filePath }) => filePath != null)
    return filteredInputNodes
  })

  const renderInputNodeList = (): ReactElement[] => {
    const visualizationItems: ReactElement[] = []

    inputNodeData.forEach((nodeData) => {
      const filePath = nodeData.filePath

      if (Array.isArray(filePath)) {
        filePath.forEach((pathElm, index) => {
          visualizationItems.push(
            <Grid
              item
              xs={12}
              md={6}
              lg={4}
              key={`${nodeData.nodeId}-${index}`}
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
                  {getFileName(pathElm)}
                </Typography>
                <Typography variant="body2" color="textSecondary" gutterBottom>
                  {pathElm}
                </Typography>
                <Box sx={{ flexGrow: 1, minHeight: 300 }}>
                  <DisplayInputDataView
                    nodeId={nodeData.nodeId}
                    filePath={pathElm}
                  />
                </Box>
              </Paper>
            </Grid>,
          )
        })
      } else if (filePath) {
        visualizationItems.push(
          <Grid item xs={12} md={6} lg={4} key={nodeData.nodeId}>
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
                {nodeData.nodeName}
              </Typography>
              <Typography variant="body2" color="textSecondary" gutterBottom>
                {filePath}
              </Typography>
              <Box sx={{ flexGrow: 1, minHeight: 300 }}>
                <DisplayInputDataView
                  nodeId={nodeData.nodeId}
                  filePath={filePath}
                />
              </Box>
            </Paper>
          </Grid>,
        )
      }
    })

    return visualizationItems
  }

  if (inputNodeData.length === 0) {
    return (
      <BaseNodesView
        open={open}
        workspaceId={workspaceId}
        uid={uid}
        handleClose={handleClose}
        title="Input Node Data"
        data={inputNodeData}
        renderData={() => []}
        emptyMessage="No input node data available"
      />
    )
  }

  return (
    <BaseNodesView
      open={open}
      workspaceId={workspaceId}
      uid={uid}
      handleClose={handleClose}
      title="Input Node Data"
      data={inputNodeData}
      renderData={() => [
        <Grid container spacing={2} key="visualization-grid">
          {renderInputNodeList()}
        </Grid>,
      ]}
      emptyMessage="No input node data available"
    />
  )
}

interface DisplayInputDataViewProps {
  nodeId: string
  filePath: string
}

const DisplayInputDataView = memo(function DisplayInputDataView({
  nodeId,
  filePath,
}: DisplayInputDataViewProps) {
  const dispatch = useDispatch<AppDispatch>()
  const fileType = useSelector(selectInputNodeFileType(nodeId))
  const dataType = toDataTypeFromFileType(fileType)
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

export default InputsView

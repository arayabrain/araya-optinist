import { useEffect, MouseEvent, ReactElement } from "react"
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
} from "@mui/material"

import { selectNodeLabelById } from "store/slice/FlowElement/FlowElementSelectors"
import { getFileName } from "store/slice/FlowElement/FlowElementUtils"
import { selectInputNode } from "store/slice/InputNode/InputNodeSelectors"
import { reproduceWorkflow } from "store/slice/Workflow/WorkflowActions"
import { RootState, AppDispatch } from "store/store"

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

  const renderInputNodeList = (): ReactElement[] => {
    const menuItemList: ReactElement[] = []
    inputNodeData.forEach((nodeData) => {
      const filePath = nodeData.filePath
      if (Array.isArray(filePath)) {
        menuItemList.push(
          <ListSubheader key={`header-${nodeData.nodeId}`}>
            <Divider textAlign="center">{nodeData.nodeName}</Divider>
          </ListSubheader>,
        )
        filePath.forEach((pathElm, index) => {
          menuItemList.push(
            <ListItem key={`${nodeData.nodeId}-${index}`}>
              <ListItemText
                primary={getFileName(pathElm)}
                secondary={pathElm}
              />
            </ListItem>,
          )
        })
      } else {
        menuItemList.push(
          <ListItem key={nodeData.nodeId}>
            <ListItemText
              primary={nodeData.nodeName}
              secondary={filePath || "No file path"}
            />
          </ListItem>,
        )
      }
    })

    return menuItemList
  }

  return (
    <Box>
      {open ? (
        <InputsViewWrapper
          sx={{ position: "absolute", zIndex: 1 }}
          onClick={handleCloseWrapper}
        >
          <InputsViewContentWrapper
            sx={{ position: "absolute", zIndex: 10000 }}
          >
            <Box
              sx={{
                padding: 2,
                width: "100%",
                height: "100%",
                overflow: "auto",
              }}
            >
              <Box sx={{ marginBottom: 2, fontWeight: "bold" }}>
                Input Node Data [uid: {uid}]
              </Box>
              {inputNodeData.length > 0 ? (
                <List>{renderInputNodeList()}</List>
              ) : (
                <Box sx={{ textAlign: "center", color: "gray" }}>
                  No input node data available
                </Box>
              )}
            </Box>
            <ButtonClose onClick={handleClose}>
              <CloseIcon />
            </ButtonClose>
          </InputsViewContentWrapper>
        </InputsViewWrapper>
      ) : null}
    </Box>
  )
}

const InputsViewWrapper = styled(Box)(() => ({
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

const InputsViewContentWrapper = styled(Box)(() => ({
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

export default InputsView

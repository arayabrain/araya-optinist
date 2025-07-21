import { useEffect, MouseEvent, ReactElement } from "react"
import { useDispatch } from "react-redux"

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

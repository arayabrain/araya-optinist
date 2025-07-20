import { useEffect, MouseEvent } from "react"

import CloseIcon from "@mui/icons-material/Close"
import { Box, styled } from "@mui/material"

type OutputsViewProps = {
  open: boolean
  uid: string
  handleClose: () => void
}

const OutputsView = ({ open, uid, handleClose }: OutputsViewProps) => {
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
        <OutputsViewWrapper
          sx={{ position: "absolute", zIndex: 1 }}
          onClick={handleCloseWrapper}
        >
          <OutputsViewContentWrapper
            sx={{ position: "absolute", zIndex: 10000 }}
          >
            [uid: {uid}]
            <ButtonClose onClick={handleClose}>
              <CloseIcon />
            </ButtonClose>
          </OutputsViewContentWrapper>
        </OutputsViewWrapper>
      ) : null}
    </Box>
  )
}

const OutputsViewWrapper = styled(Box)(() => ({
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

const OutputsViewContentWrapper = styled(Box)(() => ({
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

export default OutputsView

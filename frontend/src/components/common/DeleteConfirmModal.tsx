import { FC, useEffect, useState } from "react"

import { enqueueSnackbar } from "notistack"

import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  styled,
  Typography,
} from "@mui/material"

import { DialogContentWithIcon } from "components/common/ConfirmDialog"
import Input from "components/common/Input"
import Loading from "components/common/Loading"

type DeleteConfirmModalProps = {
  onClose: () => void
  open: boolean
  onSubmit: () => void
  titleSubmit: string
  description: string
  warningItems?: string[]
  loading?: boolean
  iconType?: "warning" | "info"
}
const DeleteConfirmModal: FC<DeleteConfirmModalProps> = ({
  onClose,
  open,
  onSubmit,
  loading,
  titleSubmit,
  description,
  warningItems,
  iconType,
}) => {
  useEffect(() => {
    if (!open) {
      setTextDelete("")
    }
  }, [open])

  const [textDelete, setTextDelete] = useState("")

  const onConfirm = () => {
    if (textDelete !== "DELETE") {
      enqueueSnackbar("Please type DELETE to confirm", { variant: "error" })
      return
    }
    onSubmit?.()
    setTextDelete("")
  }

  const onCancel = () => {
    setTextDelete("")
    onClose()
  }

  const content = (
    <DialogContentText component="div">
      {warningItems && warningItems.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {warningItems.map((item, index) => (
              <li key={index}>
                <Typography variant="body2" color="error">
                  {item}
                </Typography>
              </li>
            ))}
          </ul>
        </Box>
      )}
      <Typography style={{ whiteSpace: "pre-wrap" }}>
        To continue, type <span style={{ fontWeight: 600 }}>DELETE</span> in the
        box below:
      </Typography>
    </DialogContentText>
  )

  return (
    <>
      <Dialog open={open} onClose={onClose} maxWidth={"xs"}>
        <DialogTitle>{description}</DialogTitle>
        <DialogContent>
          {iconType ? (
            <DialogContentWithIcon content={content} iconType={iconType} />
          ) : (
            content
          )}
          <BoxConfirm>
            <Input
              placeholder="DELETE"
              value={textDelete}
              onChange={(e) => setTextDelete(e.target.value)}
              sx={{ width: "calc(100% - 20px)" }}
            />
          </BoxConfirm>
        </DialogContent>
        <DialogActions>
          <Button onClick={onCancel} variant={"outlined"}>
            CANCEL
          </Button>
          <Button
            onClick={onConfirm}
            color={"error"}
            variant="contained"
            disabled={textDelete !== "DELETE"}
          >
            {titleSubmit}
          </Button>
        </DialogActions>
      </Dialog>
      <Loading loading={loading} />
    </>
  )
}

const BoxConfirm = styled(Box)({
  margin: "20px 0 0",
})

export default DeleteConfirmModal

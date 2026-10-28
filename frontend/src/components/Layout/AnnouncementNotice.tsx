import { FC, useEffect, useState } from "react"

import Alert from "@mui/material/Alert"
import Button from "@mui/material/Button"
import ButtonBase from "@mui/material/ButtonBase"
import Dialog from "@mui/material/Dialog"
import DialogActions from "@mui/material/DialogActions"
import DialogContent from "@mui/material/DialogContent"
import DialogContentText from "@mui/material/DialogContentText"
import DialogTitle from "@mui/material/DialogTitle"
import Typography from "@mui/material/Typography"

import { BASE_URL } from "const/API"
import { tabSync } from "utils/crossTabSync"

export const ANNOUNCEMENT_DISMISSED_KEY = "announcementDismissed"
export const ANNOUNCEMENT_POLL_INTERVAL_MS = 10 * 60 * 1000
const BROADCAST_PREFIX = "announcement:"

interface Announcement {
  id: string
  message: string
}

const readDismissedId = (): string | null => {
  try {
    return localStorage.getItem(ANNOUNCEMENT_DISMISSED_KEY)
  } catch {
    return null
  }
}

const writeDismissedId = (id: string) => {
  try {
    localStorage.setItem(ANNOUNCEMENT_DISMISSED_KEY, id)
  } catch {
    // Blocked storage: the dismissal lasts for this page load only.
  }
}

// Plain fetch without auth or routing headers, so the ALB default action
// sends it to the public tier. Returns undefined when the answer is unknown.
const fetchAnnouncement = async (): Promise<
  Announcement | null | undefined
> => {
  try {
    const res = await fetch(`${BASE_URL}/announcement`, { cache: "no-store" })
    if (!res.ok) return undefined
    const { id, message } = await res.json()
    return id && message ? { id, message } : null
  } catch {
    return undefined
  }
}

const AnnouncementNotice: FC = () => {
  const [announcement, setAnnouncement] = useState<Announcement | null>(null)
  const [dismissedId, setDismissedId] = useState(readDismissedId)
  const [openId, setOpenId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      const result = await fetchAnnouncement()
      if (!cancelled && result !== undefined) setAnnouncement(result)
    }
    check()
    const timer = setInterval(check, ANNOUNCEMENT_POLL_INTERVAL_MS)
    const unsubscribe = tabSync.on("ALERT_DISMISSED", (msg) => {
      const alertId = (msg.payload as { alertId?: string } | undefined)?.alertId
      if (alertId?.startsWith(BROADCAST_PREFIX)) {
        setDismissedId(alertId.slice(BROADCAST_PREFIX.length))
      }
    })
    return () => {
      cancelled = true
      clearInterval(timer)
      unsubscribe()
    }
  }, [])

  if (!announcement || announcement.id === dismissedId) return null
  const { id, message } = announcement
  const summary = message.split("\n")[0]

  const dismiss = () => {
    writeDismissedId(id)
    tabSync.broadcastAlertDismissed(BROADCAST_PREFIX + id)
    setDismissedId(id)
  }

  return (
    <>
      <ButtonBase
        onClick={() => setOpenId(id)}
        aria-label={`Announcement: ${summary}`}
        sx={{
          minWidth: 0,
          maxWidth: 720,
          flexShrink: { xs: 0, md: 1 },
          borderRadius: 1,
        }}
      >
        <Alert
          severity="warning"
          role="status"
          sx={{
            py: 0,
            minWidth: 0,
            alignItems: "center",
            "& .MuiAlert-icon": { mr: { xs: 0, md: 1.5 } },
            "& .MuiAlert-message": { overflow: "hidden", minWidth: 0 },
          }}
        >
          <Typography
            variant="body2"
            noWrap
            sx={{ display: { xs: "none", md: "block" } }}
          >
            {summary}
          </Typography>
        </Alert>
      </ButtonBase>
      <Dialog
        open={openId === id}
        onClose={() => setOpenId(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Announcement</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ whiteSpace: "pre-line" }}>
            {message}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={dismiss}>Hide until next sign-in</Button>
          <Button variant="contained" onClick={() => setOpenId(null)}>
            Close
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
}

export default AnnouncementNotice

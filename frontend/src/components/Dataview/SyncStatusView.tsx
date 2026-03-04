import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline"
import HourglassEmptyIcon from "@mui/icons-material/HourglassEmpty"
import { Alert, Box, Button, Typography } from "@mui/material"

import { SyncStatus } from "components/Dataview/useSyncRetry"

interface SyncStatusViewProps {
  syncStatus: SyncStatus
  onRetry: () => void
}

export const SyncStatusView = ({
  syncStatus,
  onRetry,
}: SyncStatusViewProps) => {
  if (syncStatus.pending) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          py: 4,
          gap: 2,
        }}
      >
        <HourglassEmptyIcon
          sx={{
            fontSize: 40,
            color: "warning.main",
            animation: "spin 1.5s ease-in-out infinite",
            "@keyframes spin": {
              "0%": { transform: "rotate(0deg)" },
              "50%": { transform: "rotate(180deg)" },
              "100%": { transform: "rotate(360deg)" },
            },
          }}
        />
        <Alert severity="info" sx={{ width: "100%" }}>
          <Typography variant="body1" gutterBottom>
            {syncStatus.message}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            This page will auto-retry.
          </Typography>
        </Alert>
      </Box>
    )
  }

  if (syncStatus.error) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          py: 4,
          gap: 2,
        }}
      >
        <ErrorOutlineIcon sx={{ fontSize: 48, color: "error.main" }} />
        <Alert severity="error" sx={{ width: "100%" }}>
          <Typography variant="body1">{syncStatus.message}</Typography>
        </Alert>
        <Button variant="outlined" onClick={onRetry}>
          Retry
        </Button>
      </Box>
    )
  }

  return null
}

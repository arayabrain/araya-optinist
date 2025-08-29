import React, { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"

import { useSnackbar } from "notistack"

import { Close as CloseIcon, Upgrade as UpgradeIcon } from "@mui/icons-material"
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  IconButton,
  LinearProgress,
  Typography,
} from "@mui/material"

import {
  getMyLimitWarningApi,
  LimitWarning as LimitWarningType,
} from "api/storage/StorageAlerts"
import { getToken } from "utils/auth/AuthUtils"

interface LimitWarningProps {
  showAsModal?: boolean
  onClose?: () => void
  autoCheck?: boolean
}

const LimitWarning: React.FC<LimitWarningProps> = ({
  showAsModal = false,
  onClose,
  autoCheck = true,
}) => {
  const { enqueueSnackbar: _enqueueSnackbar } = useSnackbar()
  const navigate = useNavigate()
  const [warning, setWarning] = useState<LimitWarningType | null>(null)
  const [loading, setLoading] = useState(true)
  const [dismissed, setDismissed] = useState(() => {
    // Check if this warning was already dismissed in localStorage
    const dismissedWarnings = localStorage.getItem("dismissedWarnings")
    if (dismissedWarnings) {
      try {
        const parsed = JSON.parse(dismissedWarnings)
        return parsed.limitWarning === true
      } catch {
        return false
      }
    }
    return false
  })

  const fetchLimitWarning = async () => {
    try {
      setLoading(true)
      const warningResponse = await getMyLimitWarningApi()
      setWarning(warningResponse)
    } catch (error) {
      // Silently fail to not disrupt the main UI
    } finally {
      setLoading(false)
    }
  }

  const handleDismiss = () => {
    // Persist dismissal in localStorage
    const dismissedWarnings = localStorage.getItem("dismissedWarnings")
    let parsed = {}
    try {
      parsed = dismissedWarnings ? JSON.parse(dismissedWarnings) : {}
    } catch {
      // Handle JSON parse errors
    }

    localStorage.setItem(
      "dismissedWarnings",
      JSON.stringify({
        ...parsed,
        limitWarning: true,
      }),
    )

    setDismissed(true)
    onClose?.()
  }

  const handleUpgrade = () => {
    // Dismiss the warning when user clicks upgrade
    handleDismiss()
    // Navigate to payment page
    navigate("/payment")
  }

  useEffect(() => {
    if (autoCheck) {
      if (getToken()) {
        fetchLimitWarning()
      } else {
        // No token, stop loading immediately
        setLoading(false)
      }
    }
  }, [autoCheck])

  if (loading) {
    return showAsModal ? null : (
      <Box display="flex" alignItems="center" gap={1} p={1}>
        <CircularProgress size={16} />
        <Typography variant="caption">Checking account status...</Typography>
      </Box>
    )
  }

  if (dismissed || !warning?.has_warning) {
    return null
  }

  // Determine what actions to show based on warning type and conditions
  const hasStorageIssue = warning.excess_data_gb > 0
  const hasSubscriptionIssue =
    warning.warning_type === "grace" || warning.warning_type === "overdue"
  const showUpgradeButton = hasStorageIssue || hasSubscriptionIssue
  const showManageFilesButton = hasStorageIssue

  const getSeverity = (warningType: string) => {
    switch (warningType) {
      case "overdue":
        return "error"
      case "storage":
        return "warning"
      case "grace":
        return "warning"
      default:
        return "warning"
    }
  }

  const getTitle = (warningType: string) => {
    switch (warningType) {
      case "overdue":
        return "Data Cleanup Overdue"
      case "storage":
        return "Storage Limit Exceeded"
      case "grace":
        return "Premium Subscription Expired"
      default:
        return "Storage Warning"
    }
  }

  const getProgressColor = (daysRemaining: number) => {
    if (daysRemaining <= 0) return "error"
    if (daysRemaining <= 7) return "error"
    if (daysRemaining <= 14) return "warning"
    return "primary"
  }

  const progressValue =
    warning.days_remaining > 0
      ? Math.max(0, Math.min(100, (warning.days_remaining / 30) * 100))
      : 0

  const warningContent = (
    <Box>
      <Alert
        severity={getSeverity(warning.warning_type)}
        action={
          !showAsModal && (
            <IconButton size="small" onClick={handleDismiss}>
              <CloseIcon />
            </IconButton>
          )
        }
        sx={{ mb: showAsModal ? 0 : 2 }}
      >
        <AlertTitle>{getTitle(warning.warning_type)}</AlertTitle>

        <Typography variant="body2" sx={{ mb: 2 }}>
          {warning.message}
        </Typography>

        {/* Days remaining progress bar */}
        {warning.days_remaining > 0 && (
          <Box sx={{ mb: 2 }}>
            <Box display="flex" justifyContent="space-between" mb={1}>
              <Typography variant="caption" fontWeight="bold">
                Days Remaining
              </Typography>
              <Typography variant="caption" fontWeight="bold">
                {warning.days_remaining} days
              </Typography>
            </Box>
            <LinearProgress
              variant="determinate"
              value={progressValue}
              color={getProgressColor(warning.days_remaining)}
              sx={{ height: 8, borderRadius: 4 }}
            />
          </Box>
        )}

        {/* Storage information */}
        <Box
          sx={{
            bgcolor: "background.paper",
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            p: 2,
            mb: 2,
          }}
        >
          <Typography variant="subtitle2" gutterBottom>
            Storage Usage Details
          </Typography>

          <Box display="flex" justifyContent="space-between" mb={1}>
            <Typography variant="body2" color="text.secondary">
              Current Usage:
            </Typography>
            <Typography variant="body2" fontWeight="bold">
              {warning.storage_usage_gb} GB
            </Typography>
          </Box>

          <Box display="flex" justifyContent="space-between" mb={1}>
            <Typography variant="body2" color="text.secondary">
              Free Plan Limit:
            </Typography>
            <Typography variant="body2">
              {warning.storage_quota_gb} GB
            </Typography>
          </Box>

          <Box display="flex" justifyContent="space-between">
            <Typography
              variant="body2"
              color={hasStorageIssue ? "error.main" : "text.secondary"}
              fontWeight={hasStorageIssue ? "bold" : "normal"}
            >
              Excess Data:
            </Typography>
            <Typography
              variant="body2"
              color={hasStorageIssue ? "error.main" : "text.primary"}
              fontWeight={hasStorageIssue ? "bold" : "normal"}
            >
              {warning.excess_data_gb} GB
            </Typography>
          </Box>
        </Box>

        {/* Action buttons - only show when not in modal mode */}
        {!showAsModal && (
          <Box display="flex" gap={1} flexWrap="wrap">
            <Button
              variant="contained"
              color="primary"
              startIcon={<UpgradeIcon />}
              onClick={handleUpgrade}
              size="small"
            >
              Upgrade to Premium
            </Button>
            <Button
              variant="contained"
              color="primary"
              onClick={() => {
                handleDismiss()
                window.location.href = "/workspace"
              }}
              size="small"
            >
              Manage Files
            </Button>
          </Box>
        )}
      </Alert>
    </Box>
  )

  if (showAsModal) {
    return (
      <Dialog
        open={Boolean(warning?.has_warning && !dismissed)}
        onClose={handleDismiss}
        maxWidth="md"
        fullWidth
      >
        <DialogContent>{warningContent}</DialogContent>
        <DialogActions>
          <Button onClick={handleDismiss} color="inherit">
            Handle later
          </Button>
          {showManageFilesButton && (
            <Button
              variant="contained"
              color="primary"
              onClick={() => {
                handleDismiss()
                navigate("/console/workspaces")
              }}
            >
              Manage Files
            </Button>
          )}
          {showUpgradeButton && (
            <Button
              variant="contained"
              onClick={() => {
                handleDismiss()
                navigate("/console/subscription")
              }}
              startIcon={<UpgradeIcon />}
            >
              Upgrade
            </Button>
          )}
        </DialogActions>
      </Dialog>
    )
  }

  return warningContent
}

export default LimitWarning

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
  getMyLimitAlertApi,
  LimitAlert as LimitAlertType,
} from "api/storage/StorageAlerts"
import { SubscriptionPeriods } from "const/Subscription"
import { getToken } from "utils/auth/AuthUtils"

interface LimitAlertProps {
  showAsModal?: boolean
  onClose?: () => void
  autoCheck?: boolean
}

const LimitAlert: React.FC<LimitAlertProps> = ({
  showAsModal = false,
  onClose,
  autoCheck = true,
}) => {
  const { enqueueSnackbar: _enqueueSnackbar } = useSnackbar()
  const navigate = useNavigate()
  const [alert, setAlert] = useState<LimitAlertType | null>(null)
  const [loading, setLoading] = useState(true)
  const [dismissed, setDismissed] = useState(() => {
    // Check if this alert was already dismissed in localStorage
    const dismissedAlerts = localStorage.getItem("dismissedAlerts")
    if (dismissedAlerts) {
      try {
        const parsed = JSON.parse(dismissedAlerts)
        return parsed.limitAlert === true
      } catch {
        return false
      }
    }
    return false
  })

  const fetchLimitAlert = async () => {
    try {
      setLoading(true)
      const alertResponse = await getMyLimitAlertApi()
      setAlert(alertResponse)
    } catch (error) {
      // Silently fail to not disrupt the main UI
    } finally {
      setLoading(false)
    }
  }

  const handleDismiss = () => {
    // Persist dismissal in localStorage
    const dismissedAlerts = localStorage.getItem("dismissedAlerts")
    let parsed = {}
    try {
      parsed = dismissedAlerts ? JSON.parse(dismissedAlerts) : {}
    } catch {
      // Handle JSON parse errors
    }

    localStorage.setItem(
      "dismissedAlerts",
      JSON.stringify({
        ...parsed,
        limitAlert: true,
      }),
    )

    setDismissed(true)
    onClose?.()
  }

  const handleUpgrade = () => {
    // Dismiss the alert when user clicks upgrade
    handleDismiss()
    // Navigate to payment page
    navigate("/payment")
  }

  useEffect(() => {
    if (autoCheck) {
      if (getToken()) {
        fetchLimitAlert()
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

  if (dismissed || !alert?.has_alert) {
    return null
  }

  // Determine what actions to show based on alert type and conditions
  const hasStorageIssue = alert.excess_data_gb > 0
  const hasSubscriptionIssue =
    alert.alert_type === "grace" || alert.alert_type === "overdue"

  // Only show upgrade button if:
  // 1. User has subscription expiration issues (was premium, now expired), OR
  // 2. User is a free user with storage issues (has deletion_date, meaning they're on free plan)
  // Note: Active premium users with storage issues don't have deletion_date and shouldn't see upgrade button
  const isFreeUserWithStorageIssue = hasStorageIssue && !!alert.deletion_date
  const showUpgradeButton = hasSubscriptionIssue || isFreeUserWithStorageIssue
  const showManageFilesButton = hasStorageIssue

  const getSeverity = (alertType: string) => {
    switch (alertType) {
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

  const getTitle = (alertType: string) => {
    switch (alertType) {
      case "overdue":
        return "Data Cleanup Overdue"
      case "storage":
        return "Storage Limit Exceeded"
      case "grace":
        return "Premium Subscription Expired"
      default:
        return "Storage Alert"
    }
  }

  const getProgressColor = (daysRemaining: number) => {
    if (daysRemaining <= SubscriptionPeriods.CRITICAL_THRESHOLD_DAYS)
      return "error"
    if (daysRemaining <= SubscriptionPeriods.URGENT_THRESHOLD_DAYS)
      return "error"
    if (daysRemaining <= SubscriptionPeriods.WARNING_THRESHOLD_DAYS)
      return "warning"
    return "primary"
  }

  const progressValue =
    alert.days_remaining > 0
      ? Math.max(
          SubscriptionPeriods.MIN_PROGRESS_PERCENT,
          Math.min(
            SubscriptionPeriods.MAX_PROGRESS_PERCENT,
            (alert.days_remaining /
              SubscriptionPeriods.PROGRESS_REFERENCE_DAYS) *
              SubscriptionPeriods.MAX_PROGRESS_PERCENT,
          ),
        )
      : 0

  const alertContent = (
    <Box>
      <Alert
        severity={getSeverity(alert.alert_type)}
        action={
          !showAsModal && (
            <IconButton size="small" onClick={handleDismiss}>
              <CloseIcon />
            </IconButton>
          )
        }
        sx={{ mb: showAsModal ? 0 : 2 }}
      >
        <AlertTitle>{getTitle(alert.alert_type)}</AlertTitle>

        <Typography variant="body2" sx={{ mb: 2 }}>
          {alert.message}
        </Typography>

        {/* Days remaining progress bar */}
        {alert.days_remaining > 0 && (
          <Box sx={{ mb: 2 }}>
            <Box display="flex" justifyContent="space-between" mb={1}>
              <Typography variant="caption" fontWeight="bold">
                Days Remaining
              </Typography>
              <Typography variant="caption" fontWeight="bold">
                {alert.days_remaining} days
              </Typography>
            </Box>
            <LinearProgress
              variant="determinate"
              value={progressValue}
              color={getProgressColor(alert.days_remaining)}
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
              {alert.storage_usage_gb} GB
            </Typography>
          </Box>

          <Box display="flex" justifyContent="space-between" mb={1}>
            <Typography variant="body2" color="text.secondary">
              Free Plan Limit:
            </Typography>
            <Typography variant="body2">{alert.storage_quota_gb} GB</Typography>
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
              {alert.excess_data_gb} GB
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
        open={Boolean(alert?.has_alert && !dismissed)}
        onClose={handleDismiss}
        maxWidth="md"
        fullWidth
      >
        <DialogContent>{alertContent}</DialogContent>
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

  return alertContent
}

export default LimitAlert

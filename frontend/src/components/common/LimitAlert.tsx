import React, { useCallback, useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"

import { useSnackbar } from "notistack"

import {
  Close as CloseIcon,
  Upgrade as UpgradeIcon,
  Warning as WarningIcon,
} from "@mui/icons-material"
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  LinearProgress,
  Typography,
} from "@mui/material"

import {
  getMyLimitAlertApi,
  LimitAlert as LimitAlertData,
} from "api/storage/StorageAlerts"
import { SubscriptionPeriods, LimitAlertType } from "const/Subscription"
import { getToken } from "utils/auth/AuthUtils"
import { tabSync } from "utils/crossTabSync"

/**
 * Format time remaining with appropriate granularity.
 * Shows hours when less than 1 day, otherwise shows days (rounded up at 12+ hours).
 */
const formatTimeRemaining = (
  daysRemaining: number,
  deletionDate?: string | null,
): string => {
  if (daysRemaining <= 0) return "0 days"

  if (daysRemaining >= 1) {
    return `${Math.floor(daysRemaining)} day${daysRemaining >= 2 ? "s" : ""}`
  }

  // Less than 1 day - calculate hours from deletion date if available
  if (deletionDate) {
    const now = new Date()
    const endDate = new Date(deletionDate)
    const diffMs = endDate.getTime() - now.getTime()

    if (diffMs <= 0) return "0 hours"

    const hours = Math.ceil(diffMs / (1000 * 60 * 60))
    return `${hours} hour${hours !== 1 ? "s" : ""}`
  }

  // Fallback to hours estimate from fractional days
  const hours = Math.ceil(daysRemaining * 24)
  if (hours <= 0) return "Less than 1 hour"
  return `${hours} hour${hours !== 1 ? "s" : ""}`
}

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
  const [alert, setAlert] = useState<LimitAlertData | null>(null)
  const [loading, setLoading] = useState(true)
  const [acknowledgedOverdue, setAcknowledgedOverdue] = useState(false)
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

  // Cross-tab sync: listen for dismissal changes via localStorage events
  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === "dismissedAlerts" && e.newValue) {
        try {
          const parsed = JSON.parse(e.newValue)
          if (parsed.limitAlert === true) {
            setDismissed(true)
          }
        } catch {
          // Ignore parse errors
        }
      }
    }

    window.addEventListener("storage", handleStorageChange)
    return () => window.removeEventListener("storage", handleStorageChange)
  }, [])

  // Cross-tab sync: listen for dismissal via BroadcastChannel (faster sync)
  useEffect(() => {
    const unsubscribe = tabSync.on("ALERT_DISMISSED", (message) => {
      const payload = message.payload as { alertId?: string }
      if (payload?.alertId === "limitAlert") {
        setDismissed(true)
      }
    })
    return unsubscribe
  }, [])

  const fetchLimitAlert = useCallback(async () => {
    try {
      setLoading(true)
      const alertResponse = await getMyLimitAlertApi()
      setAlert(alertResponse)
    } catch (error) {
      // Silently fail to not disrupt the main UI
    } finally {
      setLoading(false)
    }
  }, [])

  // Check if this is an OVERDUE alert that requires acknowledgment
  const isOverdueAlert = alert?.alert_type === LimitAlertType.OVERDUE

  const handleDismiss = (forceAcknowledged = false) => {
    if (isOverdueAlert && !acknowledgedOverdue && !forceAcknowledged) {
      return
    }

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

    // Broadcast dismissal to other tabs for immediate sync
    tabSync.broadcastAlertDismissed("limitAlert")

    setDismissed(true)
    setAcknowledgedOverdue(false)
    onClose?.()
  }

  const handleUpgrade = () => {
    handleDismiss(true)
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
  }, [autoCheck, fetchLimitAlert])

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
    alert.alert_type === LimitAlertType.GRACE ||
    alert.alert_type === LimitAlertType.OVERDUE

  // Only show upgrade button if:
  // 1. User has subscription expiration issues (was premium, now expired), OR
  // 2. User is a free user with storage issues (has deletion_date, meaning they're on free plan)
  // Note: Active premium users with storage issues don't have deletion_date and shouldn't see upgrade button
  const isFreeUserWithStorageIssue = hasStorageIssue && !!alert.deletion_date
  const showUpgradeButton = hasSubscriptionIssue || isFreeUserWithStorageIssue
  const showManageFilesButton = hasStorageIssue

  const getSeverity = (alertType: LimitAlertType) => {
    switch (alertType) {
      case LimitAlertType.OVERDUE:
        return "error"
      case LimitAlertType.STORAGE:
        return "warning"
      case LimitAlertType.GRACE:
        return "warning"
      default:
        return "warning"
    }
  }

  const getTitle = (alertType: LimitAlertType) => {
    switch (alertType) {
      case LimitAlertType.OVERDUE:
        return "Data Cleanup Overdue"
      case LimitAlertType.STORAGE:
        return "Storage Limit Exceeded"
      case LimitAlertType.GRACE:
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

  // Clamp days to 0 minimum for display and calculations
  const safeDaysRemaining = Math.max(0, alert.days_remaining ?? 0)
  const progressValue =
    safeDaysRemaining > 0
      ? Math.max(
          SubscriptionPeriods.MIN_PROGRESS_PERCENT,
          Math.min(
            SubscriptionPeriods.MAX_PROGRESS_PERCENT,
            (safeDaysRemaining / SubscriptionPeriods.PROGRESS_REFERENCE_DAYS) *
              SubscriptionPeriods.MAX_PROGRESS_PERCENT,
          ),
        )
      : 0

  const alertContent = (
    <Box>
      <Alert
        severity={getSeverity(alert.alert_type)}
        action={
          // Hide close button for OVERDUE alerts - they require acknowledgment
          !showAsModal &&
          !isOverdueAlert && (
            <IconButton size="small" onClick={() => handleDismiss()}>
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

        {/* OVERDUE warning - emphasize consequences */}
        {isOverdueAlert && (
          <Box
            sx={{
              bgcolor: "error.light",
              color: "error.contrastText",
              borderRadius: 1,
              p: 2,
              mb: 2,
              display: "flex",
              alignItems: "flex-start",
              gap: 1,
            }}
          >
            <WarningIcon sx={{ mt: 0.5 }} />
            <Box>
              <Typography variant="subtitle2" fontWeight="bold">
                Action Required
              </Typography>
              <Typography variant="body2">
                Your data will be permanently deleted if you do not upgrade or
                reduce your storage below the free plan limit. This action
                cannot be undone.
              </Typography>
            </Box>
          </Box>
        )}

        {/* Days remaining progress bar - show even at 0 to indicate urgency */}
        {alert.days_remaining !== undefined && (
          <Box sx={{ mb: 2 }}>
            <Box display="flex" justifyContent="space-between" mb={1}>
              <Typography variant="caption" fontWeight="bold">
                Time Remaining
              </Typography>
              <Typography variant="caption" fontWeight="bold">
                {formatTimeRemaining(safeDaysRemaining, alert.deletion_date)}
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

        {/* Acknowledgment checkbox for inline OVERDUE alerts */}
        {!showAsModal && isOverdueAlert && (
          <FormControlLabel
            control={
              <Checkbox
                checked={acknowledgedOverdue}
                onChange={(e) => setAcknowledgedOverdue(e.target.checked)}
                color="error"
                size="small"
              />
            }
            label={
              <Typography variant="caption" color="error.main">
                I understand my data will be deleted if I don&apos;t take action
              </Typography>
            }
            sx={{ mb: 1 }}
          />
        )}

        {/* Action buttons - only show when not in modal mode */}
        {!showAsModal && (
          <Box display="flex" gap={1} flexWrap="wrap">
            {showUpgradeButton && (
              <Button
                variant="contained"
                color={isOverdueAlert ? "error" : "primary"}
                startIcon={<UpgradeIcon />}
                onClick={handleUpgrade}
                size="small"
              >
                Upgrade to Premium
              </Button>
            )}
            {showManageFilesButton && (
              <Button
                variant="contained"
                color="primary"
                onClick={() => {
                  handleDismiss(true)
                  navigate("/workspaces")
                }}
                size="small"
              >
                Manage Files
              </Button>
            )}
            {/* Dismiss button for OVERDUE - only enabled after acknowledgment */}
            {isOverdueAlert && (
              <Button
                variant="outlined"
                color="inherit"
                onClick={() => handleDismiss()}
                disabled={!acknowledgedOverdue}
                size="small"
              >
                Dismiss
              </Button>
            )}
          </Box>
        )}
      </Alert>
    </Box>
  )

  if (showAsModal) {
    return (
      <Dialog
        open={Boolean(alert?.has_alert && !dismissed)}
        onClose={isOverdueAlert ? undefined : () => handleDismiss()}
        maxWidth="md"
        fullWidth
        disableEscapeKeyDown={isOverdueAlert}
      >
        {isOverdueAlert && (
          <DialogTitle
            sx={{
              bgcolor: "error.main",
              color: "error.contrastText",
              display: "flex",
              alignItems: "center",
              gap: 1,
            }}
          >
            <WarningIcon />
            Urgent: Data Deletion Imminent
          </DialogTitle>
        )}
        <DialogContent sx={{ pt: isOverdueAlert ? 3 : undefined }}>
          {alertContent}
          {/* Acknowledgment checkbox for OVERDUE alerts */}
          {isOverdueAlert && (
            <FormControlLabel
              control={
                <Checkbox
                  checked={acknowledgedOverdue}
                  onChange={(e) => setAcknowledgedOverdue(e.target.checked)}
                  color="error"
                />
              }
              label={
                <Typography
                  variant="body2"
                  color="error.main"
                  fontWeight="bold"
                >
                  I understand that my data will be permanently deleted if I do
                  not take action
                </Typography>
              }
              sx={{ mt: 2 }}
            />
          )}
        </DialogContent>
        <DialogActions>
          {isOverdueAlert ? (
            <>
              <Button
                onClick={() => handleDismiss()}
                color="inherit"
                disabled={!acknowledgedOverdue}
              >
                I understand, remind me later
              </Button>
              {showManageFilesButton && (
                <Button
                  variant="contained"
                  color="primary"
                  onClick={() => {
                    handleDismiss(true)
                    navigate("/workspaces")
                  }}
                >
                  Manage Files Now
                </Button>
              )}
              {showUpgradeButton && (
                <Button
                  variant="contained"
                  color="error"
                  onClick={() => {
                    handleDismiss(true)
                    navigate("/subscription")
                  }}
                  startIcon={<UpgradeIcon />}
                >
                  Upgrade Now
                </Button>
              )}
            </>
          ) : (
            <>
              <Button onClick={() => handleDismiss()} color="inherit">
                Handle later
              </Button>
              {showManageFilesButton && (
                <Button
                  variant="contained"
                  color="primary"
                  onClick={() => {
                    handleDismiss()
                    navigate("/workspaces")
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
                    navigate("/subscription")
                  }}
                  startIcon={<UpgradeIcon />}
                >
                  Upgrade
                </Button>
              )}
            </>
          )}
        </DialogActions>
      </Dialog>
    )
  }

  return alertContent
}

export default LimitAlert

import React, { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"

import { useSnackbar } from "notistack"

import {
  Close as CloseIcon,
  ErrorOutline as ErrorIcon,
  Upgrade as UpgradeIcon,
  Warning as WarningIcon,
} from "@mui/icons-material"
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  LinearProgress,
  Typography,
} from "@mui/material"

import {
  getMyDowngradeWarningApi,
  DowngradeWarning as DowngradeWarningType,
} from "api/storage/StorageAlerts"

interface DowngradeWarningProps {
  showAsModal?: boolean
  onClose?: () => void
  autoCheck?: boolean
}

const DowngradeWarning: React.FC<DowngradeWarningProps> = ({
  showAsModal = false,
  onClose,
  autoCheck = true,
}) => {
  const { enqueueSnackbar: _enqueueSnackbar } = useSnackbar()
  const navigate = useNavigate()
  const [warning, setWarning] = useState<DowngradeWarningType | null>(null)
  const [loading, setLoading] = useState(true)
  const [dismissed, setDismissed] = useState(false)

  const fetchDowngradeWarning = async () => {
    try {
      setLoading(true)
      const warningResponse = await getMyDowngradeWarningApi()
      setWarning(warningResponse)
    } catch (error) {
      // console.error("Failed to fetch downgrade warning:", error)
      // Silently fail to not disrupt the main UI
    } finally {
      setLoading(false)
    }
  }

  const handleDismiss = () => {
    setDismissed(true)
    onClose?.()
  }

  const handleUpgrade = () => {
    // Navigate to payment page
    navigate("/payment")
  }

  useEffect(() => {
    if (autoCheck) {
      fetchDowngradeWarning()
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

  const getSeverity = (warningType: string) => {
    switch (warningType) {
      case "overdue":
        return "error"
      case "immediate":
        return "error"
      case "downgrade":
        return "warning"
      default:
        return "warning"
    }
  }

  const getIcon = (warningType: string) => {
    switch (warningType) {
      case "overdue":
        return <ErrorIcon />
      case "immediate":
        return <ErrorIcon />
      case "downgrade":
        return <WarningIcon />
      default:
        return <WarningIcon />
    }
  }

  const getTitle = (warningType: string) => {
    switch (warningType) {
      case "overdue":
        return "Data Cleanup Overdue"
      case "immediate":
        return "Storage Limit Exceeded"
      case "downgrade":
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
        <AlertTitle>
          <Box display="flex" alignItems="center" gap={1}>
            {getIcon(warning.warning_type)}
            {getTitle(warning.warning_type)}
          </Box>
        </AlertTitle>

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
              {warning.current_usage_gb} GB
            </Typography>
          </Box>

          <Box display="flex" justifyContent="space-between" mb={1}>
            <Typography variant="body2" color="text.secondary">
              Free Tier Limit:
            </Typography>
            <Typography variant="body2">
              {warning.free_tier_limit_gb} GB
            </Typography>
          </Box>

          <Box display="flex" justifyContent="space-between">
            <Typography variant="body2" color="error.main" fontWeight="bold">
              Excess Data:
            </Typography>
            <Typography variant="body2" color="error.main" fontWeight="bold">
              {warning.excess_data_gb} GB
            </Typography>
          </Box>
        </Box>

        {/* Action buttons */}
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
            variant="outlined"
            color="secondary"
            onClick={() => (window.location.href = "/workspace")}
            size="small"
          >
            Manage Files
          </Button>
        </Box>
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
        <DialogTitle>
          <Box display="flex" alignItems="center" gap={1}>
            {getIcon(warning.warning_type)}
            {getTitle(warning.warning_type)}
          </Box>
        </DialogTitle>
        <DialogContent>{warningContent}</DialogContent>
        <DialogActions>
          <Button onClick={handleDismiss} color="inherit">
            Handle later
          </Button>
          <Button
            variant="contained"
            onClick={handleUpgrade}
            startIcon={<UpgradeIcon />}
          >
            Upgrade now
          </Button>
        </DialogActions>
      </Dialog>
    )
  }

  return warningContent
}

export default DowngradeWarning

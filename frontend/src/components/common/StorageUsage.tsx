import React, { useEffect, useState } from "react"

import { Warning as WarningIcon } from "@mui/icons-material"
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  LinearProgress,
  Typography,
} from "@mui/material"

import {
  getMyStorageUsageApi,
  StorageUsage as StorageUsageType,
} from "api/storage/StorageAlerts"
import { SubscriptionAlertThresholds } from "const/Subscription"

const StorageUsage: React.FC = () => {
  const [usage, setUsage] = useState<StorageUsageType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchStorageUsage = async () => {
    try {
      setLoading(true)
      setError(null)
      const result = await getMyStorageUsageApi()
      setUsage(result)
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("Failed to fetch storage usage:", err)
      setError("Unable to load storage information")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStorageUsage()
  }, [])

  if (loading) {
    return (
      <Box display="flex" alignItems="center" gap={1} p={1}>
        <CircularProgress size={16} />
        <Typography variant="caption">Checking storage...</Typography>
      </Box>
    )
  }

  if (error) {
    return (
      <Alert
        severity="warning"
        sx={{ mb: 2 }}
        action={
          <Button size="small" onClick={fetchStorageUsage}>
            Retry
          </Button>
        }
      >
        {error}
      </Alert>
    )
  }

  if (!usage) {
    return null
  }

  const getProgressColor = (percentage: number) => {
    if (percentage >= SubscriptionAlertThresholds.CRITICAL) return "error"
    if (percentage >= SubscriptionAlertThresholds.WARNING) return "warning"
    return "primary"
  }

  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
        mt: 2,
      }}
    >
      <Box
        display="flex"
        alignItems="center"
        justifyContent="space-between"
        mb={2}
      >
        <Typography variant="body1" color="text.secondary">
          Storage Usage
        </Typography>
      </Box>

      {usage.storage_quota_bytes ? (
        <>
          <Box display="flex" alignItems="center" gap={2} mb={2}>
            <LinearProgress
              variant="determinate"
              value={Math.min(usage.storage_usage_percent || 0, 100)}
              color={getProgressColor(usage.storage_usage_percent || 0)}
              sx={{ flexGrow: 1, height: 12, borderRadius: 6 }}
            />
            <Typography variant="body2" fontWeight="bold" minWidth="60px">
              {usage.storage_usage_percent?.toFixed(1) || "0.0"}%
            </Typography>
          </Box>

          <Box display="flex" justifyContent="space-between" mb={1}>
            <Typography variant="body2" color="text.secondary">
              Used: {usage.storage_usage_formatted}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Total: {usage.storage_quota_formatted}
            </Typography>
          </Box>

          {usage.alert_level && (
            <Box display="flex" alignItems="center" gap={1} mt={2}>
              <WarningIcon
                color={usage.alert_level === "danger" ? "error" : "warning"}
                fontSize="small"
              />
              <Typography
                variant="caption"
                color={usage.alert_level === "danger" ? "error" : "warning"}
              >
                Storage usage is{" "}
                {usage.alert_level === "danger" ? "over quota" : "high"}
              </Typography>
            </Box>
          )}
        </>
      ) : (
        <Typography variant="body2" color="text.secondary">
          {usage.storage_usage_formatted} used (no quota limit set)
        </Typography>
      )}
    </Box>
  )
}

export default StorageUsage

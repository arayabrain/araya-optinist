import { useEffect, useState, useCallback } from "react"

import { useSnackbar } from "notistack"

import {
  getMyDowngradeWarningApi,
  checkDowngradeWarningStatusApi,
  DowngradeWarning,
  DowngradeWarningStatus,
} from "api/storage/StorageAlerts"

interface UseDowngradeWarningReturn {
  warning: DowngradeWarning | null
  hasWarning: boolean
  loading: boolean
  checkDowngradeWarning: () => Promise<boolean>
  dismissWarning: () => void
}

interface UseDowngradeWarningOptions {
  autoCheck?: boolean
  checkInterval?: number // in milliseconds
  showSnackbar?: boolean
  showModalOnLogin?: boolean
}

export const useDowngradeWarning = (
  options: UseDowngradeWarningOptions = {},
): UseDowngradeWarningReturn => {
  const {
    autoCheck = true,
    checkInterval = 10 * 60 * 1000, // 10 minutes default (less frequent than storage alerts)
    showSnackbar = false,
    showModalOnLogin: _showModalOnLogin = false,
  } = options

  const { enqueueSnackbar } = useSnackbar()
  const [warning, setWarning] = useState<DowngradeWarning | null>(null)
  const [loading, setLoading] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  const checkDowngradeWarning = useCallback(async (): Promise<boolean> => {
    try {
      setLoading(true)
      const warningResponse = await getMyDowngradeWarningApi()

      if (warningResponse && warningResponse.has_warning) {
        const newWarning = warningResponse

        // Show snackbar notification if this is a new warning or severity changed
        if (
          showSnackbar &&
          (!warning || newWarning.warning_type !== warning.warning_type)
        ) {
          const severity =
            newWarning.warning_type === "overdue" ? "error" : "warning"
          const autoHideDuration =
            newWarning.warning_type === "overdue" ? 15000 : 10000

          enqueueSnackbar(
            `Account Warning: ${newWarning.days_remaining} days remaining to resolve storage issue`,
            {
              variant: severity,
              autoHideDuration,
            },
          )
        }

        setWarning(newWarning)
        return true
      } else {
        setWarning(null)
        return false
      }
    } catch (error) {
      // console.error("Failed to check downgrade warning:", error)
      // Silently fail to not disrupt user experience
      setWarning(null)
      return false
    } finally {
      setLoading(false)
    }
  }, [warning, showSnackbar, enqueueSnackbar])

  const dismissWarning = useCallback(() => {
    setDismissed(true)
  }, [])

  useEffect(() => {
    if (autoCheck && !dismissed) {
      // Initial check
      checkDowngradeWarning()

      // Set up interval for periodic checks
      const interval = setInterval(checkDowngradeWarning, checkInterval)

      return () => clearInterval(interval)
    }
    return () => {}
  }, [autoCheck, checkInterval, checkDowngradeWarning, dismissed])

  return {
    warning: dismissed ? null : warning,
    hasWarning: Boolean(warning?.has_warning && !dismissed),
    loading,
    checkDowngradeWarning,
    dismissWarning,
  }
}

/**
 * Quick hook to check if user has any downgrade warnings without full details
 */
export const useDowngradeWarningStatus = () => {
  const [status, setStatus] = useState<DowngradeWarningStatus | null>(null)
  const [loading, setLoading] = useState(false)

  const checkStatus = useCallback(async () => {
    try {
      setLoading(true)
      const statusResponse = await checkDowngradeWarningStatusApi()
      setStatus(statusResponse)
    } catch (error) {
      // console.error("Failed to check downgrade warning status:", error)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    checkStatus()
  }, [checkStatus])

  return {
    status,
    hasWarning: Boolean(status?.has_warning),
    loading,
    checkStatus,
  }
}

import { useEffect, useState, useCallback } from "react"

import { useSnackbar } from "notistack"

import {
  getMyLimitWarningApi,
  checkLimitWarningStatusApi,
  LimitWarning,
  LimitWarningStatus,
} from "api/storage/StorageAlerts"

interface UseLimitWarningReturn {
  warning: LimitWarning | null
  hasWarning: boolean
  loading: boolean
  checkLimitWarning: () => Promise<boolean>
  dismissWarning: () => void
}

interface UseLimitWarningOptions {
  autoCheck?: boolean
  checkInterval?: number // in milliseconds
  showSnackbar?: boolean
  showModalOnLogin?: boolean
}

export const useLimitWarning = (
  options: UseLimitWarningOptions = {},
): UseLimitWarningReturn => {
  const {
    autoCheck = true,
    checkInterval = 10 * 60 * 1000, // 10 minutes default (less frequent than storage alerts)
    showSnackbar = false,
    showModalOnLogin: _showModalOnLogin = false,
  } = options

  const { enqueueSnackbar } = useSnackbar()
  const [warning, setWarning] = useState<LimitWarning | null>(null)
  const [loading, setLoading] = useState(false)
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

  const checkLimitWarning = useCallback(async (): Promise<boolean> => {
    try {
      setLoading(true)
      const warningResponse = await getMyLimitWarningApi()

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
      // console.error("Failed to check limit warning:", error)
      // Silently fail to not disrupt user experience
      setWarning(null)
      return false
    } finally {
      setLoading(false)
    }
  }, [warning, showSnackbar, enqueueSnackbar])

  const dismissWarning = useCallback(() => {
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
  }, [])

  useEffect(() => {
    if (autoCheck && !dismissed) {
      // Initial check
      checkLimitWarning()

      // Set up interval for periodic checks
      const interval = setInterval(checkLimitWarning, checkInterval)

      return () => clearInterval(interval)
    }
    return () => {}
  }, [autoCheck, checkInterval, checkLimitWarning, dismissed])

  return {
    warning: dismissed ? null : warning,
    hasWarning: Boolean(warning?.has_warning && !dismissed),
    loading,
    checkLimitWarning,
    dismissWarning,
  }
}

/**
 * Quick hook to check if user has any limit warnings without full details
 */
export const useLimitWarningStatus = () => {
  const [status, setStatus] = useState<LimitWarningStatus | null>(null)
  const [loading, setLoading] = useState(false)

  const checkStatus = useCallback(async () => {
    try {
      setLoading(true)
      const statusResponse = await checkLimitWarningStatusApi()
      setStatus(statusResponse)
    } catch (error) {
      // console.error("Failed to check limit warning status:", error)
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

import { useCallback, useEffect, useState } from "react"

export interface SyncStatus {
  pending: boolean
  error: boolean
  message: string
}

const INITIAL_SYNC_STATUS: SyncStatus = {
  pending: false,
  error: false,
  message: "",
}

const RETRY_MAX_COUNT = 30
const RETRY_INTERVAL = 10000

interface UseSyncRetryParams {
  is_public: boolean
  fetchFn: () => Promise<unknown>
  shouldFetch: boolean
}

export const useSyncRetry = ({
  is_public,
  fetchFn,
  shouldFetch,
}: UseSyncRetryParams) => {
  const [loading, setLoading] = useState(false)
  const [syncStatus, setSyncStatus] = useState<SyncStatus>(INITIAL_SYNC_STATUS)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    if (!shouldFetch) return

    setLoading(true)
    setSyncStatus(INITIAL_SYNC_STATUS)

    fetchFn()
      .then(() => {
        setLoading(false)
      })
      .catch((error) => {
        setLoading(false)

        if (is_public && error?.response) {
          const status = error.response.status
          const data = error.response.data

          switch (status) {
            case 202:
            case 423:
              // Experiment is published but not yet synced
              setSyncStatus({
                pending: true,
                error: false,
                message:
                  data?.message ||
                  "Loading in progress, check back in a few minutes.",
              })
              // Auto-retry
              if (retryCount < RETRY_MAX_COUNT) {
                setTimeout(() => {
                  setRetryCount(retryCount + 1)
                }, RETRY_INTERVAL)
              } else {
                // Timeout: stop retrying and show error
                setSyncStatus({
                  pending: false,
                  error: true,
                  message:
                    data?.message ||
                    "Loading is taking longer than expected. Please try again later.",
                })
              }
              break
            case 503:
              // Sync failed or download error
              setSyncStatus({
                pending: false,
                error: true,
                message:
                  data?.message ||
                  "Experiment temporarily unavailable, please try again later.",
              })
              break
            default:
              // Handle other HTTP errors (500, 404, etc.)
              setSyncStatus({
                pending: false,
                error: true,
                message:
                  data?.message ||
                  `Failed to load experiment (${status}). Please try again.`,
              })
              break
          }
        } else {
          // Network error - no response (blocked, offline, timeout, etc.)
          setSyncStatus({
            pending: false,
            error: true,
            message:
              "Failed to load experiment. Please check your connection and try again.",
          })
        }
      })
  }, [shouldFetch, is_public, fetchFn, retryCount])

  const handleRetry = useCallback(() => {
    setSyncStatus(INITIAL_SYNC_STATUS)
    setRetryCount((prev) => prev + 1)
  }, [])

  return { loading, syncStatus, handleRetry }
}

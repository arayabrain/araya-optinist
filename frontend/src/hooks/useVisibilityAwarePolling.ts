import { useCallback, useEffect, useRef, useState } from "react"

interface UseVisibilityAwarePollingOptions {
  /** Polling interval in milliseconds */
  interval: number
  /** Whether polling is enabled */
  enabled?: boolean
  /** Whether to run poll function immediately on mount */
  immediate?: boolean
  /** Whether to run poll function when tab becomes visible */
  runOnVisible?: boolean
  /** Minimum time between visibility-triggered polls (ms) */
  visibilityDebounce?: number
}

interface UseVisibilityAwarePollingReturn {
  /** Whether the tab is currently visible */
  isVisible: boolean
  /** Manually trigger the poll function */
  pollNow: () => void
  /** Stop polling until resumed */
  pause: () => void
  /** Resume polling after pause */
  resume: () => void
  /** Whether polling is currently paused */
  isPaused: boolean
}

const DEFAULT_VISIBILITY_DEBOUNCE_MS = 5000

export const useVisibilityAwarePolling = (
  pollFn: () => Promise<void> | void,
  options: UseVisibilityAwarePollingOptions,
): UseVisibilityAwarePollingReturn => {
  const {
    interval,
    enabled = true,
    immediate = false,
    runOnVisible = true,
    visibilityDebounce = DEFAULT_VISIBILITY_DEBOUNCE_MS,
  } = options

  const [isVisible, setIsVisible] = useState(() => !document.hidden)
  const [isPaused, setIsPaused] = useState(false)
  const lastPollRef = useRef<number>(0)

  const pollNow = useCallback(async () => {
    lastPollRef.current = Date.now()
    try {
      await pollFn()
    } catch (error) {
      console.error("Polling error:", error)
    }
  }, [pollFn])

  const pause = useCallback(() => setIsPaused(true), [])
  const resume = useCallback(() => setIsPaused(false), [])

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsVisible(!document.hidden)
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [])

  useEffect(() => {
    if (!runOnVisible || !enabled || isPaused || !isVisible) return

    const timeSinceLastPoll = Date.now() - lastPollRef.current
    if (timeSinceLastPoll >= visibilityDebounce) {
      pollNow()
    }
  }, [isVisible, runOnVisible, enabled, isPaused, visibilityDebounce, pollNow])

  useEffect(() => {
    if (!enabled || isPaused) return

    if (immediate && lastPollRef.current === 0) {
      pollNow()
    }

    if (!isVisible) return

    const timer = setInterval(pollNow, interval)
    return () => clearInterval(timer)
  }, [enabled, isPaused, isVisible, interval, immediate, pollNow])

  return {
    isVisible,
    pollNow,
    pause,
    resume,
    isPaused,
  }
}

export default useVisibilityAwarePolling

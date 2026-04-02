/**
 * Sleep Detection Hook
 *
 * Detects when a device wakes from sleep by monitoring interval timing gaps.
 * When the interval fires much later than expected, we assume the device slept.
 *
 * Two detection paths: (1) interval fires late while tab is visible,
 * (2) on hidden→visible, no ticks fired while hidden (real sleep suspends
 * the CPU; background throttling still fires ticks at ~60s).
 */

import { useEffect, useRef, useCallback } from "react"

const DEFAULT_CHECK_INTERVAL_MS = 30000
const SLEEP_DETECTION_MULTIPLIER = 5

interface UseSleepDetectionOptions {
  checkIntervalMs?: number
  sleepThresholdMultiplier?: number
  enabled?: boolean
}

/**
 * Hook that detects when the device wakes from sleep.
 *
 * @param onWake - Callback fired when device wakes from sleep
 * @param options - Configuration options
 */
export const useSleepDetection = (
  onWake: () => void,
  options: UseSleepDetectionOptions = {},
): void => {
  const {
    checkIntervalMs = DEFAULT_CHECK_INTERVAL_MS,
    sleepThresholdMultiplier = SLEEP_DETECTION_MULTIPLIER,
    enabled = true,
  } = options

  const lastTickRef = useRef(Date.now())
  const onWakeRef = useRef(onWake)
  // Tracks whether any interval ticks fired while tab was hidden.
  // If none fired, the CPU was suspended (real sleep, not throttling).
  const hiddenTickRef = useRef(false)
  // Timestamp when the tab went hidden, for gap measurement.
  const hiddenAtRef = useRef<number | null>(null)

  useEffect(() => {
    onWakeRef.current = onWake
  }, [onWake])

  const checkForSleep = useCallback(() => {
    const now = Date.now()
    const elapsed = now - lastTickRef.current
    const expectedInterval = checkIntervalMs * sleepThresholdMultiplier

    if (document.visibilityState === "visible") {
      if (elapsed > expectedInterval) {
        onWakeRef.current()
      }
    } else {
      // Tab is hidden — record that a tick fired (CPU is running, not sleep)
      hiddenTickRef.current = true
    }

    lastTickRef.current = now
  }, [checkIntervalMs, sleepThresholdMultiplier])

  useEffect(() => {
    if (!enabled) return

    lastTickRef.current = Date.now()
    const interval = setInterval(checkForSleep, checkIntervalMs)

    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        hiddenAtRef.current = Date.now()
        hiddenTickRef.current = false
      } else if (document.visibilityState === "visible") {
        // If no interval ticks fired while hidden, the CPU was suspended
        // (real sleep). Check whether the gap exceeds the threshold.
        if (!hiddenTickRef.current && hiddenAtRef.current !== null) {
          const now = Date.now()
          const elapsed = now - hiddenAtRef.current
          const threshold = checkIntervalMs * sleepThresholdMultiplier

          if (elapsed > threshold) {
            onWakeRef.current()
          }
        }
        lastTickRef.current = Date.now()
        hiddenAtRef.current = null
        hiddenTickRef.current = false
      }
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)

    return () => {
      clearInterval(interval)
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [enabled, checkIntervalMs, checkForSleep, sleepThresholdMultiplier])
}

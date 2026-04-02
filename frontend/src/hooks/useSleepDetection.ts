/**
 * Sleep Detection Hook
 *
 * Detects when a device wakes from sleep by monitoring interval timing gaps.
 * When the interval fires much later than expected, we assume the device slept.
 *
 * The 150s threshold avoids false positives from Chromium's background-tab
 * timer throttling (~60s). We also require tab visibility so throttled
 * background tabs don't trigger a wake event.
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

  useEffect(() => {
    onWakeRef.current = onWake
  }, [onWake])

  const checkForSleep = useCallback(() => {
    const now = Date.now()
    const elapsed = now - lastTickRef.current
    const expectedInterval = checkIntervalMs * sleepThresholdMultiplier

    if (elapsed > expectedInterval && document.visibilityState === "visible") {
      onWakeRef.current()
    }

    lastTickRef.current = now
  }, [checkIntervalMs, sleepThresholdMultiplier])

  useEffect(() => {
    if (!enabled) return

    lastTickRef.current = Date.now()
    const interval = setInterval(checkForSleep, checkIntervalMs)

    return () => clearInterval(interval)
  }, [enabled, checkIntervalMs, checkForSleep])
}

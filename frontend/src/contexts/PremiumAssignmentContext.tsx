/**
 * Premium Assignment Context Provider
 *
 * Single source of truth for premium assignment logic across the entire app.
 * Eliminates the issue of multiple hook instances causing duplicate API calls.
 */

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useRef,
} from "react"
import { flushSync } from "react-dom"
import { useSelector } from "react-redux"

import {
  assignPremiumInstance,
  getBeaconTokenApi,
  getPremiumStatus,
  getRoutingInfo,
  releasePremiumInstance,
  sendPremiumHeartbeat,
  PremiumAssignmentResult,
  PremiumStatusResult,
  RoutingInfo,
} from "api/premium/PremiumAssignmentApi"
import { BASE_URL } from "const/API"
import { PlanName, SubscriptionStatus } from "const/Subscription"
import { shouldPoll } from "contexts/premium/unreachableMachine"
import {
  InstanceUnreachableHandle,
  useInstanceUnreachableMachine,
} from "contexts/premium/useInstanceUnreachableMachine"
import { useSleepDetection } from "hooks/useSleepDetection"
import { selectLogoutGeneration } from "store/slice/User/UserSelector"
import { RootState } from "store/store"
import {
  CrossTabLeaderElection,
  syncActivityAcrossTabs,
  getLastActivityFromAnyTab,
  onActivityFromOtherTab,
  tabSync,
} from "utils/crossTabSync"
import { routingService } from "utils/routing/RoutingService"

// Absolute URL for sendBeacon (must target the API server directly).
const BEACON_RELEASE_URL = `${BASE_URL}/users/me/premium/release-beacon`
const BEACON_CONTENT_TYPE = "text/plain"

// Polling configuration constants
// Backend rate limit is 30s, so initial interval must be >= 30s
const INITIAL_POLL_INTERVAL_MS = 30000
const MAX_POLL_INTERVAL_MS = 60000
const MAX_POLL_ATTEMPTS = 40
const BACKOFF_MULTIPLIER = 1.5
const ERROR_BACKOFF_MULTIPLIER = 2

// sessionStorage keys — per-tab persistence across page refreshes.
// Clears automatically when the tab closes.
const SS_HAS_ATTEMPTED = "premium_hasAttempted"
const SS_POLL_ATTEMPTS = "premium_pollAttempts"

function ssRead(key: string): string | null {
  try {
    return sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function ssWrite(key: string, value: string): void {
  try {
    sessionStorage.setItem(key, value)
  } catch {
    // sessionStorage unavailable (e.g., some private browsing modes)
  }
}

function ssRemove(key: string): void {
  try {
    sessionStorage.removeItem(key)
  } catch {
    // sessionStorage unavailable
  }
}

// Heartbeat retry configuration (Case 49)
const HEARTBEAT_MAX_RETRIES = 3
const HEARTBEAT_RETRY_DELAY_MS = 1000

interface PremiumAssignmentState {
  isAssigning: boolean
  isReleasing: boolean
  assignmentResult: PremiumAssignmentResult | null
  statusResult: PremiumStatusResult | null
  routingInfo: RoutingInfo | null
  error: string | null
  isRetryableError: boolean
  isPremiumUser: boolean
  showInactivityWarning: boolean
  lastActivityTime: number
  // Orthogonal to instanceUnreachable — same 503 can set both; they are not redundant.
  heartbeatFailing: boolean
}

interface PremiumAssignmentContextType extends PremiumAssignmentState {
  assign: () => Promise<PremiumAssignmentResult | null>
  release: () => Promise<unknown>
  getStatus: () => Promise<PremiumStatusResult | null>
  updateRoutingInfo: () => Promise<RoutingInfo | null>
  autoReleaseOnLogout: () => void
  dismissInactivityWarning: () => void
  recordActivity: () => Promise<void>
  unreachable: InstanceUnreachableHandle
}

const PremiumAssignmentContext =
  createContext<PremiumAssignmentContextType | null>(null)

export const usePremiumAssignment = () => {
  const context = useContext(PremiumAssignmentContext)
  if (!context) {
    throw new Error(
      "usePremiumAssignment must be used within PremiumAssignmentProvider",
    )
  }
  return context
}

export const PremiumAssignmentProvider: React.FC<{
  children: React.ReactNode
}> = ({ children }) => {
  const currentUser = useSelector((state: RootState) => state.user.currentUser)
  // Track logout generation to detect stale closures
  const logoutGeneration = useSelector(selectLogoutGeneration)

  const [state, setState] = useState<PremiumAssignmentState>({
    isAssigning: false,
    isReleasing: false,
    assignmentResult: null,
    statusResult: null,
    routingInfo: null,
    error: null,
    isRetryableError: false,
    isPremiumUser: false,
    showInactivityWarning: false,
    lastActivityTime: Date.now(),
    heartbeatFailing: false,
  })

  // Ref guard to prevent multiple auto-assignment attempts per mount.
  // useRef (not useState) so the flag is set synchronously and survives
  // StrictMode double-invocations without triggering extra renders.
  const hasAttemptedRef = useRef(ssRead(SS_HAS_ATTEMPTED) === "true")

  // Polling state with backoff
  const [pollInterval, setPollInterval] = useState(INITIAL_POLL_INTERVAL_MS)
  const [pollAttempts, setPollAttempts] = useState(() => {
    const stored = ssRead(SS_POLL_ATTEMPTS)
    const n = Number(stored)
    return Number.isNaN(n) ? 0 : n
  })

  // Sync pollAttempts to sessionStorage so the cap survives page refreshes
  useEffect(() => {
    if (pollAttempts > 0) {
      ssWrite(SS_POLL_ATTEMPTS, String(pollAttempts))
    } else if (ssRead(SS_POLL_ATTEMPTS) !== null) {
      ssRemove(SS_POLL_ATTEMPTS)
    }
  }, [pollAttempts])

  // Cross-tab leader election for coordinating polling
  const [isTabLeader, setIsTabLeader] = useState(false)
  const leaderElectionRef = useRef<CrossTabLeaderElection | null>(null)
  const beaconTokenRef = useRef<string | null>(null)

  // Refs for values that inactivity check needs but shouldn't trigger re-renders
  const lastActivityTimeRef = useRef(state.lastActivityTime)
  const showInactivityWarningRef = useRef(state.showInactivityWarning)

  const unreachable = useInstanceUnreachableMachine({
    assignment: state.assignmentResult,
    isTabLeader,
  })

  // Calculate premium user status
  const isPremiumUser =
    currentUser?.subscription_plan_name === PlanName.PREMIUM &&
    (currentUser?.subscription_status === SubscriptionStatus.PREMIUM ||
      currentUser?.subscription_status === SubscriptionStatus.LIMIT_GRACE)

  // Update state when premium status changes
  useEffect(() => {
    setState((prev) => ({ ...prev, isPremiumUser }))
  }, [isPremiumUser])

  // Reset flag when user changes
  useEffect(() => {
    hasAttemptedRef.current = false
    ssRemove(SS_HAS_ATTEMPTED)
    ssRemove(SS_POLL_ATTEMPTS)
  }, [currentUser?.id])

  // Reset state when logout generation changes to prevent stale closures
  useEffect(() => {
    if (logoutGeneration > 0) {
      // Clear any cached assignment state on logout
      setState({
        isAssigning: false,
        isReleasing: false,
        assignmentResult: null,
        statusResult: null,
        routingInfo: null,
        error: null,
        isRetryableError: false,
        isPremiumUser: false,
        showInactivityWarning: false,
        lastActivityTime: Date.now(),
        heartbeatFailing: false,
      })
      unreachable.reset()
      hasAttemptedRef.current = false
      ssRemove(SS_HAS_ATTEMPTED)
      ssRemove(SS_POLL_ATTEMPTS)
    }
    // Only run on logoutGeneration change, not initial mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logoutGeneration])

  // Sync refs with state to avoid including in inactivity effect dependencies
  useEffect(() => {
    lastActivityTimeRef.current = state.lastActivityTime
  }, [state.lastActivityTime])

  useEffect(() => {
    showInactivityWarningRef.current = state.showInactivityWarning
  }, [state.showInactivityWarning])

  // Initialize cross-tab leader election for premium users
  useEffect(() => {
    if (!isPremiumUser) {
      // Clean up leader election if user is not premium
      if (leaderElectionRef.current) {
        leaderElectionRef.current.destroy()
        leaderElectionRef.current = null
        setIsTabLeader(false)
      }
      return
    }

    // Create leader election instance
    leaderElectionRef.current = new CrossTabLeaderElection(
      () => setIsTabLeader(true),
      () => setIsTabLeader(false),
    )

    // Check initial leadership state
    setIsTabLeader(leaderElectionRef.current.getIsLeader())

    return () => {
      if (leaderElectionRef.current) {
        leaderElectionRef.current.destroy()
        leaderElectionRef.current = null
      }
    }
  }, [isPremiumUser])

  /**
   * Dismiss inactivity warning
   */
  const dismissInactivityWarning = useCallback(() => {
    setState((prev) => ({ ...prev, showInactivityWarning: false }))
  }, [])

  /**
   * Sleep utility for retry delays
   */
  const sleep = useCallback(
    (ms: number) => new Promise((resolve) => setTimeout(resolve, ms)),
    [],
  )

  /**
   * Record user activity by sending heartbeat with retry logic (Case 49)
   */
  const recordActivity = useCallback(async (): Promise<void> => {
    if (!isPremiumUser) return

    for (let attempt = 0; attempt < HEARTBEAT_MAX_RETRIES; attempt++) {
      try {
        await sendPremiumHeartbeat()
        const now = Date.now()
        setState((prev) => ({
          ...prev,
          lastActivityTime: now,
          showInactivityWarning: false,
          heartbeatFailing: false,
        }))
        syncActivityAcrossTabs(now)
        return
      } catch (error) {
        const isLastAttempt = attempt === HEARTBEAT_MAX_RETRIES - 1
        if (isLastAttempt) {
          // eslint-disable-next-line no-console
          console.error("Heartbeat failed after retries:", error)
          const now = Date.now()
          setState((prev) => ({
            ...prev,
            lastActivityTime: now,
            heartbeatFailing: true,
          }))
          syncActivityAcrossTabs(now)
          throw error
        }
        // eslint-disable-next-line no-console
        console.warn(`Heartbeat attempt ${attempt + 1} failed, retrying...`)
        await sleep(HEARTBEAT_RETRY_DELAY_MS * (attempt + 1))
      }
    }
  }, [isPremiumUser, sleep])

  /**
   * Assign premium instance
   */
  const assign =
    useCallback(async (): Promise<PremiumAssignmentResult | null> => {
      if (!isPremiumUser) {
        const error = "Premium subscription required"
        setState((prev) => ({ ...prev, error }))
        return null
      }

      setState((prev) => ({
        ...prev,
        isAssigning: true,
        error: null,
        isRetryableError: false,
      }))

      try {
        const result = await assignPremiumInstance()
        const isRetryable =
          !result.assigned &&
          (result.scaling_in_progress || result.retry_after != null)

        setState((prev) => ({
          ...prev,
          isAssigning: false,
          assignmentResult: result,
          error: result.assigned ? null : result.message,
          isRetryableError: isRetryable,
        }))

        if (result.assigned) {
          routingService.setPremiumAssigned(true)
          try {
            const tokenRes = await getBeaconTokenApi()
            beaconTokenRef.current = tokenRes.data.token
          } catch {
            // Non-critical; beacon will fail gracefully
          }
        }

        return result
      } catch (error: unknown) {
        const errorMessage =
          error &&
          typeof error === "object" &&
          "response" in error &&
          error.response &&
          typeof error.response === "object" &&
          "data" in error.response &&
          error.response.data &&
          typeof error.response.data === "object" &&
          "detail" in error.response.data
            ? (error.response.data as { detail: string }).detail
            : error instanceof Error
              ? error.message
              : "Assignment failed"

        setState((prev) => ({
          ...prev,
          isAssigning: false,
          error: errorMessage,
          isRetryableError: false,
        }))
        return null
      }
    }, [isPremiumUser])

  /**
   * Release premium instance
   */
  const release = useCallback(async () => {
    setState((prev) => ({ ...prev, isReleasing: true, error: null }))

    try {
      const result = await releasePremiumInstance()
      // Clear beacon token so beforeunload doesn't fire a duplicate release
      beaconTokenRef.current = null
      setState((prev) => ({
        ...prev,
        isReleasing: false,
        assignmentResult: null,
        statusResult: null,
      }))
      // Defensive — covers refs outside the reducer that the hook's mirror effect doesn't touch.
      unreachable.reset()

      routingService.setPremiumAssigned(false)
      // Notify other tabs about premium release
      tabSync.broadcastPremiumReleased()

      return result
    } catch (error: unknown) {
      // eslint-disable-next-line no-console
      console.warn("Premium instance release warning:", error)
      setState((prev) => ({ ...prev, isReleasing: false }))
      return { released: true, message: "Release completed with warnings" }
    }
  }, [unreachable])

  /**
   * Get current status
   */
  const getStatus = useCallback(async () => {
    try {
      const status = await getPremiumStatus()
      setState((prev) => ({ ...prev, statusResult: status }))
      return status
    } catch (error: unknown) {
      // eslint-disable-next-line no-console
      console.warn("Failed to get premium status:", error)
      return null
    }
  }, [])

  /**
   * Update routing info
   */
  const updateRoutingInfo = useCallback(async () => {
    if (!currentUser) return null

    try {
      const routing = await getRoutingInfo()
      setState((prev) => ({ ...prev, routingInfo: routing }))

      // Update the routing service
      routingService.updateRoutingInfo(currentUser)

      return routing
    } catch (error: unknown) {
      // eslint-disable-next-line no-console
      console.warn("Failed to get routing info:", error)
      return null
    }
  }, [currentUser])

  /**
   * Auto-assign on premium user login (fully isolated to prevent loops)
   */
  const autoAssignOnLogin = useCallback(async () => {
    if (!isPremiumUser || hasAttemptedRef.current) return

    // Set flag immediately to prevent duplicate calls
    hasAttemptedRef.current = true

    try {
      // Check current status first (inline to avoid dependency issues)
      const statusResponse = await getPremiumStatus()
      if (statusResponse?.error) {
        // Lambda failed transiently — don't trigger assignment flow.
        // Not persisted to sessionStorage, so page refresh retries.
        return
      }
      if (statusResponse?.assignment) {
        // User already has an assignment - update state immediately so notifications trigger
        // Convert PremiumAssignment to PremiumAssignmentResult format
        const assignmentResult: PremiumAssignmentResult = {
          message: "Premium instance already assigned",
          instance_id: statusResponse.assignment.instance_id,
          assigned: true,
          is_shared: statusResponse.assignment.is_shared,
          assignment_source:
            statusResponse.assignment.assignment_source ?? "existing",
        }
        setState((prev) => ({
          ...prev,
          assignmentResult,
          error: null,
          isRetryableError: false,
        }))
        routingService.setPremiumAssigned(true)
        try {
          const tokenRes = await getBeaconTokenApi()
          beaconTokenRef.current = tokenRes.data.token
        } catch {
          // Non-critical; beacon will fail gracefully
        }
        ssWrite(SS_HAS_ATTEMPTED, "true") // duplicated in assign path below — both exits must persist
        return
      }

      flushSync(() => {
        setState((prev) => ({ ...prev, isAssigning: true }))
      })

      // Attempt assignment directly (inline to avoid dependency issues)
      const assignmentResponse = await assignPremiumInstance()
      if (assignmentResponse?.assigned) {
        // Update state to reflect the assignment
        setState((prev) => ({
          ...prev,
          isAssigning: false,
          assignmentResult: assignmentResponse,
          error: null,
        }))
        routingService.setPremiumAssigned(true)
        try {
          const tokenRes = await getBeaconTokenApi()
          beaconTokenRef.current = tokenRes.data.token
        } catch {
          // Non-critical; beacon will fail gracefully
        }
      } else {
        // Only store assignmentResult for transient errors (scaling/retry)
        // so the waiting popup stays visible and polling retries.
        // For non-retryable errors, leave assignmentResult null to prevent
        // contradictory polling + "Falling back to shared" notification.
        const isRetryable =
          assignmentResponse.scaling_in_progress ||
          assignmentResponse.retry_after != null
        setState((prev) => ({
          ...prev,
          isAssigning: false,
          assignmentResult: isRetryable ? assignmentResponse : null,
          error: assignmentResponse.message || null,
          isRetryableError: isRetryable,
        }))
      }
      // Persist only after a successful status/assign round-trip.
      // On network error (catch below), leave unpersisted so refresh retries.
      ssWrite(SS_HAS_ATTEMPTED, "true") // duplicated in already-assigned path above — both exits must persist
    } catch (error) {
      // hasAttemptedRef stays true to prevent rapid-fire retries on this mount.
      // sessionStorage is NOT written, so a page refresh will retry.
      // Set error state so the error notification fires
      const errorMessage =
        error instanceof Error ? error.message : "Assignment failed"
      // eslint-disable-next-line no-console
      console.warn("Auto-assignment failed:", error)
      setState((prev) => ({
        ...prev,
        isAssigning: false,
        error: errorMessage,
        isRetryableError: false,
      }))
      routingService.clearRoutingInfo()
    }
  }, [isPremiumUser]) // refs and setState are stable — no other deps needed

  /**
   * Auto-release on logout via sendBeacon.
   * Uses the HMAC-signed beacon token (no auth header needed),
   * so it's safe to call right before dispatch(logout) clears
   * the auth token from localStorage.
   */
  const autoReleaseOnLogout = useCallback((): void => {
    if (beaconTokenRef.current) {
      const blob = new Blob(
        [JSON.stringify({ token: beaconTokenRef.current })],
        { type: BEACON_CONTENT_TYPE },
      )
      navigator.sendBeacon(BEACON_RELEASE_URL, blob)
      beaconTokenRef.current = null
    }
    setState((prev) => ({
      ...prev,
      assignmentResult: null,
      statusResult: null,
    }))
    routingService.setPremiumAssigned(false)
  }, [])

  // Inactivity monitoring for premium users
  // Uses refs for lastActivityTime/showInactivityWarning to avoid interval churn
  useEffect(() => {
    if (!isPremiumUser || !currentUser || !state.assignmentResult) {
      return
    }

    let inactivityCheckInterval: ReturnType<typeof setInterval> | null = null

    const checkInactivity = () => {
      const now = Date.now()
      // Check activity from any tab, not just this one
      const lastActivityAnyTab = getLastActivityFromAnyTab()
      const effectiveLastActivity = Math.max(
        lastActivityTimeRef.current,
        lastActivityAnyTab,
      )
      const timeSinceLastActivity = now - effectiveLastActivity

      const oneHourMs = 60 * 60 * 1000 // 1 hour
      const twoHoursMs = 2 * 60 * 60 * 1000 // 2 hours
      // eslint-disable-next-line no-console
      console.log(
        `Inactivity check: ${Math.round(timeSinceLastActivity / 1000 / 60)}min ` +
          "since last activity (any tab)",
      )

      if (timeSinceLastActivity >= twoHoursMs) {
        // eslint-disable-next-line no-console
        console.warn(
          "2 hours of inactivity detected - auto-releasing premium instance",
        )
        setState((prev) => ({ ...prev, showInactivityWarning: false }))
        autoReleaseOnLogout()
      } else if (
        timeSinceLastActivity >= oneHourMs &&
        !showInactivityWarningRef.current
      ) {
        // eslint-disable-next-line no-console
        console.warn("1 hour of inactivity detected - showing warning")
        setState((prev) => ({ ...prev, showInactivityWarning: true }))
      }
    }

    // Check inactivity every 30 seconds
    inactivityCheckInterval = setInterval(checkInactivity, 30 * 1000)

    // Listen for activity from other tabs to dismiss warning
    const unsubscribe = onActivityFromOtherTab((timestamp) => {
      setState((prev) => ({
        ...prev,
        lastActivityTime: Math.max(prev.lastActivityTime, timestamp),
        showInactivityWarning: false,
      }))
    })

    return () => {
      if (inactivityCheckInterval) {
        clearInterval(inactivityCheckInterval)
      }
      unsubscribe()
    }
  }, [isPremiumUser, currentUser, state.assignmentResult, autoReleaseOnLogout])

  // Sleep/wake detection callback (Cases 50-51)
  const handleDeviceWake = useCallback(() => {
    if (!isPremiumUser || !state.assignmentResult) return
    recordActivity().catch((error) => {
      // eslint-disable-next-line no-console
      console.warn("Failed to record activity after wake:", error)
    })
  }, [isPremiumUser, state.assignmentResult, recordActivity])

  // Detect sleep/wake cycles and refresh activity status (Cases 50-51)
  useSleepDetection(handleDeviceWake, {
    enabled: isPremiumUser && !!state.assignmentResult,
  })

  // Listen for premium release events from other tabs (Cases 54-56)
  useEffect(() => {
    const unsubscribe = tabSync.on("PREMIUM_RELEASED", () => {
      // Backend has already released this assignment; drop the local token
      // so a later logout/beforeunload doesn't beacon a now-invalid token.
      beaconTokenRef.current = null
      setState((prev) => ({
        ...prev,
        assignmentResult: null,
        statusResult: null,
      }))
    })
    return unsubscribe
  }, [])

  // Auto-assign when premium user is detected
  useEffect(() => {
    if (isPremiumUser && currentUser) {
      autoAssignOnLogin()
    } else {
      // eslint-disable-next-line no-console
      console.warn("Conditions not met for auto-assignment:", {
        isPremiumUser,
        hasCurrentUser: !!currentUser,
      })
    }
  }, [isPremiumUser, currentUser, autoAssignOnLogin])

  // Reset polling state when user changes or gets a dedicated instance
  useEffect(() => {
    if (!state.assignmentResult?.is_shared) {
      setPollInterval(INITIAL_POLL_INTERVAL_MS)
      setPollAttempts(0)
    }
  }, [state.assignmentResult?.is_shared])

  // Poll runs while unreachable so a backend reassignment is caught; a poll result alone never clears unreachable (only a real response does).
  useEffect(() => {
    if (
      !shouldPoll(
        isPremiumUser,
        isTabLeader,
        state.assignmentResult,
        unreachable.state.instanceUnreachable,
      )
    ) {
      return
    }

    // Check if we've exceeded max attempts
    if (pollAttempts >= MAX_POLL_ATTEMPTS) {
      // eslint-disable-next-line no-console
      console.warn(
        `Max poll attempts (${MAX_POLL_ATTEMPTS}) reached. Stopping polling.`,
      )
      setState((prev) => ({
        ...prev,
        error:
          "No premium instance available after extended wait. " +
          "Please try again later or contact support.",
        isRetryableError: false,
      }))
      return
    }

    const timeoutId = setTimeout(async () => {
      try {
        const result = await assignPremiumInstance()

        if (result.assigned && !result.is_shared) {
          // eslint-disable-next-line no-console
          console.log("Premium instance now available:", result.instance_id)
          // The hook clears unreachable on an instance_id change; same-id is a no-op — reachability must come from a real response.
          setState((prev) => ({
            ...prev,
            assignmentResult: result,
            error: null,
            isRetryableError: false,
          }))
          setPollInterval(INITIAL_POLL_INTERVAL_MS)
          setPollAttempts(0)
        } else {
          setState((prev) => ({ ...prev, assignmentResult: result }))
          // eslint-disable-next-line no-console
          console.warn(
            "Still on temporary instance, will retry with backoff...",
          )
          setPollAttempts((prev) => prev + 1)
          // Exponential backoff capped at MAX_POLL_INTERVAL_MS
          setPollInterval((prev) =>
            Math.min(prev * BACKOFF_MULTIPLIER, MAX_POLL_INTERVAL_MS),
          )
        }
      } catch (error) {
        // eslint-disable-next-line no-console
        console.warn("Error polling for premium instance:", error)
        setPollAttempts((prev) => prev + 1)
        // More aggressive backoff on errors
        setPollInterval((prev) =>
          Math.min(prev * ERROR_BACKOFF_MULTIPLIER, MAX_POLL_INTERVAL_MS),
        )
      }
    }, pollInterval)

    return () => clearTimeout(timeoutId)
  }, [
    isPremiumUser,
    isTabLeader,
    state.assignmentResult,
    unreachable.state.instanceUnreachable,
    pollInterval,
    pollAttempts,
  ])

  // Handle browser close/refresh for premium users
  useEffect(() => {
    if (!isPremiumUser || !currentUser) return

    const handleBeforeUnload = () => {
      if (state.assignmentResult?.instance_id && beaconTokenRef.current) {
        const blob = new Blob(
          [JSON.stringify({ token: beaconTokenRef.current })],
          { type: BEACON_CONTENT_TYPE },
        )
        navigator.sendBeacon(BEACON_RELEASE_URL, blob)
      }
    }

    window.addEventListener("beforeunload", handleBeforeUnload)
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload)
    }
  }, [isPremiumUser, currentUser, state.assignmentResult])

  const contextValue: PremiumAssignmentContextType = {
    ...state,
    // Override state.isPremiumUser (mirror-effect-driven) with the
    // synchronously-computed value derived from currentUser. Closes the
    // logout race where the mirror effect hasn't propagated yet on a
    // same-tab sign-out → sign-in flow, causing useLogout's gate to bail
    // with stale state.isPremiumUser=false (ISSUE_5).
    isPremiumUser,
    assign,
    release,
    getStatus,
    updateRoutingInfo,
    autoReleaseOnLogout,
    dismissInactivityWarning,
    recordActivity,
    unreachable,
  }

  return (
    <PremiumAssignmentContext.Provider value={contextValue}>
      {children}
    </PremiumAssignmentContext.Provider>
  )
}

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
import { PlanName, SubscriptionStatus } from "const/Subscription"
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

// Polling configuration constants
const INITIAL_POLL_INTERVAL_MS = 5000
const MAX_POLL_INTERVAL_MS = 60000
const MAX_POLL_ATTEMPTS = 120 // ~10 minutes at initial rate
const BACKOFF_MULTIPLIER = 1.5
const ERROR_BACKOFF_MULTIPLIER = 2

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
  isPremiumUser: boolean
  showInactivityWarning: boolean
  lastActivityTime: number
  heartbeatFailing: boolean
}

interface PremiumAssignmentContextType extends PremiumAssignmentState {
  assign: () => Promise<PremiumAssignmentResult | null>
  release: () => Promise<unknown>
  getStatus: () => Promise<PremiumStatusResult | null>
  updateRoutingInfo: () => Promise<RoutingInfo | null>
  autoReleaseOnLogout: () => Promise<unknown>
  dismissInactivityWarning: () => void
  recordActivity: () => Promise<void>
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
    isPremiumUser: false,
    showInactivityWarning: false,
    lastActivityTime: Date.now(),
    heartbeatFailing: false,
  })

  // Flag to prevent multiple auto-assignment attempts per session
  const [hasAttemptedAutoAssignment, setHasAttemptedAutoAssignment] =
    useState(false)

  // Polling state with backoff
  const [pollInterval, setPollInterval] = useState(INITIAL_POLL_INTERVAL_MS)
  const [pollAttempts, setPollAttempts] = useState(0)

  // Cross-tab leader election for coordinating polling
  const [isTabLeader, setIsTabLeader] = useState(false)
  const leaderElectionRef = useRef<CrossTabLeaderElection | null>(null)
  const beaconTokenRef = useRef<string | null>(null)

  // Refs for values that inactivity check needs but shouldn't trigger re-renders
  const lastActivityTimeRef = useRef(state.lastActivityTime)
  const showInactivityWarningRef = useRef(state.showInactivityWarning)

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
    setHasAttemptedAutoAssignment(false)
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
        isPremiumUser: false,
        showInactivityWarning: false,
        lastActivityTime: Date.now(),
        heartbeatFailing: false,
      })
      setHasAttemptedAutoAssignment(false)
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
      () => {
        // eslint-disable-next-line no-console
        console.log("This tab became the leader for premium polling")
        setIsTabLeader(true)
      },
      () => {
        // eslint-disable-next-line no-console
        console.log("This tab lost leadership for premium polling")
        setIsTabLeader(false)
      },
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

      setState((prev) => ({ ...prev, isAssigning: true, error: null }))

      try {
        const result = await assignPremiumInstance()

        setState((prev) => ({
          ...prev,
          isAssigning: false,
          assignmentResult: result,
          error: result.assigned ? null : result.message,
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
  }, [])

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
    if (!isPremiumUser || hasAttemptedAutoAssignment) return

    // Set flag immediately to prevent duplicate calls
    setHasAttemptedAutoAssignment(true)

    try {
      // Check current status first (inline to avoid dependency issues)
      const statusResponse = await getPremiumStatus()
      if (statusResponse?.assignment) {
        // User already has an assignment - update state immediately so notifications trigger
        // Convert PremiumAssignment to PremiumAssignmentResult format
        const assignmentResult: PremiumAssignmentResult = {
          message: "Premium instance already assigned",
          instance_id: statusResponse.assignment.instance_id,
          assigned: true,
          is_shared: statusResponse.assignment.is_shared,
        }
        setState((prev) => ({
          ...prev,
          assignmentResult,
          error: null,
        }))
        routingService.setPremiumAssigned(true)
        try {
          const tokenRes = await getBeaconTokenApi()
          beaconTokenRef.current = tokenRes.data.token
        } catch {
          // Non-critical; beacon will fail gracefully
        }
        return
      }

      // Attempt assignment directly (inline to avoid dependency issues)
      const assignmentResponse = await assignPremiumInstance()
      if (assignmentResponse?.assigned) {
        // Update state to reflect the assignment
        setState((prev) => ({
          ...prev,
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
      }
    } catch (error) {
      // eslint-disable-next-line no-console
      console.warn("Auto-assignment failed:", error)
      routingService.clearRoutingInfo()
    }
  }, [isPremiumUser, hasAttemptedAutoAssignment])

  /**
   * Auto-release on logout
   */
  const autoReleaseOnLogout = useCallback(async (): Promise<unknown> => {
    // Always attempt release regardless of local state.
    try {
      return await release()
    } catch (error) {
      // eslint-disable-next-line no-console
      console.warn("Failed to release premium instance on logout:", error)
      return null
    }
  }, [release])

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
        console.log(
          "2 hours of inactivity detected - auto-releasing premium instance",
        )
        setState((prev) => ({ ...prev, showInactivityWarning: false }))
        autoReleaseOnLogout().catch((error) => {
          // eslint-disable-next-line no-console
          console.error("Failed to auto-release after inactivity:", error)
        })
      } else if (
        timeSinceLastActivity >= oneHourMs &&
        !showInactivityWarningRef.current
      ) {
        // eslint-disable-next-line no-console
        console.log("1 hour of inactivity detected - showing warning")
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
    // eslint-disable-next-line no-console
    console.log("Device wake detected - checking activity status")
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
      console.log("Conditions not met for auto-assignment:", {
        isPremiumUser,
        hasCurrentUser: !!currentUser,
        hasAttemptedAutoAssignment,
      })
    }
  }, [
    isPremiumUser,
    currentUser,
    hasAttemptedAutoAssignment,
    autoAssignOnLogin,
  ])

  // Reset polling state when user changes or gets a dedicated instance
  useEffect(() => {
    if (!state.assignmentResult?.is_shared) {
      setPollInterval(INITIAL_POLL_INTERVAL_MS)
      setPollAttempts(0)
    }
  }, [state.assignmentResult?.is_shared])

  // Poll for premium instance when user is on temporary shared instance
  // Only the leader tab polls to prevent duplicate API calls
  useEffect(() => {
    const shouldPoll =
      isPremiumUser &&
      isTabLeader &&
      state.assignmentResult?.assigned &&
      state.assignmentResult?.is_shared

    if (!shouldPoll) {
      return
    }

    // Check if we've exceeded max attempts
    if (pollAttempts >= MAX_POLL_ATTEMPTS) {
      // eslint-disable-next-line no-console
      console.log(
        `Max poll attempts (${MAX_POLL_ATTEMPTS}) reached. Stopping polling.`,
      )
      setState((prev) => ({
        ...prev,
        error:
          "No premium instance available after extended wait. " +
          "Please try again later or contact support.",
      }))
      return
    }

    // eslint-disable-next-line no-console
    console.log(
      `Polling for premium instance (attempt ${pollAttempts + 1}/${MAX_POLL_ATTEMPTS}, ` +
        `interval ${pollInterval}ms)...`,
    )

    const timeoutId = setTimeout(async () => {
      try {
        const result = await assignPremiumInstance()

        if (result.assigned && !result.is_shared) {
          // eslint-disable-next-line no-console
          console.log("Premium instance now available:", result.instance_id)
          setState((prev) => ({
            ...prev,
            assignmentResult: result,
            error: null,
          }))
          // Reset polling state on success
          setPollInterval(INITIAL_POLL_INTERVAL_MS)
          setPollAttempts(0)
        } else {
          // eslint-disable-next-line no-console
          console.log("Still on temporary instance, will retry with backoff...")
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
    state.assignmentResult?.assigned,
    state.assignmentResult?.is_shared,
    pollInterval,
    pollAttempts,
  ])

  // Handle browser close/refresh for premium users
  useEffect(() => {
    if (!isPremiumUser || !currentUser) return

    const handleBeforeUnload = () => {
      if (state.assignmentResult?.instance_id && beaconTokenRef.current) {
        routingService.clearRoutingInfo()
        const beaconData = JSON.stringify({
          token: beaconTokenRef.current,
        })
        navigator.sendBeacon("/api/users/me/premium/release-beacon", beaconData)
      }
    }

    window.addEventListener("beforeunload", handleBeforeUnload)
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload)
    }
  }, [isPremiumUser, currentUser, state.assignmentResult])

  const contextValue: PremiumAssignmentContextType = {
    ...state,
    assign,
    release,
    getStatus,
    updateRoutingInfo,
    autoReleaseOnLogout,
    dismissInactivityWarning,
    recordActivity,
  }

  return (
    <PremiumAssignmentContext.Provider value={contextValue}>
      {children}
    </PremiumAssignmentContext.Provider>
  )
}

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
} from "react"
import { useSelector } from "react-redux"

import {
  assignPremiumInstance,
  getPremiumStatus,
  getRoutingInfo,
  releasePremiumInstance,
  sendPremiumHeartbeat,
  PremiumAssignmentResult,
  PremiumStatusResult,
  RoutingInfo,
} from "api/premium/PremiumAssignmentApi"
import { RootState } from "store/store"
import { routingService } from "utils/routing/RoutingService"

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
  console.log("PremiumAssignmentProvider mounted!")
  const currentUser = useSelector((state: RootState) => state.user.currentUser)

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
  })

  // Flag to prevent multiple auto-assignment attempts per session
  const [hasAttemptedAutoAssignment, setHasAttemptedAutoAssignment] =
    useState(false)

  // Calculate premium user status
  const isPremiumUser =
    currentUser?.subscription_plan_name === "Premium" &&
    (currentUser?.subscription_status === "Premium" ||
      currentUser?.subscription_status === "Limit Grace")

  // Update state when premium status changes
  useEffect(() => {
    setState((prev) => ({ ...prev, isPremiumUser }))
  }, [isPremiumUser])

  // Reset flag when user changes
  useEffect(() => {
    setHasAttemptedAutoAssignment(false)
  }, [currentUser?.id])

  /**
   * Dismiss inactivity warning
   */
  const dismissInactivityWarning = useCallback(() => {
    setState((prev) => ({ ...prev, showInactivityWarning: false }))
  }, [])

  /**
   * Record user activity by sending heartbeat
   */
  const recordActivity = useCallback(async (): Promise<void> => {
    if (!isPremiumUser) return

    try {
      await sendPremiumHeartbeat()
      setState((prev) => ({
        ...prev,
        lastActivityTime: Date.now(),
        showInactivityWarning: false,
      }))
    } catch (error) {
      console.warn("Failed to record premium user activity:", error)
    }
  }, [isPremiumUser])

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
      setState((prev) => ({
        ...prev,
        isReleasing: false,
        assignmentResult: null,
        statusResult: null,
      }))
      return result
    } catch (error: unknown) {
      // Don't treat release errors as critical
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
      console.warn("Failed to get routing info:", error)
      return null
    }
  }, [currentUser])

  /**
   * Auto-assign on premium user login (fully isolated to prevent loops)
   */
  const autoAssignOnLogin = useCallback(async () => {
    if (!isPremiumUser || hasAttemptedAutoAssignment) return

    console.log("Premium user detected, attempting auto-assignment...")

    // Set flag immediately to prevent duplicate calls
    setHasAttemptedAutoAssignment(true)

    try {
      // Check current status first (inline to avoid dependency issues)
      const statusResponse = await getPremiumStatus()
      if (statusResponse?.assignment) {
        console.log("Premium user already assigned to instance")
        return
      }

      // Attempt assignment directly (inline to avoid dependency issues)
      const assignmentResponse = await assignPremiumInstance()
      if (assignmentResponse?.assigned) {
        console.log(
          "Premium user successfully assigned to instance:",
          assignmentResponse.instance_id,
        )
        // Update state to reflect the assignment
        setState((prev) => ({
          ...prev,
          assignmentResult: assignmentResponse,
          error: null,
        }))
      }
    } catch (error) {
      console.warn("Auto-assignment failed:", error)
    }
  }, [isPremiumUser, hasAttemptedAutoAssignment])

  /**
   * Auto-release on logout
   */
  const autoReleaseOnLogout = useCallback(async (): Promise<unknown> => {
    // Check if we have an active assignment by making a fresh status call
    try {
      const currentStatus = await getPremiumStatus()
      if (currentStatus?.assignment) {
        console.log("Releasing premium instance on logout...")
        return await release()
      }
      return null
    } catch (error) {
      console.warn("Failed to check status before logout release:", error)
      return null
    }
  }, [release])

  // Inactivity monitoring for premium users
  useEffect(() => {
    if (!isPremiumUser || !currentUser || !state.assignmentResult) {
      return
    }

    let inactivityCheckInterval: NodeJS.Timeout | null = null

    const checkInactivity = () => {
      const now = Date.now()
      const timeSinceLastActivity = now - state.lastActivityTime

      const oneHourMs = 60 * 60 * 1000 // 1 hour
      const twoHoursMs = 2 * 60 * 60 * 1000 // 2 hours

      console.log(
        `⏰ Inactivity check: ${Math.round(timeSinceLastActivity / 1000 / 60)}min since last activity`,
      )

      if (timeSinceLastActivity >= twoHoursMs) {
        // 2 hours of inactivity - auto-release
        console.log(
          "🔴 2 hours of inactivity detected - auto-releasing premium instance",
        )
        setState((prev) => ({ ...prev, showInactivityWarning: false }))
        autoReleaseOnLogout().catch((error) => {
          console.error("Failed to auto-release after inactivity:", error)
        })
      } else if (
        timeSinceLastActivity >= oneHourMs &&
        !state.showInactivityWarning
      ) {
        // 1 hour of inactivity - show warning
        console.log("🟡 1 hour of inactivity detected - showing warning")
        setState((prev) => ({ ...prev, showInactivityWarning: true }))
      }
    }

    // Check inactivity every 30 seconds
    inactivityCheckInterval = setInterval(checkInactivity, 30 * 1000)

    return () => {
      if (inactivityCheckInterval) {
        clearInterval(inactivityCheckInterval)
      }
    }
  }, [
    isPremiumUser,
    currentUser,
    state.assignmentResult,
    state.lastActivityTime,
    state.showInactivityWarning,
    autoReleaseOnLogout,
  ])

  // Auto-assign when premium user is detected
  useEffect(() => {
    console.log("PremiumAssignmentContext useEffect triggered:", {
      isPremiumUser,
      currentUserId: currentUser?.id,
      currentUserEmail: currentUser?.email,
      subscriptionPlan: currentUser?.subscription_plan_name,
      subscriptionStatus: currentUser?.subscription_status,
      hasAttemptedAutoAssignment,
    })

    if (isPremiumUser && currentUser) {
      console.log("Conditions met, calling autoAssignOnLogin...")
      autoAssignOnLogin()
    } else {
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

  // Handle browser close/refresh for premium users
  useEffect(() => {
    if (!isPremiumUser) return

    const handleBeforeUnload = () => {
      // Try to release premium assignment on browser close/refresh
      // Note: This is best-effort and may not always complete due to browser limitations
      autoReleaseOnLogout().catch((error) => {
        console.warn("Failed to release on beforeunload:", error)
      })
    }

    window.addEventListener("beforeunload", handleBeforeUnload)
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload)
    }
  }, [isPremiumUser, autoReleaseOnLogout])

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

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
      // eslint-disable-next-line no-console
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
        }
        setState((prev) => ({
          ...prev,
          assignmentResult,
          error: null,
        }))
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
      }
    } catch (error) {
      // eslint-disable-next-line no-console
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
        return await release()
      }
      return null
    } catch (error) {
      // eslint-disable-next-line no-console
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
      // eslint-disable-next-line no-console
      console.log(
        `Inactivity check: ${Math.round(timeSinceLastActivity / 1000 / 60)}min since last activity`,
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
        !state.showInactivityWarning
      ) {
        // eslint-disable-next-line no-console
        console.log("1 hour of inactivity detected - showing warning")
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

  // Poll for premium instance when user is on temporary shared instance
  useEffect(() => {
    if (
      !isPremiumUser ||
      !state.assignmentResult?.assigned ||
      !state.assignmentResult?.is_shared ||
      state.assignmentResult?.assignment_source !== "autoscaling_temp"
    ) {
      return
    }

    // eslint-disable-next-line no-console
    console.log(
      "User is on temporary shared instance, polling for premium instance...",
    )

    let pollInterval: NodeJS.Timeout | null = null

    const pollForPremiumInstance = async () => {
      try {
        // Check if premium instance is now available
        const result = await assignPremiumInstance()

        if (result.assigned && !result.is_shared) {
          // eslint-disable-next-line no-console
          console.log("Premium instance now available:", result.instance_id)
          setState((prev) => ({
            ...prev,
            assignmentResult: result,
            error: null,
          }))

          // Stop polling
          if (pollInterval) {
            clearInterval(pollInterval)
            pollInterval = null
          }
        } else {
          // eslint-disable-next-line no-console
          console.log("Still on temporary instance, will retry...")
        }
      } catch (error) {
        // eslint-disable-next-line no-console
        console.warn("Error polling for premium instance:", error)
      }
    }

    // Poll every 5 seconds
    pollInterval = setInterval(pollForPremiumInstance, 5000)

    // Cleanup on unmount or when conditions change
    return () => {
      if (pollInterval) {
        clearInterval(pollInterval)
      }
    }
  }, [
    isPremiumUser,
    state.assignmentResult?.assigned,
    state.assignmentResult?.is_shared,
    state.assignmentResult?.assignment_source,
  ])

  // Handle browser close/refresh for premium users
  useEffect(() => {
    if (!isPremiumUser) return

    const handleBeforeUnload = () => {
      // Try to release premium assignment on browser close/refresh
      // Note: This is best-effort and may not always complete due to browser limitations
      autoReleaseOnLogout().catch((error) => {
        // eslint-disable-next-line no-console
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

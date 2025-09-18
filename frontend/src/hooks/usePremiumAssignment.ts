/**
 * Premium Assignment Hook
 *
 * React hook for managing premium user instance assignments.
 */

import { useCallback, useEffect, useState } from "react"
import { useSelector } from "react-redux"

import {
  assignPremiumInstance,
  getPremiumStatus,
  getRoutingInfo,
  PremiumAssignmentResult,
  PremiumStatusResult,
  releasePremiumInstance,
  RoutingInfo,
} from "api/premium/PremiumAssignmentApi"
import { RootState } from "store/store"
import { routingService } from "utils/routing/RoutingService"

export interface PremiumAssignmentState {
  isAssigning: boolean
  isReleasing: boolean
  assignmentResult: PremiumAssignmentResult | null
  statusResult: PremiumStatusResult | null
  routingInfo: RoutingInfo | null
  error: string | null
}

export const usePremiumAssignment = () => {
  const currentUser = useSelector((state: RootState) => state.user.currentUser)

  const [state, setState] = useState<PremiumAssignmentState>({
    isAssigning: false,
    isReleasing: false,
    assignmentResult: null,
    statusResult: null,
    routingInfo: null,
    error: null,
  })

  const isPremiumUser =
    currentUser?.subscription_plan_name === "Premium" &&
    currentUser?.subscription_status === "active"

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
    if (!currentUser) return

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
   * Auto-assign on premium user login
   */
  const autoAssignOnLogin = useCallback(async () => {
    if (!isPremiumUser) return

    // eslint-disable-next-line no-console
    console.log("Premium user detected, attempting auto-assignment...")

    // Check current status first
    const status = await getStatus()

    // If already assigned, just update routing info
    if (status?.assignment) {
      // eslint-disable-next-line no-console
      console.log("Premium user already assigned to instance")
      await updateRoutingInfo()
      return
    }

    // Attempt assignment (this provisions the premium instance)
    const result = await assign()
    if (result?.assigned) {
      // eslint-disable-next-line no-console
      console.log(
        "Premium user successfully assigned to instance:",
        result.instance_id,
      )
      await updateRoutingInfo()
    }
  }, [isPremiumUser, assign, getStatus, updateRoutingInfo])

  /**
   * Auto-release on logout
   */
  const autoReleaseOnLogout = useCallback(async () => {
    if (state.statusResult?.assignment) {
      // eslint-disable-next-line no-console
      console.log("Releasing premium instance on logout...")
      await release()
    }
  }, [state.statusResult, release])

  // Auto-assign when premium user is detected
  useEffect(() => {
    if (isPremiumUser && currentUser) {
      autoAssignOnLogin()
    }
  }, [isPremiumUser, currentUser, autoAssignOnLogin])

  return {
    ...state,
    isPremiumUser,
    assign,
    release,
    getStatus,
    updateRoutingInfo,
    autoAssignOnLogin,
    autoReleaseOnLogout,
  }
}

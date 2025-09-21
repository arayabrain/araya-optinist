/**
 * Premium Assignment API
 *
 * Handles API calls for premium user instance assignment and management.
 */

import axios from "utils/axios"

export interface RoutingInfo {
  user_id: string
  user_tier: "premium" | "free"
  requires_premium_routing: boolean
  routing_headers: Record<string, string>
}

export interface PremiumAssignmentResult {
  message: string
  instance_id?: string
  assigned: boolean
  retry_after?: number
  scaling_in_progress?: boolean
}

export interface PremiumReleaseResult {
  message: string
  released_instance?: string
  released: boolean
}

export interface PremiumAssignment {
  instance_id: string
  assigned_at: string
  status: string
}

export interface PremiumStatusResult {
  user_id: number
  subscription_type: "premium" | "free"
  is_premium: boolean
  assignment: PremiumAssignment | null
  migration_ready?: boolean
  health_status?: string
  error?: string
}

export interface PremiumHeartbeatResult {
  message: string
  updated: boolean
  user_id: number
  user_tier: "premium" | "free"
  assignment_active: boolean
  activity_update?: boolean
  error?: string
}

/**
 * Get routing information for the current user
 */
export const getRoutingInfo = async (): Promise<RoutingInfo> => {
  const response = await axios.get("/users/me/routing-info")
  return response.data
}

/**
 * Assign current user to a premium instance
 */
export const assignPremiumInstance =
  async (): Promise<PremiumAssignmentResult> => {
    const response = await axios.post("/users/me/premium/assign")
    return response.data
  }

/**
 * Release current user from their premium instance
 */
export const releasePremiumInstance =
  async (): Promise<PremiumReleaseResult> => {
    const response = await axios.delete("/users/me/premium/assign")
    return response.data
  }

/**
 * Get current premium assignment status
 */
export const getPremiumStatus = async (): Promise<PremiumStatusResult> => {
  const response = await axios.get("/users/me/premium/status")
  return response.data
}

/**
 * Send heartbeat to update activity timestamp for premium users
 * Prevents stale assignment cleanup for active users
 */
export const sendPremiumHeartbeat =
  async (): Promise<PremiumHeartbeatResult> => {
    const response = await axios.post("/users/me/premium/heartbeat")
    return response.data
  }

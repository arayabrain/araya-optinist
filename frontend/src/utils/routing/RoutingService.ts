/**
 * Routing Service for Premium User ALB Header Management
 *
 * Handles the logic for determining when to include premium routing headers
 * (X-User-Tier, X-User-ID) for ALB-based routing to dedicated instances.
 */

import { UserDTO } from "api/users/UsersApiDTO"

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

class RoutingService {
  private routingInfo: RoutingInfo | null = null
  private lastFetch: number = 0
  private readonly CACHE_DURATION = 5 * 60 * 1000 // 5 minutes

  /**
   * Get routing headers for the current user request
   */
  getRoutingHeaders(): Record<string, string> {
    if (!this.routingInfo || !this.routingInfo.requires_premium_routing) {
      return {}
    }

    return this.routingInfo.routing_headers
  }

  /**
   * Clear routing information (on logout)
   */
  clearRoutingInfo(): void {
    this.routingInfo = null
    this.lastFetch = 0
  }

  /**
   * Check if user requires premium routing
   */
  requiresPremiumRouting(): boolean {
    return this.routingInfo?.requires_premium_routing || false
  }

  /**
   * Get current user tier
   */
  getUserTier(): "premium" | "free" | null {
    return this.routingInfo?.user_tier || null
  }

  /**
   * Check if routing info is stale and needs refresh
   */
  isRoutingInfoStale(): boolean {
    if (!this.routingInfo) return true
    return Date.now() - this.lastFetch > this.CACHE_DURATION
  }

  /**
   * Update routing information for a user
   */
  updateRoutingInfo(user: UserDTO): void {
    const isPremium = this.isPremiumUser(user)

    this.routingInfo = {
      user_id: user.id?.toString() || "",
      user_tier: isPremium ? "premium" : "free",
      requires_premium_routing: isPremium,
      routing_headers: isPremium
        ? {
            "X-User-Tier": "premium",
            "X-User-ID": user.id?.toString() || "",
          }
        : {},
    }

    this.lastFetch = Date.now()
  }

  /**
   * Determine if a user is premium based on subscription info
   */
  private isPremiumUser(user: UserDTO): boolean {
    return (
      user.subscription_plan_name === "Premium" &&
      (user.subscription_status === "Premium" ||
        user.subscription_status === "Limit Grace")
    )
  }

  getCurrentRoutingInfo(): RoutingInfo | null {
    return this.routingInfo
  }
}

// Export singleton instance
export const routingService = new RoutingService()
export default routingService

/**
 * Routing Service for Premium User ALB Header Management
 *
 * Handles the logic for managing non-reversible routing IDs issued by the backend.
 * The backend generates cryptographically secure routing IDs from user UIDs using
 * HMAC-SHA256, which cannot be forged or reverse-engineered.
 *
 * Security Flow:
 * 1. Backend validates Firebase JWT authentication
 * 2. Backend generates non-reversible routing_id from UID (HMAC-SHA256)
 * 3. Backend sends routing_id in X-Routing-ID response header
 * 4. Frontend stores routing_id and includes it in subsequent requests
 * 5. ALB routes based on routing_id, backend validates against JWT UID
 *
 * Privacy: User UID is never exposed to the client, only the opaque routing_id
 */

import { UserDTO } from "api/users/UsersApiDTO"
import { PlanName, SubscriptionStatus, UserTier } from "const/Subscription"

export interface RoutingInfo {
  user_id: string
  user_tier: UserTier
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
  private routingToken: string | null = null
  private lastFetch: number = 0
  private readonly CACHE_DURATION = 5 * 60 * 1000 // 5 minutes
  private readonly STORAGE_KEY = "routing_id"

  constructor() {
    // Load token from localStorage on initialization
    this.loadTokenFromStorage()
  }

  /**
   * Get routing headers for the current user request
   * Returns the backend-issued non-reversible routing ID
   */
  getRoutingHeaders(): Record<string, string> {
    if (!this.routingToken) {
      return {}
    }

    return {
      "X-Routing-ID": this.routingToken,
    }
  }

  /**
   * Update routing ID from backend response header
   * Called by axios response interceptor when X-Routing-ID header is present
   */
  updateRoutingToken(token: string): void {
    this.routingToken = token
    this.saveTokenToStorage(token)
  }

  /**
   * Update routing information for a user
   * Note: This maintains user tier info but no longer sets client-controlled headers
   */
  updateRoutingInfo(user: UserDTO): void {
    const isPremium = this.isPremiumUser(user)

    this.routingInfo = {
      user_id: user.uid || "",
      user_tier: isPremium ? UserTier.PREMIUM : UserTier.FREE,
      requires_premium_routing: isPremium,
      routing_headers: {}, // No longer client-controlled
    }

    this.lastFetch = Date.now()
  }

  /**
   * Clear routing information (on logout)
   */
  clearRoutingInfo(): void {
    this.routingInfo = null
    this.routingToken = null
    this.lastFetch = 0
    this.clearTokenFromStorage()
  }

  /**
   * Get current routing ID (for debugging)
   */
  getRoutingToken(): string | null {
    return this.routingToken
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
  getUserTier(): UserTier | null {
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
   * Determine if a user is premium based on subscription info
   */
  private isPremiumUser(user: UserDTO): boolean {
    return (
      user.subscription_plan_name === PlanName.PREMIUM &&
      (user.subscription_status === SubscriptionStatus.PREMIUM ||
        user.subscription_status === SubscriptionStatus.LIMIT_GRACE)
    )
  }

  /**
   * Get current routing information
   */
  getCurrentRoutingInfo(): RoutingInfo | null {
    return this.routingInfo
  }

  /**
   * Load routing ID from localStorage
   */
  private loadTokenFromStorage(): void {
    try {
      const token = localStorage.getItem(this.STORAGE_KEY)
      if (token) {
        this.routingToken = token
      }
    } catch (e) {
      console.warn("Failed to load routing ID from localStorage:", e)
    }
  }

  /**
   * Save routing ID to localStorage
   */
  private saveTokenToStorage(token: string): void {
    try {
      localStorage.setItem(this.STORAGE_KEY, token)
    } catch (e) {
      console.warn("Failed to save routing ID to localStorage:", e)
    }
  }

  /**
   * Clear routing ID from localStorage
   */
  private clearTokenFromStorage(): void {
    try {
      localStorage.removeItem(this.STORAGE_KEY)
    } catch (e) {
      console.warn("Failed to clear routing ID from localStorage:", e)
    }
  }
}

// Export singleton instance
export const routingService = new RoutingService()
export default routingService

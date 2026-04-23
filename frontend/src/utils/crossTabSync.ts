/**
 * Cross-Tab Leader Election
 *
 * Coordinates polling and other operations across browser tabs to prevent
 * duplicate API calls. Only the leader tab performs polling operations.
 */

const LEADER_KEY = "premium_poll_leader"
const LEADER_HEARTBEAT_MS = 2000
const LEADER_TIMEOUT_MS = LEADER_HEARTBEAT_MS * 2.5

export class CrossTabLeaderElection {
  private isLeader = false
  private heartbeatInterval: ReturnType<typeof setInterval> | null = null
  private checkInterval: ReturnType<typeof setInterval> | null = null
  private onBecomeLeader: () => void
  private onLoseLeadership: () => void
  private tabId: string

  constructor(onBecomeLeader: () => void, onLoseLeadership: () => void) {
    this.onBecomeLeader = onBecomeLeader
    this.onLoseLeadership = onLoseLeadership
    this.tabId = `${Date.now()}-${Math.random().toString(36).slice(2)}`

    this.tryBecomeLeader()
    this.startCheckingLeadership()

    // Listen for storage changes from other tabs
    window.addEventListener("storage", this.handleStorageChange)

    // Resign leadership on page unload
    window.addEventListener("beforeunload", this.resignLeadership)
  }

  private tryBecomeLeader = () => {
    const stored = localStorage.getItem(LEADER_KEY)
    const now = Date.now()

    if (!stored) {
      // No leader, claim leadership
      this.claimLeadership()
      return
    }

    try {
      const { timestamp, tabId } = JSON.parse(stored)

      // If we're already the leader, just update heartbeat
      if (tabId === this.tabId) {
        this.updateHeartbeat()
        return
      }

      // Check if leader is stale
      if (now - timestamp > LEADER_TIMEOUT_MS) {
        this.claimLeadership()
      }
    } catch {
      // Invalid stored data, claim leadership
      this.claimLeadership()
    }
  }

  private claimLeadership = () => {
    const data = JSON.stringify({
      timestamp: Date.now(),
      tabId: this.tabId,
    })
    localStorage.setItem(LEADER_KEY, data)

    if (!this.isLeader) {
      this.isLeader = true
      this.startHeartbeat()
      this.onBecomeLeader()
    }
  }

  private updateHeartbeat = () => {
    if (!this.isLeader) return

    const data = JSON.stringify({
      timestamp: Date.now(),
      tabId: this.tabId,
    })
    localStorage.setItem(LEADER_KEY, data)
  }

  private startHeartbeat = () => {
    if (this.heartbeatInterval) return

    this.heartbeatInterval = setInterval(() => {
      if (this.isLeader) {
        this.updateHeartbeat()
      }
    }, LEADER_HEARTBEAT_MS)
  }

  private stopHeartbeat = () => {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval)
      this.heartbeatInterval = null
    }
  }

  private startCheckingLeadership = () => {
    // Periodically check if we should become leader (in case current dies)
    this.checkInterval = setInterval(() => {
      if (!this.isLeader) {
        this.tryBecomeLeader()
      }
    }, LEADER_TIMEOUT_MS)
  }

  private handleStorageChange = (e: StorageEvent) => {
    if (e.key !== LEADER_KEY) return

    if (!e.newValue) {
      // Leader key was removed, try to become leader
      this.tryBecomeLeader()
      return
    }

    try {
      const { tabId } = JSON.parse(e.newValue)

      if (this.isLeader && tabId !== this.tabId) {
        // Another tab claimed leadership
        this.isLeader = false
        this.stopHeartbeat()
        this.onLoseLeadership()
      }
    } catch {
      // Invalid data, ignore
    }
  }

  private resignLeadership = () => {
    if (this.isLeader) {
      localStorage.removeItem(LEADER_KEY)
      this.isLeader = false
    }
  }

  /**
   * Check if this tab is currently the leader
   */
  getIsLeader = (): boolean => {
    return this.isLeader
  }

  /**
   * Clean up event listeners and intervals
   */
  destroy = () => {
    this.stopHeartbeat()
    if (this.checkInterval) {
      clearInterval(this.checkInterval)
      this.checkInterval = null
    }
    window.removeEventListener("storage", this.handleStorageChange)
    window.removeEventListener("beforeunload", this.resignLeadership)
    this.resignLeadership()
  }
}

// Activity sync across tabs
const ACTIVITY_KEY = "premium_last_activity"

export const syncActivityAcrossTabs = (timestamp: number) => {
  localStorage.setItem(ACTIVITY_KEY, timestamp.toString())
}

export const getLastActivityFromAnyTab = (): number => {
  const stored = localStorage.getItem(ACTIVITY_KEY)
  return stored ? parseInt(stored, 10) : 0
}

export const onActivityFromOtherTab = (
  callback: (timestamp: number) => void,
): (() => void) => {
  const handler = (e: StorageEvent) => {
    if (e.key === ACTIVITY_KEY && e.newValue) {
      callback(parseInt(e.newValue, 10))
    }
  }

  window.addEventListener("storage", handler)
  return () => window.removeEventListener("storage", handler)
}

// ============================================================================
// TabSyncService - BroadcastChannel for real-time cross-tab communication
// ============================================================================

export type TabSyncMessageType =
  | "STORAGE_UPDATED"
  | "ALERT_DISMISSED"
  | "PREMIUM_RELEASED"
  | "PREMIUM_INSTANCE_UNREACHABLE"
  | "PREMIUM_INSTANCE_REACHABLE"
  | "PREMIUM_INSTANCE_PROBE_UPDATE"
  | "LOGOUT"

export interface TabSyncMessage {
  type: TabSyncMessageType
  payload?: unknown
}

type MessageHandler = (message: TabSyncMessage) => void

const TAB_SYNC_CHANNEL = "app_sync"

/**
 * Service for real-time cross-tab communication using BroadcastChannel.
 *
 * Use this for events that need immediate synchronization across all tabs,
 * such as logout, premium release, or storage updates.
 *
 * BroadcastChannel is preferred over localStorage events for:
 * - Lower latency (direct message passing vs storage polling)
 * - Larger payload support
 * - Cleaner API for pub/sub patterns
 */
export class TabSyncService {
  private channel: BroadcastChannel | null = null
  private handlers: Map<TabSyncMessageType, Set<MessageHandler>> = new Map()
  private globalHandlers: Set<MessageHandler> = new Set()

  constructor() {
    if (typeof BroadcastChannel !== "undefined") {
      this.channel = new BroadcastChannel(TAB_SYNC_CHANNEL)
      this.channel.onmessage = this.handleMessage
    }
  }

  /**
   * Broadcast a message to all other tabs.
   */
  broadcast(message: TabSyncMessage): void {
    if (this.channel) {
      this.channel.postMessage(message)
    }
  }

  /**
   * Subscribe to messages of a specific type.
   * Returns an unsubscribe function.
   */
  on(type: TabSyncMessageType, handler: MessageHandler): () => void {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set())
    }
    this.handlers.get(type)!.add(handler)

    return () => {
      this.handlers.get(type)?.delete(handler)
    }
  }

  /**
   * Subscribe to all messages.
   * Returns an unsubscribe function.
   */
  onAny(handler: MessageHandler): () => void {
    this.globalHandlers.add(handler)
    return () => {
      this.globalHandlers.delete(handler)
    }
  }

  /**
   * Broadcast storage update event.
   */
  broadcastStorageUpdate(): void {
    this.broadcast({ type: "STORAGE_UPDATED" })
  }

  /**
   * Broadcast alert dismissal event.
   */
  broadcastAlertDismissed(alertId: string): void {
    this.broadcast({ type: "ALERT_DISMISSED", payload: { alertId } })
  }

  /**
   * Broadcast premium release event.
   */
  broadcastPremiumReleased(): void {
    this.broadcast({ type: "PREMIUM_RELEASED" })
  }

  /**
   * Broadcast logout event to all tabs.
   */
  broadcastLogout(): void {
    this.broadcast({ type: "LOGOUT" })
  }

  private handleMessage = (event: MessageEvent<TabSyncMessage>): void => {
    const message = event.data
    if (!message || !message.type) return

    // Call type-specific handlers
    const typeHandlers = this.handlers.get(message.type)
    if (typeHandlers) {
      typeHandlers.forEach((handler) => {
        try {
          handler(message)
        } catch (error) {
          // eslint-disable-next-line no-console
          console.error(
            `TabSyncService: Error in handler for ${message.type}:`,
            error,
          )
        }
      })
    }

    // Call global handlers
    this.globalHandlers.forEach((handler) => {
      try {
        handler(message)
      } catch (error) {
        // eslint-disable-next-line no-console
        console.error("TabSyncService: Error in global handler:", error)
      }
    })
  }

  /**
   * Clean up the BroadcastChannel.
   */
  destroy(): void {
    if (this.channel) {
      this.channel.close()
      this.channel = null
    }
    this.handlers.clear()
    this.globalHandlers.clear()
  }
}

// Singleton instance for application-wide use
export const tabSync = new TabSyncService()

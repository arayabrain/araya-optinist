/**
 * Cross-Tab Operation Locking Utility (Case 52)
 *
 * Provides cross-tab awareness for workspace operations to prevent conflicts
 * like deleting a workspace while an upload is in progress in another tab.
 *
 * NOTE: Lock acquisition is not strictly atomic due to localStorage limitations.
 * Two tabs could theoretically both read "no lock" and write their own lock.
 * This is acceptable for our use case (warning users, not strict enforcement).
 * For stricter coordination, consider Web Locks API or server-side locking.
 */

const LOCK_KEY_PREFIX = "workspace_op_lock_"
const LOCK_STALE_TIMEOUT_MS = 5 * 60 * 1000 // 5 minutes

interface OperationLock {
  operation: OperationType
  timestamp: number
  tabId: string
}

export type OperationType = "upload" | "delete" | "run" | "sync"

let tabId: string | null = null

const getTabId = (): string => {
  if (!tabId) {
    tabId = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  }
  return tabId
}

const getLockKey = (workspaceId: string): string => {
  return `${LOCK_KEY_PREFIX}${workspaceId}`
}

const isLockStale = (lock: OperationLock): boolean => {
  return Date.now() - lock.timestamp > LOCK_STALE_TIMEOUT_MS
}

/**
 * Acquire a lock for a workspace operation.
 * Returns true if lock was acquired, false if another operation is in progress.
 */
export const acquireWorkspaceLock = (
  workspaceId: string,
  operation: OperationType,
): boolean => {
  const key = getLockKey(workspaceId)
  const existing = localStorage.getItem(key)

  if (existing) {
    try {
      const lock: OperationLock = JSON.parse(existing)

      // If lock is stale, we can take over
      if (isLockStale(lock)) {
        const newLock: OperationLock = {
          operation,
          timestamp: Date.now(),
          tabId: getTabId(),
        }
        localStorage.setItem(key, JSON.stringify(newLock))
        return true
      }

      // If we already hold the lock, update timestamp
      if (lock.tabId === getTabId()) {
        const newLock: OperationLock = {
          ...lock,
          timestamp: Date.now(),
        }
        localStorage.setItem(key, JSON.stringify(newLock))
        return true
      }

      // Another tab holds a valid lock
      return false
    } catch {
      // Invalid stored data, claim lock
      const newLock: OperationLock = {
        operation,
        timestamp: Date.now(),
        tabId: getTabId(),
      }
      localStorage.setItem(key, JSON.stringify(newLock))
      return true
    }
  }

  // No existing lock, claim it
  const newLock: OperationLock = {
    operation,
    timestamp: Date.now(),
    tabId: getTabId(),
  }
  localStorage.setItem(key, JSON.stringify(newLock))
  return true
}

/**
 * Release a lock for a workspace operation.
 * Only releases if this tab holds the lock.
 */
export const releaseWorkspaceLock = (workspaceId: string): void => {
  const key = getLockKey(workspaceId)
  const existing = localStorage.getItem(key)

  if (existing) {
    try {
      const lock: OperationLock = JSON.parse(existing)
      // Only release if we hold the lock
      if (lock.tabId === getTabId()) {
        localStorage.removeItem(key)
      }
    } catch {
      // Invalid data, remove it
      localStorage.removeItem(key)
    }
  }
}

/**
 * Get the current lock for a workspace, if any.
 * Returns null if no valid lock exists.
 */
export const getWorkspaceLock = (workspaceId: string): OperationLock | null => {
  const key = getLockKey(workspaceId)
  const existing = localStorage.getItem(key)

  if (!existing) {
    return null
  }

  try {
    const lock: OperationLock = JSON.parse(existing)
    if (isLockStale(lock)) {
      localStorage.removeItem(key)
      return null
    }
    return lock
  } catch {
    localStorage.removeItem(key)
    return null
  }
}

/**
 * Check if a workspace has any operation in progress (from any tab).
 */
export const hasActiveOperation = (workspaceId: string): boolean => {
  const lock = getWorkspaceLock(workspaceId)
  return lock !== null
}

/**
 * Check if this tab holds the lock for a workspace.
 */
export const holdsLock = (workspaceId: string): boolean => {
  const lock = getWorkspaceLock(workspaceId)
  return lock !== null && lock.tabId === getTabId()
}

/**
 * Listen for lock changes from other tabs.
 */
export const onLockChange = (
  workspaceId: string,
  callback: (lock: OperationLock | null) => void,
): (() => void) => {
  const key = getLockKey(workspaceId)

  const handler = (e: StorageEvent) => {
    if (e.key !== key) return

    if (!e.newValue) {
      callback(null)
      return
    }

    try {
      const lock: OperationLock = JSON.parse(e.newValue)
      if (isLockStale(lock)) {
        callback(null)
      } else {
        callback(lock)
      }
    } catch {
      callback(null)
    }
  }

  window.addEventListener("storage", handler)
  return () => window.removeEventListener("storage", handler)
}

/**
 * Refresh the timestamp on a lock to prevent it from going stale.
 * Call this periodically during long operations.
 */
export const refreshLock = (workspaceId: string): boolean => {
  const lock = getWorkspaceLock(workspaceId)
  if (lock && lock.tabId === getTabId()) {
    const newLock: OperationLock = {
      ...lock,
      timestamp: Date.now(),
    }
    localStorage.setItem(getLockKey(workspaceId), JSON.stringify(newLock))
    return true
  }
  return false
}

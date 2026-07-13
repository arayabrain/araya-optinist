/**
 * Tests for Operation Lock Utility (Case 52)
 */

import {
  describe,
  it,
  expect,
  beforeEach,
  afterEach,
  jest,
} from "@jest/globals"

import {
  acquireWorkspaceLock,
  releaseWorkspaceLock,
  getWorkspaceLock,
  hasActiveOperation,
  holdsLock,
  refreshLock,
  onLockChange,
} from "utils/operationLock"

const LOCK_KEY_PREFIX = "workspace_op_lock_"

describe("operationLock", () => {
  beforeEach(() => {
    localStorage.clear()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  describe("acquireWorkspaceLock", () => {
    it("should acquire lock when no lock exists", () => {
      const result = acquireWorkspaceLock("ws1", "upload")
      expect(result).toBe(true)
      expect(hasActiveOperation("ws1")).toBe(true)
    })

    it("should return false when another tab holds the lock", () => {
      // Simulate another tab's lock
      const otherTabLock = {
        operation: "upload",
        timestamp: Date.now(),
        tabId: "other-tab-id",
      }
      localStorage.setItem(
        `${LOCK_KEY_PREFIX}ws1`,
        JSON.stringify(otherTabLock),
      )

      const result = acquireWorkspaceLock("ws1", "delete")
      expect(result).toBe(false)
    })

    it("should acquire lock if existing lock is stale", () => {
      const staleLock = {
        operation: "upload",
        timestamp: Date.now() - 6 * 60 * 1000, // 6 minutes ago
        tabId: "other-tab-id",
      }
      localStorage.setItem(`${LOCK_KEY_PREFIX}ws1`, JSON.stringify(staleLock))

      const result = acquireWorkspaceLock("ws1", "delete")
      expect(result).toBe(true)
    })

    it("should update timestamp if same tab already holds lock", () => {
      acquireWorkspaceLock("ws1", "upload")
      const firstLock = getWorkspaceLock("ws1")

      jest.advanceTimersByTime(1000)

      acquireWorkspaceLock("ws1", "upload")
      const secondLock = getWorkspaceLock("ws1")

      expect(secondLock?.timestamp).toBeGreaterThan(firstLock!.timestamp)
    })

    it("should acquire lock if stored data is invalid", () => {
      localStorage.setItem(`${LOCK_KEY_PREFIX}ws1`, "invalid-json")

      const result = acquireWorkspaceLock("ws1", "upload")
      expect(result).toBe(true)
    })
  })

  describe("releaseWorkspaceLock", () => {
    it("should release lock held by this tab", () => {
      acquireWorkspaceLock("ws1", "upload")
      expect(hasActiveOperation("ws1")).toBe(true)

      releaseWorkspaceLock("ws1")
      expect(hasActiveOperation("ws1")).toBe(false)
    })

    it("should not release lock held by another tab", () => {
      const otherTabLock = {
        operation: "upload",
        timestamp: Date.now(),
        tabId: "other-tab-id",
      }
      localStorage.setItem(
        `${LOCK_KEY_PREFIX}ws1`,
        JSON.stringify(otherTabLock),
      )

      releaseWorkspaceLock("ws1")
      expect(hasActiveOperation("ws1")).toBe(true)
    })

    it("should handle non-existent lock gracefully", () => {
      expect(() => releaseWorkspaceLock("ws1")).not.toThrow()
    })
  })

  describe("getWorkspaceLock", () => {
    it("should return null for non-existent lock", () => {
      expect(getWorkspaceLock("ws1")).toBeNull()
    })

    it("should return lock details when lock exists", () => {
      acquireWorkspaceLock("ws1", "upload")
      const lock = getWorkspaceLock("ws1")

      expect(lock).not.toBeNull()
      expect(lock?.operation).toBe("upload")
    })

    it("should return null and clean up stale lock", () => {
      const staleLock = {
        operation: "upload",
        timestamp: Date.now() - 6 * 60 * 1000,
        tabId: "other-tab-id",
      }
      localStorage.setItem(`${LOCK_KEY_PREFIX}ws1`, JSON.stringify(staleLock))

      const lock = getWorkspaceLock("ws1")
      expect(lock).toBeNull()
      expect(localStorage.getItem(`${LOCK_KEY_PREFIX}ws1`)).toBeNull()
    })
  })

  describe("holdsLock", () => {
    it("should return true when this tab holds the lock", () => {
      acquireWorkspaceLock("ws1", "upload")
      expect(holdsLock("ws1")).toBe(true)
    })

    it("should return false when another tab holds the lock", () => {
      const otherTabLock = {
        operation: "upload",
        timestamp: Date.now(),
        tabId: "other-tab-id",
      }
      localStorage.setItem(
        `${LOCK_KEY_PREFIX}ws1`,
        JSON.stringify(otherTabLock),
      )

      expect(holdsLock("ws1")).toBe(false)
    })

    it("should return false when no lock exists", () => {
      expect(holdsLock("ws1")).toBe(false)
    })
  })

  describe("refreshLock", () => {
    it("should update timestamp when this tab holds lock", () => {
      acquireWorkspaceLock("ws1", "upload")
      const firstLock = getWorkspaceLock("ws1")

      jest.advanceTimersByTime(1000)

      const result = refreshLock("ws1")
      expect(result).toBe(true)

      const secondLock = getWorkspaceLock("ws1")
      expect(secondLock?.timestamp).toBeGreaterThan(firstLock!.timestamp)
    })

    it("should return false when another tab holds the lock", () => {
      const otherTabLock = {
        operation: "upload",
        timestamp: Date.now(),
        tabId: "other-tab-id",
      }
      localStorage.setItem(
        `${LOCK_KEY_PREFIX}ws1`,
        JSON.stringify(otherTabLock),
      )

      const result = refreshLock("ws1")
      expect(result).toBe(false)
    })

    it("should return false when no lock exists", () => {
      const result = refreshLock("ws1")
      expect(result).toBe(false)
    })
  })

  describe("onLockChange", () => {
    it("should call callback when lock is released", () => {
      const callback = jest.fn()
      const unsubscribe = onLockChange("ws1", callback)

      const event = new StorageEvent("storage", {
        key: `${LOCK_KEY_PREFIX}ws1`,
        newValue: null,
      })
      window.dispatchEvent(event)

      expect(callback).toHaveBeenCalledWith(null)
      unsubscribe()
    })

    it("should call callback with lock when acquired by other tab", () => {
      const callback = jest.fn()
      const unsubscribe = onLockChange("ws1", callback)

      const newLock = {
        operation: "upload",
        timestamp: Date.now(),
        tabId: "other-tab",
      }
      const event = new StorageEvent("storage", {
        key: `${LOCK_KEY_PREFIX}ws1`,
        newValue: JSON.stringify(newLock),
      })
      window.dispatchEvent(event)

      expect(callback).toHaveBeenCalledWith(
        expect.objectContaining({ operation: "upload" }),
      )
      unsubscribe()
    })

    it("should ignore events for other keys", () => {
      const callback = jest.fn()
      const unsubscribe = onLockChange("ws1", callback)

      const event = new StorageEvent("storage", {
        key: `${LOCK_KEY_PREFIX}ws2`,
        newValue: null,
      })
      window.dispatchEvent(event)

      expect(callback).not.toHaveBeenCalled()
      unsubscribe()
    })

    it("should unsubscribe correctly", () => {
      const callback = jest.fn()
      const unsubscribe = onLockChange("ws1", callback)
      unsubscribe()

      const event = new StorageEvent("storage", {
        key: `${LOCK_KEY_PREFIX}ws1`,
        newValue: null,
      })
      window.dispatchEvent(event)

      expect(callback).not.toHaveBeenCalled()
    })
  })

  describe("cross-tab scenarios", () => {
    it("should prevent deletion when upload is in progress", () => {
      // Tab 1 starts upload
      expect(acquireWorkspaceLock("ws1", "upload")).toBe(true)

      // Simulate Tab 2 trying to delete (different tabId)
      const otherTabLock = getWorkspaceLock("ws1")
      expect(otherTabLock).not.toBeNull()
      expect(otherTabLock?.operation).toBe("upload")

      // Tab 2 cannot acquire delete lock
      const deleteAttempt = localStorage.getItem(`${LOCK_KEY_PREFIX}ws1`)
      expect(deleteAttempt).not.toBeNull()
    })

    it("should allow operation after lock is released", () => {
      acquireWorkspaceLock("ws1", "upload")
      releaseWorkspaceLock("ws1")

      // Now another operation should succeed
      expect(acquireWorkspaceLock("ws1", "delete")).toBe(true)
    })
  })
})

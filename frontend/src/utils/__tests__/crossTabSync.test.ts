import { describe, it, expect, beforeEach, jest } from "@jest/globals"

import {
  syncActivityAcrossTabs,
  getLastActivityFromAnyTab,
  onActivityFromOtherTab,
} from "utils/crossTabSync"

// Note: CrossTabLeaderElection uses timers and is tested separately
// These tests focus on the activity sync functions which are simpler to test

describe("Activity Sync Functions", () => {
  let mockStorage: Record<string, string>
  let originalLocalStorage: Storage

  beforeEach(() => {
    mockStorage = {}
    originalLocalStorage = window.localStorage

    const localStorageMock = {
      getItem: (key: string) => mockStorage[key] || null,
      setItem: (key: string, value: string) => {
        mockStorage[key] = value
      },
      removeItem: (key: string) => {
        delete mockStorage[key]
      },
      clear: () => {
        mockStorage = {}
      },
      length: 0,
      key: () => null,
    }

    Object.defineProperty(window, "localStorage", {
      value: localStorageMock,
      writable: true,
    })
  })

  describe("syncActivityAcrossTabs", () => {
    it("should store timestamp in localStorage", () => {
      const timestamp = 1234567890
      syncActivityAcrossTabs(timestamp)

      expect(mockStorage["premium_last_activity"]).toBe(timestamp.toString())
    })

    it("should overwrite previous timestamp", () => {
      syncActivityAcrossTabs(1000)
      syncActivityAcrossTabs(2000)

      expect(mockStorage["premium_last_activity"]).toBe("2000")
    })
  })

  describe("getLastActivityFromAnyTab", () => {
    it("should return 0 when no activity stored", () => {
      const result = getLastActivityFromAnyTab()
      expect(result).toBe(0)
    })

    it("should return stored timestamp", () => {
      mockStorage["premium_last_activity"] = "1234567890"

      const result = getLastActivityFromAnyTab()
      expect(result).toBe(1234567890)
    })

    it("should parse timestamp as integer", () => {
      mockStorage["premium_last_activity"] = "9999999999"

      const result = getLastActivityFromAnyTab()
      expect(typeof result).toBe("number")
      expect(result).toBe(9999999999)
    })
  })

  describe("onActivityFromOtherTab", () => {
    it("should return unsubscribe function", () => {
      const callback = jest.fn()
      const unsubscribe = onActivityFromOtherTab(callback)

      expect(typeof unsubscribe).toBe("function")
      unsubscribe()
    })

    it("should call callback when storage event fires with activity key", () => {
      const callback = jest.fn()
      const unsubscribe = onActivityFromOtherTab(callback)

      const timestamp = 1234567890
      const event = new StorageEvent("storage", {
        key: "premium_last_activity",
        newValue: timestamp.toString(),
      })
      window.dispatchEvent(event)

      expect(callback).toHaveBeenCalledWith(timestamp)

      unsubscribe()
    })

    it("should not call callback for other storage keys", () => {
      const callback = jest.fn()
      const unsubscribe = onActivityFromOtherTab(callback)

      const event = new StorageEvent("storage", {
        key: "some_other_key",
        newValue: "some_value",
      })
      window.dispatchEvent(event)

      expect(callback).not.toHaveBeenCalled()

      unsubscribe()
    })

    it("should not call callback after unsubscribe", () => {
      const callback = jest.fn()
      const unsubscribe = onActivityFromOtherTab(callback)

      unsubscribe()

      const event = new StorageEvent("storage", {
        key: "premium_last_activity",
        newValue: "1234567890",
      })
      window.dispatchEvent(event)

      expect(callback).not.toHaveBeenCalled()
    })

    it("should not call callback when newValue is null", () => {
      const callback = jest.fn()
      const unsubscribe = onActivityFromOtherTab(callback)

      const event = new StorageEvent("storage", {
        key: "premium_last_activity",
        newValue: null,
      })
      window.dispatchEvent(event)

      expect(callback).not.toHaveBeenCalled()

      unsubscribe()
    })
  })
})

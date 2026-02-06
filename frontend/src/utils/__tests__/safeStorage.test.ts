import { safeLocalStorage } from "utils/safeStorage"

describe("safeLocalStorage", () => {
  beforeEach(() => {
    localStorage.clear()
    jest.clearAllMocks()
  })

  describe("getItem", () => {
    it("should return stored value", () => {
      localStorage.setItem("test", "value")
      expect(safeLocalStorage.getItem("test")).toBe("value")
    })

    it("should return null for non-existent key", () => {
      expect(safeLocalStorage.getItem("nonexistent")).toBeNull()
    })

    it("should return null on error", () => {
      const mockGetItem = jest.spyOn(Storage.prototype, "getItem")
      mockGetItem.mockImplementation(() => {
        throw new Error("Storage error")
      })

      expect(safeLocalStorage.getItem("test")).toBeNull()
      mockGetItem.mockRestore()
    })
  })

  describe("setItem", () => {
    it("should store value and return true", () => {
      expect(safeLocalStorage.setItem("test", "value")).toBe(true)
      expect(localStorage.getItem("test")).toBe("value")
    })

    it("should return false on non-quota error", () => {
      const mockSetItem = jest.spyOn(Storage.prototype, "setItem")
      mockSetItem.mockImplementation(() => {
        throw new Error("Storage error")
      })

      expect(safeLocalStorage.setItem("test", "value")).toBe(false)
      mockSetItem.mockRestore()
    })

    it("should attempt cleanup on QuotaExceededError", () => {
      const clearOldDataSpy = jest.spyOn(safeLocalStorage, "clearOldData")
      let callCount = 0

      const mockSetItem = jest.spyOn(Storage.prototype, "setItem")
      mockSetItem.mockImplementation(() => {
        callCount++
        if (callCount === 1) {
          const error = new DOMException("Quota exceeded", "QuotaExceededError")
          throw error
        }
      })

      safeLocalStorage.setItem("test", "value")

      expect(clearOldDataSpy).toHaveBeenCalled()
      mockSetItem.mockRestore()
    })
  })

  describe("removeItem", () => {
    it("should remove stored value", () => {
      localStorage.setItem("test", "value")
      safeLocalStorage.removeItem("test")
      expect(localStorage.getItem("test")).toBeNull()
    })

    it("should not throw on error", () => {
      const mockRemoveItem = jest.spyOn(Storage.prototype, "removeItem")
      mockRemoveItem.mockImplementation(() => {
        throw new Error("Storage error")
      })

      expect(() => safeLocalStorage.removeItem("test")).not.toThrow()
      mockRemoveItem.mockRestore()
    })
  })

  describe("getJSON", () => {
    it("should return parsed JSON value", () => {
      localStorage.setItem("test", JSON.stringify({ foo: "bar" }))
      expect(safeLocalStorage.getJSON("test", {})).toEqual({ foo: "bar" })
    })

    it("should return default value for non-existent key", () => {
      expect(
        safeLocalStorage.getJSON("nonexistent", { default: true }),
      ).toEqual({ default: true })
    })

    it("should return default value on parse error", () => {
      localStorage.setItem("test", "invalid json")
      expect(safeLocalStorage.getJSON("test", { default: true })).toEqual({
        default: true,
      })
    })
  })

  describe("setJSON", () => {
    it("should store JSON-stringified value", () => {
      expect(safeLocalStorage.setJSON("test", { foo: "bar" })).toBe(true)
      expect(localStorage.getItem("test")).toBe("{\"foo\":\"bar\"}")
    })

    it("should return false on circular reference", () => {
      const circular: Record<string, unknown> = {}
      circular.self = circular

      expect(safeLocalStorage.setJSON("test", circular)).toBe(false)
    })
  })

  describe("clearOldTimestampedEntries", () => {
    it("should remove entries older than one week", () => {
      const oneWeekAgo = Date.now() - 8 * 24 * 60 * 60 * 1000
      const recent = Date.now() - 1 * 24 * 60 * 60 * 1000

      localStorage.setItem(
        "dismissedAlerts",
        JSON.stringify({
          old: oneWeekAgo,
          recent: recent,
          notATimestamp: "string value",
        }),
      )

      safeLocalStorage.clearOldTimestampedEntries("dismissedAlerts")

      const result = safeLocalStorage.getJSON<Record<string, unknown>>(
        "dismissedAlerts",
        {},
      )
      expect(result).not.toHaveProperty("old")
      expect(result).toHaveProperty("recent")
      expect(result).toHaveProperty("notATimestamp")
    })

    it("should handle empty object", () => {
      localStorage.setItem("dismissedAlerts", JSON.stringify({}))
      expect(() =>
        safeLocalStorage.clearOldTimestampedEntries("dismissedAlerts"),
      ).not.toThrow()
    })

    it("should handle missing key", () => {
      expect(() =>
        safeLocalStorage.clearOldTimestampedEntries("nonexistent"),
      ).not.toThrow()
    })
  })
})

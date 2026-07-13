/**
 * Safe localStorage wrapper with error handling and quota management.
 */

const ONE_WEEK_MS = 7 * 24 * 60 * 60 * 1000

/**
 * Keys that can be cleared when quota is exceeded.
 * These should be non-essential cached data that can be regenerated.
 */
const CLEARABLE_KEYS = ["dismissedAlerts", "storageAlertDismissed"] as const

export const safeLocalStorage = {
  /**
   * Safely get an item from localStorage.
   * Returns null on any error.
   */
  getItem(key: string): string | null {
    try {
      return localStorage.getItem(key)
    } catch {
      return null
    }
  },

  /**
   * Safely set an item in localStorage.
   * Returns true on success, false on failure.
   * Attempts to clear old data if quota is exceeded.
   */
  setItem(key: string, value: string): boolean {
    try {
      localStorage.setItem(key, value)
      return true
    } catch (e) {
      if (e instanceof DOMException && e.name === "QuotaExceededError") {
        console.warn("localStorage quota exceeded, attempting cleanup")
        this.clearOldData()

        try {
          localStorage.setItem(key, value)
          return true
        } catch {
          return false
        }
      }
      return false
    }
  },

  /**
   * Safely remove an item from localStorage.
   */
  removeItem(key: string): void {
    try {
      localStorage.removeItem(key)
    } catch {
      // Ignore errors on removal
    }
  },

  /**
   * Get a JSON-parsed value from localStorage.
   * Returns defaultValue on any error or if key doesn't exist.
   */
  getJSON<T>(key: string, defaultValue: T): T {
    const raw = this.getItem(key)
    if (raw === null) return defaultValue

    try {
      return JSON.parse(raw) as T
    } catch {
      return defaultValue
    }
  },

  /**
   * Set a JSON-stringified value in localStorage.
   * Returns true on success, false on failure.
   */
  setJSON<T>(key: string, value: T): boolean {
    try {
      return this.setItem(key, JSON.stringify(value))
    } catch {
      return false
    }
  },

  /**
   * Clear old non-essential cached data.
   * Called automatically when quota is exceeded.
   */
  clearOldData(): void {
    for (const key of CLEARABLE_KEYS) {
      this.clearOldTimestampedEntries(key)
    }
  },

  /**
   * Clear entries older than one week from a timestamped object.
   */
  clearOldTimestampedEntries(key: string): void {
    const data = this.getJSON<Record<string, unknown>>(key, {})
    const oneWeekAgo = Date.now() - ONE_WEEK_MS

    const filtered: Record<string, unknown> = {}
    let hasChanges = false

    for (const [entryKey, value] of Object.entries(data)) {
      if (typeof value === "number" && value < oneWeekAgo) {
        hasChanges = true
        continue
      }
      filtered[entryKey] = value
    }

    if (hasChanges) {
      this.setJSON(key, filtered)
    }
  },
}

export default safeLocalStorage

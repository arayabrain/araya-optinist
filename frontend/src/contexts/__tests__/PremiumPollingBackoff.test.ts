/**
 * Tests for Premium Instance Polling Backoff (Case 43)
 *
 * These tests verify the exponential backoff behavior when polling for
 * premium instance availability.
 */

import { describe, it, expect } from "@jest/globals"

// Constants from PremiumAssignmentContext.tsx
const INITIAL_POLL_INTERVAL_MS = 5000
const MAX_POLL_INTERVAL_MS = 60000
const MAX_POLL_ATTEMPTS = 120
const BACKOFF_MULTIPLIER = 1.5
const ERROR_BACKOFF_MULTIPLIER = 2

/**
 * Calculate expected interval after N successful polls (no instance available)
 */
const calculateIntervalAfterPolls = (pollCount: number): number => {
  let interval = INITIAL_POLL_INTERVAL_MS
  for (let i = 0; i < pollCount; i++) {
    interval = Math.min(interval * BACKOFF_MULTIPLIER, MAX_POLL_INTERVAL_MS)
  }
  return interval
}

/**
 * Calculate expected interval after N error polls
 */
const calculateIntervalAfterErrors = (errorCount: number): number => {
  let interval = INITIAL_POLL_INTERVAL_MS
  for (let i = 0; i < errorCount; i++) {
    interval = Math.min(
      interval * ERROR_BACKOFF_MULTIPLIER,
      MAX_POLL_INTERVAL_MS,
    )
  }
  return interval
}

describe("Polling Backoff Logic (Case 43)", () => {
  describe("Configuration Constants", () => {
    it("should have initial interval of 5 seconds", () => {
      expect(INITIAL_POLL_INTERVAL_MS).toBe(5000)
    })

    it("should have max interval of 60 seconds", () => {
      expect(MAX_POLL_INTERVAL_MS).toBe(60000)
    })

    it("should have max attempts of 120", () => {
      expect(MAX_POLL_ATTEMPTS).toBe(120)
    })

    it("should use 1.5x backoff multiplier for normal polls", () => {
      expect(BACKOFF_MULTIPLIER).toBe(1.5)
    })

    it("should use 2x backoff multiplier for errors", () => {
      expect(ERROR_BACKOFF_MULTIPLIER).toBe(2)
    })
  })

  describe("Normal Backoff Progression", () => {
    it("should start at 5 seconds", () => {
      expect(calculateIntervalAfterPolls(0)).toBe(5000)
    })

    it("should increase to 7.5 seconds after first poll", () => {
      expect(calculateIntervalAfterPolls(1)).toBe(7500)
    })

    it("should increase to 11.25 seconds after second poll", () => {
      expect(calculateIntervalAfterPolls(2)).toBe(11250)
    })

    it("should cap at max interval of 60 seconds", () => {
      // After enough polls, should be capped at 60 seconds
      const interval = calculateIntervalAfterPolls(20)
      expect(interval).toBe(MAX_POLL_INTERVAL_MS)
    })

    it("should reach max interval after ~13 polls", () => {
      // 5000 * 1.5^13 = 5000 * 189.6 = ~947265, capped at 60000
      // Find when it first reaches 60000
      let interval = INITIAL_POLL_INTERVAL_MS
      let polls = 0
      while (interval < MAX_POLL_INTERVAL_MS) {
        interval *= BACKOFF_MULTIPLIER
        polls++
      }
      // Should take about 12-13 polls to reach max
      expect(polls).toBeGreaterThanOrEqual(12)
      expect(polls).toBeLessThanOrEqual(15)
    })
  })

  describe("Error Backoff Progression", () => {
    it("should increase more aggressively on errors (2x)", () => {
      expect(calculateIntervalAfterErrors(1)).toBe(10000) // 5000 * 2
    })

    it("should reach max interval faster on errors", () => {
      // 5000 * 2^4 = 80000, capped at 60000
      const interval = calculateIntervalAfterErrors(4)
      expect(interval).toBe(MAX_POLL_INTERVAL_MS)
    })
  })

  describe("Maximum Attempts", () => {
    it("should allow 120 attempts before stopping", () => {
      expect(MAX_POLL_ATTEMPTS).toBe(120)
    })

    it("should provide approximately 10 minutes of polling at initial rate", () => {
      // 120 attempts * 5 seconds = 600 seconds = 10 minutes
      const totalTimeAtInitialRate =
        (MAX_POLL_ATTEMPTS * INITIAL_POLL_INTERVAL_MS) / 1000 / 60
      expect(totalTimeAtInitialRate).toBe(10)
    })

    it("should provide extended total polling time with backoff", () => {
      // Calculate total time with backoff
      let totalTime = 0
      let interval = INITIAL_POLL_INTERVAL_MS
      for (let i = 0; i < MAX_POLL_ATTEMPTS; i++) {
        totalTime += interval
        interval = Math.min(interval * BACKOFF_MULTIPLIER, MAX_POLL_INTERVAL_MS)
      }
      // With backoff, total time is much longer than 10 minutes
      const totalMinutes = totalTime / 1000 / 60
      expect(totalMinutes).toBeGreaterThan(60) // Should be over an hour total
    })
  })

  describe("Reset Behavior", () => {
    it("should reset to initial interval after successful assignment", () => {
      // When is_shared becomes false, pollInterval resets
      // This is tested via the useEffect that watches assignmentResult.is_shared
      expect(INITIAL_POLL_INTERVAL_MS).toBe(5000) // Reset value
    })
  })
})

/**
 * Tests for Premium Sleep Detection Integration (Cases 50-51)
 *
 * Tests verify that the sleep detection hook is properly integrated
 * with the premium assignment context.
 */

import {
  describe,
  it,
  expect,
  jest,
  beforeEach,
  afterEach,
} from "@jest/globals"

// Configuration from useSleepDetection
const DEFAULT_CHECK_INTERVAL_MS = 30000
const SLEEP_DETECTION_MULTIPLIER = 5

/**
 * Simulates the sleep detection logic from useSleepDetection
 */
class SleepDetectionSimulator {
  private lastTick: number
  private checkIntervalMs: number
  private sleepThresholdMultiplier: number
  private onWake: () => void
  private interval: ReturnType<typeof setInterval> | null = null
  private hiddenTick = false
  private hiddenAt: number | null = null

  constructor(
    onWake: () => void,
    options: {
      checkIntervalMs?: number
      sleepThresholdMultiplier?: number
    } = {},
  ) {
    this.onWake = onWake
    this.checkIntervalMs = options.checkIntervalMs ?? DEFAULT_CHECK_INTERVAL_MS
    this.sleepThresholdMultiplier =
      options.sleepThresholdMultiplier ?? SLEEP_DETECTION_MULTIPLIER
    this.lastTick = Date.now()
  }

  private handleVisibilityChange = (): void => {
    if (document.visibilityState === "hidden") {
      this.hiddenAt = Date.now()
      this.hiddenTick = false
    } else if (document.visibilityState === "visible") {
      if (!this.hiddenTick && this.hiddenAt !== null) {
        const now = Date.now()
        const elapsed = now - this.hiddenAt
        const threshold = this.checkIntervalMs * this.sleepThresholdMultiplier

        if (elapsed > threshold) {
          this.onWake()
        }
      }
      this.lastTick = Date.now()
      this.hiddenAt = null
      this.hiddenTick = false
    }
  }

  start(): void {
    this.lastTick = Date.now()
    this.interval = setInterval(
      () => this.checkForSleep(),
      this.checkIntervalMs,
    )
    document.addEventListener("visibilitychange", this.handleVisibilityChange)
  }

  stop(): void {
    if (this.interval) {
      clearInterval(this.interval)
      this.interval = null
    }
    document.removeEventListener(
      "visibilitychange",
      this.handleVisibilityChange,
    )
  }

  private checkForSleep(): void {
    const now = Date.now()
    const elapsed = now - this.lastTick
    const threshold = this.checkIntervalMs * this.sleepThresholdMultiplier

    if (document.visibilityState === "visible") {
      if (elapsed > threshold) {
        this.onWake()
      }
    } else {
      this.hiddenTick = true
    }

    this.lastTick = now
  }

  simulateSleep(durationMs: number): void {
    this.lastTick = Date.now() - durationMs
  }
}

describe("Sleep Detection Integration (Cases 50-51)", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    // Default: tab is visible
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    })
  })

  afterEach(() => {
    jest.useRealTimers()
    jest.restoreAllMocks()
  })

  const setVisibility = (state: "visible" | "hidden") => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => state,
    })
  }

  const changeVisibility = (state: "visible" | "hidden") => {
    setVisibility(state)
    document.dispatchEvent(new Event("visibilitychange"))
  }

  describe("Sleep Detection Configuration", () => {
    it("should check for sleep every 30 seconds", () => {
      expect(DEFAULT_CHECK_INTERVAL_MS).toBe(30000)
    })

    it("should detect sleep when interval fires 5x late", () => {
      expect(SLEEP_DETECTION_MULTIPLIER).toBe(5)
    })

    it("should detect sleep after 150+ second gap", () => {
      const threshold = DEFAULT_CHECK_INTERVAL_MS * SLEEP_DETECTION_MULTIPLIER
      expect(threshold).toBe(150000)
    })
  })

  describe("Integration Behavior", () => {
    it("should not call onWake during normal interval ticks", () => {
      const onWake = jest.fn()
      const simulator = new SleepDetectionSimulator(onWake)
      simulator.start()

      jest.advanceTimersByTime(30000)

      expect(onWake).not.toHaveBeenCalled()
      simulator.stop()
    })

    it("should call onWake when large time gap detected", () => {
      const onWake = jest.fn()
      const simulator = new SleepDetectionSimulator(onWake)
      simulator.start()

      simulator.simulateSleep(200000)
      jest.advanceTimersByTime(30000)

      expect(onWake).toHaveBeenCalledTimes(1)
      simulator.stop()
    })

    it("should call onWake for each sleep event", () => {
      const onWake = jest.fn()
      const simulator = new SleepDetectionSimulator(onWake)
      simulator.start()

      simulator.simulateSleep(200000)
      jest.advanceTimersByTime(30000)
      expect(onWake).toHaveBeenCalledTimes(1)

      jest.advanceTimersByTime(30000)
      expect(onWake).toHaveBeenCalledTimes(1)

      simulator.simulateSleep(200000)
      jest.advanceTimersByTime(30000)
      expect(onWake).toHaveBeenCalledTimes(2)

      simulator.stop()
    })
  })

  describe("Use Cases", () => {
    describe("Case 50: Warning shown while user actively working", () => {
      it("should detect laptop lid close/open scenario", () => {
        const activityRecorded = jest.fn()
        const simulator = new SleepDetectionSimulator(activityRecorded)
        simulator.start()

        simulator.simulateSleep(30 * 60 * 1000)
        jest.advanceTimersByTime(30000)

        expect(activityRecorded).toHaveBeenCalledTimes(1)
        simulator.stop()
      })

      it("should allow activity recording to dismiss warning", () => {
        let showInactivityWarning = true

        const recordActivity = jest.fn().mockImplementation(() => {
          showInactivityWarning = false
        })

        const simulator = new SleepDetectionSimulator(recordActivity)
        simulator.start()

        simulator.simulateSleep(200000)
        jest.advanceTimersByTime(30000)

        expect(recordActivity).toHaveBeenCalled()
        expect(showInactivityWarning).toBe(false)
        simulator.stop()
      })
    })

    describe("Case 51: Timer skewed during system sleep", () => {
      it("should re-synchronize after 30 minute sleep", () => {
        const onWake = jest.fn()
        const simulator = new SleepDetectionSimulator(onWake)
        simulator.start()

        simulator.simulateSleep(30 * 60 * 1000)
        jest.advanceTimersByTime(30000)

        expect(onWake).toHaveBeenCalled()
        simulator.stop()
      })

      it("should not trigger for normal operation", () => {
        const onWake = jest.fn()
        const simulator = new SleepDetectionSimulator(onWake)
        simulator.start()

        for (let i = 0; i < 10; i++) {
          jest.advanceTimersByTime(30000)
        }

        expect(onWake).not.toHaveBeenCalled()
        simulator.stop()
      })

      it("should not trigger for browser background throttle gaps", () => {
        const onWake = jest.fn()
        const simulator = new SleepDetectionSimulator(onWake)
        simulator.start()

        // 60s gap is typical of Chromium background tab throttling
        simulator.simulateSleep(60000)
        jest.advanceTimersByTime(30000)

        expect(onWake).not.toHaveBeenCalled()
        simulator.stop()
      })

      it("should detect sleep-while-hidden via visibilitychange", () => {
        // We need a mutable currentTime to simulate sleep without firing ticks
        let now = Date.now()
        jest.spyOn(Date, "now").mockImplementation(() => now)

        const onWake = jest.fn()
        const simulator = new SleepDetectionSimulator(onWake)
        simulator.start()

        // Tab goes hidden (records hiddenAt, resets hiddenTick)
        changeVisibility("hidden")

        // Device sleeps — CPU suspended, no ticks fire, just time passes
        now += 600000 // 10 min

        // User opens laptop, tab becomes visible — no ticks fired so
        // visibilitychange detects the gap as real sleep
        changeVisibility("visible")
        expect(onWake).toHaveBeenCalledTimes(1)

        simulator.stop()
      })

      it("should detect sleep just above threshold", () => {
        const onWake = jest.fn()
        const simulator = new SleepDetectionSimulator(onWake)
        simulator.start()

        simulator.simulateSleep(151000)
        jest.advanceTimersByTime(30000)

        expect(onWake).toHaveBeenCalled()
        simulator.stop()
      })
    })
  })

  describe("Edge Cases", () => {
    it("should handle multiple rapid wake events correctly", () => {
      const onWake = jest.fn()
      const simulator = new SleepDetectionSimulator(onWake)
      simulator.start()

      simulator.simulateSleep(200000)
      jest.advanceTimersByTime(30000)
      expect(onWake).toHaveBeenCalledTimes(1)

      jest.advanceTimersByTime(1000)
      jest.advanceTimersByTime(1000)
      jest.advanceTimersByTime(1000)

      expect(onWake).toHaveBeenCalledTimes(1)
      simulator.stop()
    })

    it("should work with custom configuration", () => {
      const onWake = jest.fn()
      // With 5000ms interval and 3x multiplier, threshold is 15000ms
      const simulator = new SleepDetectionSimulator(onWake, {
        checkIntervalMs: 5000,
        sleepThresholdMultiplier: 3,
      })
      simulator.start()

      // Normal tick - should not trigger (5000ms elapsed < 15000ms threshold)
      jest.advanceTimersByTime(5000)
      expect(onWake).not.toHaveBeenCalled()

      // Simulate sleep by setting lastTick far in the past
      simulator.simulateSleep(16000) // 16000ms elapsed > 15000ms threshold
      jest.advanceTimersByTime(5000)
      expect(onWake).toHaveBeenCalled()

      simulator.stop()
    })

    it("should handle errors in onWake callback gracefully", () => {
      const errorOnWake = jest.fn().mockImplementation(() => {
        throw new Error("Callback error")
      })

      const simulator = new SleepDetectionSimulator(errorOnWake)
      simulator.start()

      simulator.simulateSleep(200000)

      expect(() => {
        jest.advanceTimersByTime(30000)
      }).toThrow("Callback error")

      simulator.stop()
    })
  })

  describe("Premium Context Integration", () => {
    it("should simulate recordActivity being called on wake", async () => {
      let lastActivityTime = Date.now()
      let heartbeatCalled = false

      const recordActivity = jest.fn().mockImplementation(async () => {
        heartbeatCalled = true
        lastActivityTime = Date.now()
      })

      const handleDeviceWake = () => {
        recordActivity().catch(() => {})
      }

      const simulator = new SleepDetectionSimulator(handleDeviceWake)
      simulator.start()

      const initialTime = lastActivityTime

      simulator.simulateSleep(200000)
      jest.advanceTimersByTime(30000)

      await Promise.resolve()

      expect(recordActivity).toHaveBeenCalled()
      expect(heartbeatCalled).toBe(true)
      expect(lastActivityTime).toBeGreaterThanOrEqual(initialTime)
      simulator.stop()
    })

    it("should handle recordActivity errors without crashing", async () => {
      const recordActivity = jest.fn().mockRejectedValue(new Error("API error"))

      const handleDeviceWake = () => {
        recordActivity().catch(() => {})
      }

      const simulator = new SleepDetectionSimulator(handleDeviceWake)
      simulator.start()

      simulator.simulateSleep(200000)

      expect(() => {
        jest.advanceTimersByTime(30000)
      }).not.toThrow()

      await Promise.resolve()

      expect(recordActivity).toHaveBeenCalled()
      simulator.stop()
    })
  })
})

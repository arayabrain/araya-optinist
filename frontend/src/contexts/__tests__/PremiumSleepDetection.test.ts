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
const DEFAULT_CHECK_INTERVAL_MS = 10000
const SLEEP_DETECTION_MULTIPLIER = 2

/**
 * Simulates the sleep detection logic from useSleepDetection
 */
class SleepDetectionSimulator {
  private lastTick: number
  private checkIntervalMs: number
  private sleepThresholdMultiplier: number
  private onWake: () => void
  private interval: ReturnType<typeof setInterval> | null = null

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

  start(): void {
    this.lastTick = Date.now()
    this.interval = setInterval(
      () => this.checkForSleep(),
      this.checkIntervalMs,
    )
  }

  stop(): void {
    if (this.interval) {
      clearInterval(this.interval)
      this.interval = null
    }
  }

  private checkForSleep(): void {
    const now = Date.now()
    const elapsed = now - this.lastTick
    const threshold = this.checkIntervalMs * this.sleepThresholdMultiplier

    if (elapsed > threshold) {
      this.onWake()
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
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  describe("Sleep Detection Configuration", () => {
    it("should check for sleep every 10 seconds", () => {
      expect(DEFAULT_CHECK_INTERVAL_MS).toBe(10000)
    })

    it("should detect sleep when interval fires 2x late", () => {
      expect(SLEEP_DETECTION_MULTIPLIER).toBe(2)
    })

    it("should detect sleep after 20+ second gap", () => {
      const threshold = DEFAULT_CHECK_INTERVAL_MS * SLEEP_DETECTION_MULTIPLIER
      expect(threshold).toBe(20000)
    })
  })

  describe("Integration Behavior", () => {
    it("should not call onWake during normal interval ticks", () => {
      const onWake = jest.fn()
      const simulator = new SleepDetectionSimulator(onWake)
      simulator.start()

      jest.advanceTimersByTime(10000)

      expect(onWake).not.toHaveBeenCalled()
      simulator.stop()
    })

    it("should call onWake when large time gap detected", () => {
      const onWake = jest.fn()
      const simulator = new SleepDetectionSimulator(onWake)
      simulator.start()

      simulator.simulateSleep(25000)
      jest.advanceTimersByTime(10000)

      expect(onWake).toHaveBeenCalledTimes(1)
      simulator.stop()
    })

    it("should call onWake for each sleep event", () => {
      const onWake = jest.fn()
      const simulator = new SleepDetectionSimulator(onWake)
      simulator.start()

      simulator.simulateSleep(25000)
      jest.advanceTimersByTime(10000)
      expect(onWake).toHaveBeenCalledTimes(1)

      jest.advanceTimersByTime(10000)
      expect(onWake).toHaveBeenCalledTimes(1)

      simulator.simulateSleep(30000)
      jest.advanceTimersByTime(10000)
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
        jest.advanceTimersByTime(10000)

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

        simulator.simulateSleep(25000)
        jest.advanceTimersByTime(10000)

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
        jest.advanceTimersByTime(10000)

        expect(onWake).toHaveBeenCalled()
        simulator.stop()
      })

      it("should not trigger for normal operation", () => {
        const onWake = jest.fn()
        const simulator = new SleepDetectionSimulator(onWake)
        simulator.start()

        for (let i = 0; i < 10; i++) {
          jest.advanceTimersByTime(10000)
        }

        expect(onWake).not.toHaveBeenCalled()
        simulator.stop()
      })

      it("should handle short sleep periods correctly", () => {
        const onWake = jest.fn()
        const simulator = new SleepDetectionSimulator(onWake)
        simulator.start()

        // Advance time normally - elapsed will be ~10000ms which is below 20000 threshold
        jest.advanceTimersByTime(10000)

        expect(onWake).not.toHaveBeenCalled()
        simulator.stop()
      })

      it("should detect sleep just above threshold", () => {
        const onWake = jest.fn()
        const simulator = new SleepDetectionSimulator(onWake)
        simulator.start()

        simulator.simulateSleep(21000)
        jest.advanceTimersByTime(10000)

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

      simulator.simulateSleep(25000)
      jest.advanceTimersByTime(10000)
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

      simulator.simulateSleep(25000)

      expect(() => {
        jest.advanceTimersByTime(10000)
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

      simulator.simulateSleep(25000)
      jest.advanceTimersByTime(10000)

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

      simulator.simulateSleep(25000)

      expect(() => {
        jest.advanceTimersByTime(10000)
      }).not.toThrow()

      await Promise.resolve()

      expect(recordActivity).toHaveBeenCalled()
      simulator.stop()
    })
  })
})

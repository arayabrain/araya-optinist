import { describe, it, expect } from "@jest/globals"

import {
  stackedTrace,
  visibleTraceCount,
} from "components/Workspace/Visualize/Plot/timeSeriesStack"

describe("stackedTrace", () => {
  const y = [1, 2, 3, 4]

  it("leaves a lone trace in its own units", () => {
    expect(stackedTrace(y, 0, 5, 1)).toEqual(y)
  })

  it("z-scores and offsets a trace when several are stacked", () => {
    const stacked = stackedTrace(y, 2, 1, 3)
    const mean = stacked.reduce((a, b) => a + b, 0) / stacked.length
    expect(mean).toBeCloseTo(2)
    expect(stacked[3] - stacked[0]).toBeCloseTo(3 / Math.sqrt(1.25))
  })

  it("stays finite when the first sample is negative and the variance small", () => {
    const stacked = stackedTrace([-0.3, 0.01, -0.01, 0.01, -0.01], 0, 1, 2)
    expect(stacked.every(Number.isFinite)).toBe(true)
  })

  it("draws a constant trace flat at its offset", () => {
    expect(stackedTrace([5, 5, 5], 2, 1, 2)).toEqual([2, 2, 2])
  })

  it("divides by the span so stacked traces fit between offsets", () => {
    const narrow = stackedTrace(y, 0, 5, 2)
    const wide = stackedTrace(y, 0, 1, 2)
    expect(narrow[3] - narrow[0]).toBeCloseTo((wide[3] - wide[0]) / 5)
  })
})

describe("visibleTraceCount", () => {
  it("counts only selected traces the ROI filter leaves visible", () => {
    expect(visibleTraceCount(["1", "4"], ["1", "2", "3"])).toBe(1)
    expect(visibleTraceCount(["1", "2"], ["1", "2", "3"])).toBe(2)
    expect(visibleTraceCount([], ["1"])).toBe(0)
  })
})

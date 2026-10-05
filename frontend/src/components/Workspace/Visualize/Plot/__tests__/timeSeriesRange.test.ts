import { describe, it, expect } from "@jest/globals"

import {
  errorBarArray,
  frameAxisRange,
} from "components/Workspace/Visualize/Plot/timeSeriesRange"

describe("frameAxisRange", () => {
  it("uses a fixed range when both bounds are set", () => {
    expect(frameAxisRange(-5, 20)).toEqual({ range: [-5, 20] })
  })

  it("keeps a lone Left as an autorange floor", () => {
    expect(frameAxisRange(-5, undefined)).toEqual({
      autorange: true,
      autorangeoptions: { include: -5, minallowed: -5 },
    })
  })

  it("keeps a lone Right as an autorange ceiling", () => {
    expect(frameAxisRange(undefined, 20)).toEqual({
      autorange: true,
      autorangeoptions: { include: 20, maxallowed: 20 },
    })
  })

  it("autoranges with no bounds", () => {
    expect(frameAxisRange(undefined, undefined)).toEqual({ autorange: true })
  })
})

describe("errorBarArray", () => {
  it("follows the x order, not the integer-key order of the std object", () => {
    const std = { "-2": 0.2, "-1": 0.1, "0": 0.5, "1": 0.6 }
    expect(errorBarArray(["-2", "-1", "0", "1"], std)).toEqual([
      0.2, 0.1, 0.5, 0.6,
    ])
    // Object.values would have given 0.5, 0.6, 0.2, 0.1
  })

  it("turns a null std into undefined so no bar is drawn", () => {
    expect(errorBarArray(["0", "1"], { "0": null, "1": 0.3 })).toEqual([
      undefined,
      0.3,
    ])
  })

  it("is all undefined without a std object", () => {
    expect(errorBarArray(["0", "1"], undefined)).toEqual([undefined, undefined])
  })
})

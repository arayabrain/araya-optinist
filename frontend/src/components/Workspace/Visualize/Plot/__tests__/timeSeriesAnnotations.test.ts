import { describe, it, expect } from "@jest/globals"

import { buildAnnotations } from "components/Workspace/Visualize/Plot/timeSeriesAnnotations"

const trace = (y: (number | undefined)[]) => ({ y })

describe("buildAnnotations", () => {
  it("anchors the label at the last x value, not the array length", () => {
    // ETA: pre_event -10, post_event 10, trigger_len 1 -> x values -10..10
    const xValues = Array.from({ length: 21 }, (_, i) => String(i - 10))
    const data = { "3": trace(xValues.map((_, i) => i)) }

    const [annotation] = buildAnnotations(["3"], xValues, data)

    expect(annotation.x).toBe(10)
    expect(annotation.xanchor).toBe("left")
    expect(annotation.xshift).toBe(8)
    expect(annotation.y).toBe(20)
    expect(annotation.text).toBe("cell: 3")
  })

  it("anchors at the last x value for a 0-based axis too", () => {
    const xValues = ["0", "1", "2", "3"]

    const [annotation] = buildAnnotations(["0"], xValues, {
      "0": trace([5, 6, 7, 8]),
    })

    expect(annotation.x).toBe(3)
    expect(annotation.y).toBe(8)
  })

  it("handles a single sample", () => {
    const [annotation] = buildAnnotations(["0"], ["-10"], { "0": trace([1]) })

    expect(annotation.x).toBe(-10)
    expect(annotation.y).toBe(1)
  })

  it("emits nothing when there are no x values", () => {
    expect(buildAnnotations(["0"], [], { "0": trace([]) })).toEqual([])
  })

  it("emits nothing when the last x value is not numeric", () => {
    expect(
      buildAnnotations(["0"], ["0", "label"], { "0": trace([1, 2]) }),
    ).toEqual([])
  })

  it("skips draw-order entries with no matching trace", () => {
    const annotations = buildAnnotations(["0", "7"], ["0", "1"], {
      "0": trace([1, 2]),
    })

    expect(annotations).toHaveLength(1)
    expect(annotations[0].text).toBe("cell: 0")
  })

  it("skips a trace whose last point is not yet loaded", () => {
    // A legend click puts the key in drawOrderList a render before its data arrives.
    expect(
      buildAnnotations(["0"], ["0", "1"], { "0": trace([1, undefined]) }),
    ).toEqual([])
  })

  it("skips a trace whose last point is NaN", () => {
    expect(
      buildAnnotations(["0"], ["0", "1"], { "0": trace([1, NaN]) }),
    ).toEqual([])
  })
})

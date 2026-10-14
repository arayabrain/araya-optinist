import { describe, it, expect } from "@jest/globals"

import {
  buildShadeShapes,
  eventSpans,
  SHADE_COLORS,
} from "components/Workspace/Visualize/Plot/eventSpans"

const X = [0, 1, 2, 3, 4, 5]

describe("eventSpans", () => {
  it("does not count a sample equal to the threshold as an event", () => {
    expect(eventSpans(X, [0, 0.5, 0.5, 0, 0, 0])).toEqual({
      spans: [],
      capped: false,
    })
  })

  it("ends a run in the middle at the x of the next sample", () => {
    expect(eventSpans(X, [0, 1, 1, 0, 0, 0]).spans).toEqual([[1, 3]])
  })

  it("extends a run that reaches the end by the last x step", () => {
    expect(eventSpans(X, [0, 0, 0, 0, 1, 1]).spans).toEqual([[4, 6]])
    expect(eventSpans([0, 0.5, 1], [0, 0, 1]).spans).toEqual([[1, 1.5]])
  })

  it("gives a single sample event a width of one", () => {
    expect(eventSpans([7], [1]).spans).toEqual([[7, 8]])
  })

  it("gives one span per separated run", () => {
    expect(eventSpans(X, [1, 0, 0, 1, 1, 0]).spans).toEqual([
      [0, 1],
      [3, 5],
    ])
  })

  it("returns cap spans and flags that more existed", () => {
    expect(eventSpans(X, [1, 0, 1, 0, 1, 0], 0.5, 2)).toEqual({
      spans: [
        [0, 1],
        [2, 3],
      ],
      capped: true,
    })
  })

  it("is not capped when the run count equals the cap", () => {
    expect(eventSpans(X, [1, 0, 1, 0, 0, 0], 0.5, 2).capped).toBe(false)
  })

  it("gives no spans for empty input", () => {
    expect(eventSpans([], [])).toEqual({ spans: [], capped: false })
  })
})

describe("buildShadeShapes", () => {
  const xrange = ["0", "1", "2", "3"]
  const data = {
    "5": { "0": 0, "1": 1, "2": 0, "3": 0 },
    "9": { "0": 1, "1": 0, "2": 1, "3": 0 },
  }
  const all = () => true

  it("builds one paper-height rect per event in the first palette colour", () => {
    expect(buildShadeShapes(xrange, data, ["5"], 1, SHADE_COLORS, all)).toEqual(
      {
        shapes: [
          {
            type: "rect",
            xref: "x",
            yref: "paper",
            y0: 0,
            y1: 1,
            x0: 1,
            x1: 2,
            layer: "below",
            line: { width: 0 },
            fillcolor: "rgba(128, 128, 128, 0.25)",
          },
        ],
        capped: false,
      },
    )
  })

  it("divides x by the scale for the time unit", () => {
    const { shapes } = buildShadeShapes(
      xrange,
      data,
      ["5"],
      10,
      SHADE_COLORS,
      all,
    )
    expect(shapes[0]).toMatchObject({ x0: 0.1, x1: 0.2 })
  })

  it("colours each record by its position and counts every record's events", () => {
    const { shapes } = buildShadeShapes(
      xrange,
      data,
      ["5", "9"],
      1,
      SHADE_COLORS,
      all,
    )
    expect(shapes.map((s) => (s as { fillcolor: string }).fillcolor)).toEqual([
      SHADE_COLORS[0],
      SHADE_COLORS[1],
      SHADE_COLORS[1],
    ])
  })

  it("skips records that are not loaded or have no data", () => {
    const { shapes } = buildShadeShapes(
      xrange,
      data,
      ["5", "9", "404"],
      1,
      SHADE_COLORS,
      (key) => key !== "5",
    )
    expect(shapes).toHaveLength(2)
    expect(shapes[0]).toMatchObject({ fillcolor: SHADE_COLORS[1] })
  })

  it("stops at 2000 shapes across records and flags it", () => {
    const longX = Array.from({ length: 800 }, (_, i) => String(i))
    const alternating = Object.fromEntries(
      longX.map((x, i) => [x, i % 2 === 0 ? 1 : 0]),
    )
    const keys = ["1", "2", "3", "4", "5", "6"]
    const many = Object.fromEntries(keys.map((k) => [k, alternating]))
    const result = buildShadeShapes(longX, many, keys, 1, SHADE_COLORS, all)
    expect(result.shapes).toHaveLength(2000)
    expect(result.capped).toBe(true)
  })
})

import { describe, it, expect } from "@jest/globals"

import { filterHeatMapRows } from "components/Workspace/Visualize/Plot/heatMapRows"

// ETA with iscell: rows are ROI numbers, not positions.
const z = [[0], [1], [2], [3]]
const index = ["2", "5", "7", "11"]

describe("filterHeatMapRows", () => {
  it("keeps every row when no box is linked", () => {
    expect(filterHeatMapRows(z, index, undefined)).toEqual({ z, index })
  })

  it("keeps every row when the linked box has nothing selected", () => {
    expect(filterHeatMapRows(z, index, [])).toEqual({ z, index })
  })

  it("matches selected ROIs by ROI number, not by row position", () => {
    expect(filterHeatMapRows(z, index, [7, 2])).toEqual({
      z: [[0], [2]],
      index: ["2", "7"],
    })
  })

  it("matches a numeric index as served by the split-JSON API", () => {
    const numericIndex = [2, 5, 7, 11] as unknown as string[]
    expect(filterHeatMapRows(z, numericIndex, [5]).z).toEqual([[1]])
  })

  it("draws no rows when only ROIs absent from the heatmap are selected", () => {
    expect(filterHeatMapRows(z, index, [3])).toEqual({ z: [], index: [] })
  })
})

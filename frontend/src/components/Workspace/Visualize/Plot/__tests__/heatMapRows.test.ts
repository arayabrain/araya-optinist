import { describe, expect, it, jest } from "@jest/globals"

import {
  filterHeatMapColumns,
  filterHeatMapRows,
  heatMapZRange,
  manualZRangeIgnored,
} from "components/Workspace/Visualize/Plot/heatMapRows"

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

describe("filterHeatMapColumns", () => {
  const square = [
    [NaN, 1, 2],
    [1, NaN, 3],
    [2, 3, NaN],
  ]
  const columns = [2, 5, 7]

  it("keeps the columns of the selected ROIs, in matrix order", () => {
    expect(filterHeatMapColumns(square, columns, [7, 2])).toEqual({
      z: [
        [NaN, 2],
        [1, 3],
        [2, NaN],
      ],
      columns: [2, 7],
    })
  })

  it("keeps every column when nothing is selected", () => {
    expect(filterHeatMapColumns(square, columns, [])).toEqual({
      z: square,
      columns,
    })
  })
})

describe("heatMapZRange", () => {
  it("spans the whole matrix, ignoring the NaN diagonal", () => {
    expect(
      heatMapZRange([
        [NaN, -0.4],
        [0.9, NaN],
      ]),
    ).toEqual([-0.4, 0.9])
  })

  it("ignores the null the split-JSON API serves for NaN", () => {
    const served = [
      [null, 1],
      [2, null],
    ] as unknown as number[][]
    expect(heatMapZRange(served)).toEqual([1, 2])
  })

  it("is undefined without finite values", () => {
    expect(heatMapZRange(undefined)).toBeUndefined()
    expect(heatMapZRange([[NaN]])).toBeUndefined()
  })
})

describe("heatMapZRange modes", () => {
  // 101 values 0..100, one outlier
  const matrix = [Array.from({ length: 101 }, (_, i) => i), [1000]]

  it("auto spans the outlier", () => {
    expect(heatMapZRange(matrix, "auto")).toEqual([0, 1000])
  })

  it("percentile clips both ends and drops the outlier", () => {
    expect(heatMapZRange(matrix, "percentile", 2)).toEqual([2, 99])
  })

  it("percentile falls back to auto when the clip leaves no range", () => {
    expect(heatMapZRange([[5, 5, 5]], "percentile", 10)).toEqual([5, 5])
    expect(heatMapZRange(matrix, "percentile", 0)).toEqual([0, 1000])
    expect(heatMapZRange(matrix, "percentile", 50)).toEqual([0, 1000])
  })

  it("manual uses the typed range and ignores the data", () => {
    expect(heatMapZRange(matrix, "manual", 2, [0.8, 1.5])).toEqual([0.8, 1.5])
  })

  it("manual falls back to auto until the range is valid", () => {
    expect(heatMapZRange(matrix, "manual", 2, null)).toEqual([0, 1000])
    expect(heatMapZRange(matrix, "manual", 2, [3, 3])).toEqual([0, 1000])
  })

  it("manual takes an empty bound from the data", () => {
    expect(heatMapZRange(matrix, "manual", 2, [null, 50])).toEqual([0, 50])
    expect(heatMapZRange(matrix, "manual", 2, [5, null])).toEqual([5, 1000])
    expect(heatMapZRange(matrix, "manual", 2, [null, null])).toEqual([0, 1000])
    // a typed min above the data max leaves no range
    expect(heatMapZRange(matrix, "manual", 2, [2000, null])).toEqual([0, 1000])
  })

  it("reports a typed range that is empty on the data", () => {
    expect(manualZRangeIgnored(matrix, null)).toBe(false)
    expect(manualZRangeIgnored(matrix, [null, null])).toBe(false)
    expect(manualZRangeIgnored(matrix, [5, null])).toBe(false)
    expect(manualZRangeIgnored(matrix, [2000, null])).toBe(true)
    expect(manualZRangeIgnored(matrix, [null, -1])).toBe(true)
    expect(manualZRangeIgnored(matrix, [3, 3])).toBe(true)
  })

  it("skips NaN and null in every mode", () => {
    const served = [[null, 1], [2, NaN], [3]] as unknown as number[][]
    expect(heatMapZRange(served, "percentile", 10)).toEqual([1, 3])
    expect(heatMapZRange(served, "manual", 2, null)).toEqual([1, 3])
  })
})

describe("heatMapZRange auto mode", () => {
  it("scans for the min and max without copying and sorting the values", () => {
    const sort = jest.spyOn(Array.prototype, "sort")
    try {
      expect(
        heatMapZRange(
          [
            [3, NaN],
            [-1, 2],
          ],
          "auto",
        ),
      ).toEqual([-1, 3])
      expect(sort).not.toHaveBeenCalled()
    } finally {
      sort.mockRestore()
    }
  })
})

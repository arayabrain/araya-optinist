import { describe, it, expect } from "@jest/globals"

import {
  filterHeatMapColumns,
  filterHeatMapRows,
  heatMapZRange,
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

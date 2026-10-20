import { describe, it, expect } from "@jest/globals"

import {
  outlineRoi,
  roiIdAt,
} from "components/Workspace/Visualize/Plot/roiOutline"

const _ = null

describe("outlineRoi", () => {
  it("drops interior pixels and keeps the boundary of a filled ROI", () => {
    const roi = [
      [_, _, _, _, _],
      [_, 1, 1, 1, _],
      [_, 1, 1, 1, _],
      [_, 1, 1, 1, _],
      [_, _, _, _, _],
    ]
    expect(outlineRoi(roi)).toEqual([
      [_, _, _, _, _],
      [_, 1, 1, 1, _],
      [_, 1, _, 1, _],
      [_, 1, 1, 1, _],
      [_, _, _, _, _],
    ])
  })

  it("keeps the edge pixels on both sides where two ROIs touch", () => {
    // 3-wide ROIs, so the middle row's x = 3 and x = 4 touch only each other
    const roi = [
      [_, 0, 0, 0, 2, 2, 2, _],
      [_, 0, 0, 0, 2, 2, 2, _],
      [_, 0, 0, 0, 2, 2, 2, _],
    ]
    expect(outlineRoi(roi)).toEqual([
      [_, 0, 0, 0, 2, 2, 2, _],
      [_, 0, _, 0, 2, _, 2, _],
      [_, 0, 0, 0, 2, 2, 2, _],
    ])
  })

  it("keeps a one-pixel ROI", () => {
    const roi = [
      [_, _, _],
      [_, 5, _],
      [_, _, _],
    ]
    expect(outlineRoi(roi)).toEqual(roi)
  })

  it("keeps pixels on the field border", () => {
    const roi = [
      [3, 3, 3],
      [3, 3, 3],
      [3, 3, 3],
    ]
    expect(outlineRoi(roi)).toEqual([
      [3, 3, 3],
      [3, _, 3],
      [3, 3, 3],
    ])
  })

  it("treats a negative background value as no ROI", () => {
    expect(
      outlineRoi([
        [-1, -1, -1],
        [-1, 2, -1],
      ]),
    ).toEqual([
      [_, _, _],
      [_, 2, _],
    ])
  })

  it("leaves an all-null field all null", () => {
    const roi = [
      [_, _, _],
      [_, _, _],
    ]
    expect(outlineRoi(roi)).toEqual(roi)
  })

  it("keeps every pixel of a one-row field", () => {
    const roi = [[4, 4, 4, _, 6]]
    expect(outlineRoi(roi)).toEqual(roi)
  })

  it("preserves the shape of a non-square field", () => {
    const roi = Array.from({ length: 4 }, () => Array(7).fill(1))
    const out = outlineRoi(roi)
    expect(out).toHaveLength(4)
    out.forEach((row) => expect(row).toHaveLength(7))
    expect(out[1].slice(1, 6)).toEqual([_, _, _, _, _])
  })
})

describe("roiIdAt", () => {
  const roi = [
    [_, _, _, _],
    [_, 2, 2, _],
    [_, 2, 2, -1],
  ]

  it("takes the id from the ROI trace, including id 0", () => {
    expect(roiIdAt({ curveNumber: 1, x: 0, y: 0, z: 0 }, roi)).toBe(0)
  })

  it("looks up the ROI under an image-trace click on an outline gap", () => {
    expect(roiIdAt({ curveNumber: 0, x: 1.2, y: 1.4, z: 37 }, roi)).toBe(2)
  })

  it("returns null for an image-trace click on the background", () => {
    expect(roiIdAt({ curveNumber: 0, x: 0, y: 0, z: 37 }, roi)).toBeNull()
  })

  it("returns null for a negative id", () => {
    expect(roiIdAt({ curveNumber: 1, x: 3, y: 2, z: -1 }, roi)).toBeNull()
    expect(roiIdAt({ curveNumber: 0, x: 3, y: 2 }, roi)).toBeNull()
  })

  it("returns null outside the field", () => {
    expect(roiIdAt({ curveNumber: 0, x: 1, y: 3 }, roi)).toBeNull()
    expect(roiIdAt({ curveNumber: 0, x: 4, y: 1 }, roi)).toBeNull()
  })
})

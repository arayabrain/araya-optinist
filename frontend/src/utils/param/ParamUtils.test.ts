import { describe, expect, it } from "@jest/globals"

import { ParamMap } from "utils/param/ParamType"
import {
  convertToParamMap,
  describeNodeParamChanges,
  hasOutdatedShape,
  reconcileParamMap,
} from "utils/param/ParamUtils"

const defaults: ParamMap = convertToParamMap({
  border_nan: "copy",
  shifts_interpolate: false,
  max_shifts: [6, 6],
  gSig_filt: null,
  advanced: { nb: 2, merge_params: { merge_thr: 0.85 } },
})

describe("reconcileParamMap", () => {
  it("keeps saved values and drops keys unknown to the defaults", () => {
    const saved: ParamMap = convertToParamMap({
      border_nan: "min",
      use_cuda: false,
      max_shifts: [8, 8],
      gSig_filt: null,
      advanced: {
        nb: 3,
        merge_params: { merge_thr: 0.9, max_merge_area: null },
      },
    })
    const { params, removed } = reconcileParamMap(saved, defaults)
    expect(params).toEqual(
      convertToParamMap({
        border_nan: "min",
        max_shifts: [8, 8],
        gSig_filt: null,
        advanced: { nb: 3, merge_params: { merge_thr: 0.9 } },
      }),
    )
    expect(removed).toEqual([
      "use_cuda",
      "advanced/merge_params/max_merge_area",
    ])
  })

  it("does not add defaults the saved tree lacks", () => {
    const saved: ParamMap = convertToParamMap({ border_nan: "copy" })
    const { params, removed } = reconcileParamMap(saved, defaults)
    expect(params).toEqual(saved)
    expect(removed).toEqual([])
  })

  it("drops a key whose shape changed between child and parent", () => {
    const saved: ParamMap = convertToParamMap({
      border_nan: "copy",
      advanced: 1,
    })
    const { params, removed } = reconcileParamMap(saved, defaults)
    expect(params).toEqual(convertToParamMap({ border_nan: "copy" }))
    expect(removed).toEqual(["advanced"])
  })
})

describe("hasOutdatedShape", () => {
  it("is false when every saved key is known", () => {
    expect(hasOutdatedShape(defaults, defaults)).toBe(false)
  })

  it("is false for a retired key when all default groups are present", () => {
    const saved = convertToParamMap({ use_cuda: false, advanced: { nb: 2 } })
    expect(hasOutdatedShape(saved, defaults)).toBe(false)
  })

  it("is true when no saved key is known", () => {
    expect(
      hasOutdatedShape(convertToParamMap({ soma_crop: false }), defaults),
    ).toBe(true)
  })

  it("is true for unknown keys plus a missing default group (v1 layout)", () => {
    const saved = convertToParamMap({ border_nan: "copy", soma_crop: false })
    expect(hasOutdatedShape(saved, defaults)).toBe(true)
  })
})

describe("describeNodeParamChanges", () => {
  it("names the node and lists removed paths", () => {
    expect(
      describeNodeParamChanges([
        { nodeId: "n1", name: "caiman_mc", removed: ["advanced/use_cuda"] },
        { nodeId: "n2", name: "caiman_cnmf", removed: ["max_merge_area"] },
      ]),
    ).toBe(
      "caiman_mc (removed: advanced/use_cuda) / caiman_cnmf (removed: max_merge_area)",
    )
  })
})

import type { OptionsObject, SnackbarKey } from "notistack"

import { describe, expect, it, jest } from "@jest/globals"

import { ParamMap } from "utils/param/ParamType"
import {
  convertToParamMap,
  describeNodeParamChanges,
  hasOutdatedShape,
  notifyParamChanges,
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

  it("is false for a retired key when a newer default group is missing", () => {
    const saved = convertToParamMap({ border_nan: "copy", soma_crop: false })
    expect(hasOutdatedShape(saved, defaults)).toBe(false)
  })

  it("is true for a flat key that now lives inside a default group (v1 layout)", () => {
    const saved = convertToParamMap({ border_nan: "copy", merge_thr: 0.9 })
    expect(hasOutdatedShape(saved, defaults)).toBe(true)
  })

  it("is true when nothing overlaps group-less defaults", () => {
    const scalars = convertToParamMap({ use_conda: true, cores: 2 })
    expect(hasOutdatedShape(convertToParamMap({ foo: 1 }), scalars)).toBe(true)
    expect(
      hasOutdatedShape(convertToParamMap({ cores: 4, foo: 1 }), scalars),
    ).toBe(false)
  })
})

describe("notifyParamChanges", () => {
  it("stays quiet when nothing was removed", () => {
    const snackbar = jest.fn<SnackbarKey, [string, OptionsObject?]>(() => "key")
    notifyParamChanges(undefined, snackbar)
    notifyParamChanges([], snackbar)
    expect(snackbar).not.toHaveBeenCalled()
  })

  it("names every change in one info snackbar", () => {
    const snackbar = jest.fn<SnackbarKey, [string, OptionsObject?]>(() => "key")
    notifyParamChanges(
      [{ nodeId: "n1", name: "caiman_mc", removed: ["advanced/use_cuda"] }],
      snackbar,
    )
    expect(snackbar).toHaveBeenCalledTimes(1)
    expect(snackbar.mock.calls[0][0]).toBe(
      "Parameters updated to the current version: caiman_mc (removed: advanced/use_cuda)",
    )
    expect(snackbar.mock.calls[0][1]).toMatchObject({ variant: "info" })
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

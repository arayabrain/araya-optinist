import { describe, it, expect } from "@jest/globals"

import { DATA_TYPE_SET } from "store/slice/DisplayData/DisplayDataType"
import {
  selectHeatMapItemZRange,
  selectHeatMapItemZRangeMode,
  selectHeatMapItemZRangePercentile,
} from "store/slice/VisualizeItem/VisualizeItemSelectors"
import reducer, {
  setHeatMapItemZRange,
  setHeatMapItemZRangeMode,
  setHeatMapItemZRangePercentile,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import { VISUALIZE_ITEM_TYPE_SET } from "store/slice/VisualizeItem/VisualizeItemType"
import { RootState } from "store/store"

const ITEM = 3

// A heatmap item saved before the colour range existed: no zRange fields at all.
const legacyState = {
  items: {
    [ITEM]: {
      itemType: VISUALIZE_ITEM_TYPE_SET.DISPLAY_DATA,
      dataType: DATA_TYPE_SET.HEAT_MAP,
      isWorkflowDialog: false,
    },
  },
} as unknown as ReturnType<typeof reducer>

const asRoot = (visualaizeItem: ReturnType<typeof reducer>) =>
  ({ visualaizeItem }) as unknown as RootState

describe("heatmap colour range in the store", () => {
  it("defaults a legacy item to the full data range", () => {
    const state = asRoot(legacyState)
    expect(selectHeatMapItemZRangeMode(ITEM)(state)).toBe("auto")
    expect(selectHeatMapItemZRangePercentile(ITEM)(state)).toBe(2)
    expect(selectHeatMapItemZRange(ITEM)(state)).toBeNull()
  })

  it("stores the mode, the percentile and the manual range", () => {
    let state = reducer(
      legacyState,
      setHeatMapItemZRangeMode({ itemId: ITEM, zRangeMode: "percentile" }),
    )
    state = reducer(
      state,
      setHeatMapItemZRangePercentile({ itemId: ITEM, zRangePercentile: 5 }),
    )
    state = reducer(
      state,
      setHeatMapItemZRange({ itemId: ITEM, zRange: [0.8, 1.5] }),
    )
    const root = asRoot(state)
    expect(selectHeatMapItemZRangeMode(ITEM)(root)).toBe("percentile")
    expect(selectHeatMapItemZRangePercentile(ITEM)(root)).toBe(5)
    expect(selectHeatMapItemZRange(ITEM)(root)).toEqual([0.8, 1.5])
  })
})

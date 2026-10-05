import { describe, it, expect } from "@jest/globals"

import { DATA_TYPE_SET } from "store/slice/DisplayData/DisplayDataType"
import {
  deleteDisplayItem,
  setNewDisplayDataPath,
} from "store/slice/VisualizeItem/VisualizeItemActions"
import {
  selectHeatMapLinkedDrawOrderList,
  selectVisualizeHeatMapLinkItemIdList,
} from "store/slice/VisualizeItem/VisualizeItemSelectors"
import reducer from "store/slice/VisualizeItem/VisualizeItemSlice"
import {
  VISUALIZE_ITEM_TYPE_SET,
  VisualaizeItem,
} from "store/slice/VisualizeItem/VisualizeItemType"
import { RootState } from "store/store"

const IMAGE = 0
const TRACES = 1
const HEATMAP = 2
const DIALOG_TRACES = 3

const item = (dataType: string, extra: object = {}) => ({
  itemType: VISUALIZE_ITEM_TYPE_SET.DISPLAY_DATA,
  dataType,
  isWorkflowDialog: false,
  ...extra,
})

const buildItems = (refItemId: number | null) => ({
  [IMAGE]: item(DATA_TYPE_SET.IMAGE),
  [TRACES]: item(DATA_TYPE_SET.TIME_SERIES, { drawOrderList: ["7", "73"] }),
  [HEATMAP]: item(DATA_TYPE_SET.HEAT_MAP, { refItemId }),
  [DIALOG_TRACES]: item(DATA_TYPE_SET.TIME_SERIES, { isWorkflowDialog: true }),
})

const asRoot = (refItemId: number | null) =>
  ({ visualaizeItem: { items: buildItems(refItemId) } }) as unknown as RootState

describe("heatmap link targets", () => {
  it("offers image and fluorescence boxes, not other heatmaps or dialog boxes", () => {
    expect(selectVisualizeHeatMapLinkItemIdList(asRoot(null))).toEqual([
      IMAGE,
      TRACES,
    ])
  })

  it("follows the cells drawn in a linked fluorescence box", () => {
    expect(selectHeatMapLinkedDrawOrderList(HEATMAP)(asRoot(TRACES))).toEqual([
      "7",
      "73",
    ])
  })

  it("has no trace selection when linked to an image box or unlinked", () => {
    expect(
      selectHeatMapLinkedDrawOrderList(HEATMAP)(asRoot(IMAGE)),
    ).toBeUndefined()
    expect(
      selectHeatMapLinkedDrawOrderList(HEATMAP)(asRoot(null)),
    ).toBeUndefined()
  })

  it("drops the link when the linked fluorescence box is deleted", () => {
    const state = {
      items: buildItems(TRACES),
      layout: [[IMAGE, TRACES, HEATMAP]],
      selectedItemId: null,
      clickedRois: {},
    } as unknown as VisualaizeItem

    const next = reducer(
      state,
      deleteDisplayItem({ itemId: TRACES, deleteData: false }),
    )

    expect(next.items[HEATMAP]).toMatchObject({ refItemId: null })
  })

  it("drops the link when the linked box changes to a non-ROI data type", () => {
    const state = {
      items: buildItems(TRACES),
      layout: [[IMAGE, TRACES, HEATMAP]],
      selectedItemId: null,
      clickedRois: {},
    } as unknown as VisualaizeItem

    const next = reducer(
      state,
      setNewDisplayDataPath({
        itemId: TRACES,
        filePath: "/some/table.csv",
        nodeId: null,
        dataType: DATA_TYPE_SET.CSV,
        deleteData: false,
      }),
    )

    expect(next.items[HEATMAP]).toMatchObject({ refItemId: null })
  })
})

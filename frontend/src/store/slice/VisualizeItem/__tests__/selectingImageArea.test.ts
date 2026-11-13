import { describe, it, expect, jest } from "@jest/globals"

import { DATA_TYPE_SET } from "store/slice/DisplayData/DisplayDataType"
import { selectingImageArea } from "store/slice/VisualizeItem/VisualizeItemActions"
import { VISUALIZE_ITEM_TYPE_SET } from "store/slice/VisualizeItem/VisualizeItemType"
import { RootState } from "store/store"

// 2 rows x 3 columns, ROI 1 top-left, ROI 2 bottom-right
const roiData = [
  [1, null, null],
  [null, null, 2],
]

const state = {
  visualaizeItem: {
    items: {
      0: {
        itemType: VISUALIZE_ITEM_TYPE_SET.DISPLAY_DATA,
        dataType: DATA_TYPE_SET.IMAGE,
        roiItem: { filePath: "/roi" },
      },
    },
  },
  displayData: { roi: { "/roi": { data: [roiData] } } },
} as unknown as RootState

const select = async (x: number[], y: number[]) => {
  const action = await selectingImageArea({ itemId: 0, range: { x, y } })(
    jest.fn() as never,
    () => state,
    undefined,
  )
  return action.payload
}

describe("selectingImageArea", () => {
  it("selects nothing for a box wholly in the padding around the field", async () => {
    expect(await select([-3, -1], [0, 1])).toEqual([])
    expect(await select([0, 2], [3, 5])).toEqual([])
  })

  it("selects the part of a box that overlaps the field", async () => {
    expect(await select([-2, 0.4], [-2, 0.4])).toEqual(["1"])
    expect(await select([1.6, 9], [-9, 9])).toEqual(["2"])
  })

  it("walks only the field however far a zoomed-out box reaches", async () => {
    const started = Date.now()
    expect(await select([-3000, 3000], [-3000, 3000])).toEqual(["1", "2"])
    expect(Date.now() - started).toBeLessThan(1000)
  })
})

import { createAsyncThunk, createAction } from "@reduxjs/toolkit"

import {
  getImageData,
  getTimeSeriesDataById,
  RejectPayload,
} from "store/slice/DisplayData/DisplayDataActions"
import {
  selectRoiData,
  selectStatusRoiTempAdd,
} from "store/slice/DisplayData/DisplayDataSelectors"
import { DATA_TYPE } from "store/slice/DisplayData/DisplayDataType"
import { selectVisualizeItems } from "store/slice/VisualizeItem/VisualizeItemSelectors"
import {
  setClickedData,
  setImageItemEndIndex,
} from "store/slice/VisualizeItem/VisualizeItemSlice"
import { VISUALIZE_ITEM_SLICE_NAME } from "store/slice/VisualizeItem/VisualizeItemType"
import {
  isImageItem,
  isTimeSeriesItem,
} from "store/slice/VisualizeItem/VisualizeItemUtils"
import { ThunkApiConfig } from "store/store"

/**
 * An image item's frames, for its window. A dialog item has no range control,
 * so a window the server refuses as too large is narrowed to the frames it
 * reports as fitting, and fetched again.
 */
export const getImageItemData = createAsyncThunk<
  void,
  { itemId: number; path: string; workspaceId: number },
  ThunkApiConfig
>(
  `${VISUALIZE_ITEM_SLICE_NAME}/getImageItemData`,
  async ({ itemId, path, workspaceId }, thunkAPI) => {
    const item = selectVisualizeItems(thunkAPI.getState())[itemId]
    if (item == null || !isImageItem(item)) return
    const { startIndex, endIndex } = item
    const result = await thunkAPI.dispatch(
      getImageData({ path, workspaceId, startIndex, endIndex }),
    )
    if (!item.isWorkflowDialog || !getImageData.rejected.match(result)) return

    const maxFrames = (result.payload as RejectPayload | undefined)?.maxFrames
    const firstFrame = Math.max(startIndex, 1)
    if (maxFrames && endIndex - firstFrame + 1 > maxFrames) {
      const fittedEndIndex = firstFrame + maxFrames - 1
      thunkAPI.dispatch(
        setImageItemEndIndex({ itemId, endIndex: fittedEndIndex }),
      )
      await thunkAPI.dispatch(
        getImageData({
          path,
          workspaceId,
          startIndex,
          endIndex: fittedEndIndex,
        }),
      )
    }
  },
)

export const setImageItemClickedDataId = createAsyncThunk<
  void,
  { itemId: number; clickedDataId: string | null },
  ThunkApiConfig
>(
  `${VISUALIZE_ITEM_SLICE_NAME}/setImageItemClickedDataId`,
  ({ itemId, clickedDataId }, thunkAPI) => {
    thunkAPI.dispatch(setClickedData({ itemId, clickedDataId }))
    const items = selectVisualizeItems(thunkAPI.getState())
    const tempAdd = selectStatusRoiTempAdd(thunkAPI.getState())
    Object.values(items).forEach((item) => {
      if (
        isTimeSeriesItem(item) &&
        item.filePath != null &&
        item.refImageItemId === itemId &&
        clickedDataId &&
        !tempAdd?.includes(Number(clickedDataId))
      ) {
        thunkAPI.dispatch(
          getTimeSeriesDataById({ path: item.filePath, index: clickedDataId }),
        )
      }
    })
  },
)

export const selectingImageArea = createAsyncThunk<
  string[],
  {
    itemId: number
    range: {
      x: number[]
      y: number[]
    }
    callback?: (rois: string[]) => void
  },
  ThunkApiConfig
>(
  `${VISUALIZE_ITEM_SLICE_NAME}/selectingImageArea`,
  ({ itemId, range, callback }, thunkAPI) => {
    const { x, y } = range
    const selectedZList: string[] = []
    const items = selectVisualizeItems(thunkAPI.getState())
    const imageItem = items[itemId]
    if (isImageItem(imageItem) && imageItem.roiItem != null) {
      const roiFilePath = imageItem.roiItem.filePath
      if (roiFilePath != null) {
        const roiData = selectRoiData(roiFilePath)(thunkAPI.getState())
        // the box's overlap with the field; empty when it lies wholly outside
        const x1 = Math.max(Math.round(x[0]), 0)
        const x2 = Math.min(Math.round(x[1]), (roiData[0]?.length ?? 0) - 1)
        const y1 = Math.max(Math.round(y[0]), 0)
        const y2 = Math.min(Math.round(y[1]), roiData.length - 1)
        for (let x = x1; x <= x2; x++) {
          for (let y = y1; y <= y2; y++) {
            const z = roiData[y]?.[x]
            if (z != null) {
              const zNum = String(z) // indexとidのずれを回避
              if (!selectedZList.includes(zNum)) {
                selectedZList.push(zNum)
              }
            }
          }
        }
        Object.values(items).forEach((item) => {
          if (isTimeSeriesItem(item) && item.filePath != null) {
            callback?.(selectedZList)
          }
        })
      }
    }
    return selectedZList
  },
)

type DeleteOption =
  | {
      deleteData: true
      dataType: DATA_TYPE
      filePath: string
    }
  | { deleteData: false }

type DeleteDisplayItemArgs = {
  itemId: number
} & DeleteOption

export const deleteDisplayItem = createAction<DeleteDisplayItemArgs>(
  `${VISUALIZE_ITEM_SLICE_NAME}/deleteDisplayItem`,
)

type SetNewDisplayDataPathArgs = {
  itemId: number
  filePath: string
  nodeId: string | null
  dataType?: DATA_TYPE
} & (
  | {
      deleteData: true
      prevFilePath: string
      prevDataType: DATA_TYPE
    }
  | { deleteData: false }
)

export const setNewDisplayDataPath = createAction<SetNewDisplayDataPathArgs>(
  `${VISUALIZE_ITEM_SLICE_NAME}/setNewDisplayDataPath`,
)

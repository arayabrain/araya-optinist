import { AxiosError } from "axios"

import {
  AnyAction,
  ThunkAction,
  createAction,
  createAsyncThunk,
} from "@reduxjs/toolkit"

import {
  TimeSeriesData,
  CsvData,
  RoiData,
  ScatterData,
  BarData,
  HTMLData,
  ImageData,
  ImagePreviewTooLarge,
  HeatMapData,
  HistogramData,
  LineData,
  PieData,
  PolarData,
  StructuredData,
  getTimeSeriesInitDataApi,
  getTimeSeriesDataByIdApi,
  getTimeSeriesAllDataApi,
  getHeatMapDataApi,
  getImageDataApi,
  getCsvDataApi,
  getRoiDataApi,
  getScatterDataApi,
  getBarDataApi,
  getHTMLDataApi,
  getHistogramDataApi,
  getLineDataApi,
  getPieDataApi,
  getPolarDataApi,
  getMatlabDataApi,
  MatlabData,
  getStructuredDataApi,
  cancelRoiApi,
  addRoiApi,
  mergeRoiApi,
  deleteRoiApi,
  promoteRoiApi,
  commitRoiApi,
  getStatusRoi,
} from "api/visualizations/Outputs"
import { StatusROI } from "components/Workspace/Visualize/Plot/ImagePlot"
import {
  DisplayData,
  PlotMetaData,
  DISPLAY_DATA_SLICE_NAME,
} from "store/slice/DisplayData/DisplayDataType"

export interface RejectPayload {
  message: string
  status?: number
  // Frames that fit, when an image preview was refused as too large
  maxFrames?: number
}

export const SYNC_IN_PROGRESS_MESSAGE = "Syncing from cloud storage..."

export function getDisplayErrorMessage(
  payload: RejectPayload | undefined,
  fallback: string,
): string {
  if (payload?.status === 503) {
    return SYNC_IN_PROGRESS_MESSAGE
  }
  return payload?.message ?? fallback
}

function extractErrorPayload(e: unknown): RejectPayload {
  if (e instanceof AxiosError) {
    const status = e.response?.status
    const body = e.response?.data as Partial<ImagePreviewTooLarge> | undefined
    const payload: RejectPayload = {
      message: body?.detail ?? e.message ?? "Request failed",
      status,
    }
    if (typeof body?.max_frames === "number") {
      payload.maxFrames = body.max_frames
    }
    return payload
  }
  if (e instanceof Error) {
    return { message: e.message }
  }
  return { message: String(e) }
}

export const getTimeSeriesInitData = createAsyncThunk<
  {
    data: TimeSeriesData
    xrange: string[]
    std: TimeSeriesData
    meta?: PlotMetaData
  },
  { path: string; itemId: number; isFull?: boolean },
  { state: { displayData: DisplayData } }
>(
  `${DISPLAY_DATA_SLICE_NAME}/getTimeSeriesInitData`,
  async ({ path, isFull }, thunkAPI) => {
    try {
      const response = await getTimeSeriesInitDataApi(path, isFull)
      const data = { ...response.data }
      const std = { ...response.std }
      const tried = new Set<string>()
      const failed = new Set<string>()
      // One point per record, so refetch the records another item holds in full,
      // including any a by-id request filled while this one was in flight
      for (;;) {
        const entry = thunkAPI.getState().displayData.timeSeries[path]
        const held = entry?.data ?? {}
        const full = (key: string) => Object.keys(held[key] ?? {}).length > 1
        // a failed refetch keeps the newest series held rather than a blank
        failed.forEach((key) => {
          if (!full(key)) return
          data[key] = held[key]
          if (entry?.std[key] !== undefined) std[key] = entry.std[key]
        })
        const reload = Object.keys(data).filter(
          (key) =>
            !tried.has(key) && full(key) && Object.keys(data[key]).length === 1,
        )
        if (reload.length === 0) break
        // every record held in full (select all): one all-data request, which has
        // no isFull; a sparser selection stays by id rather than load the output
        const stubs = Object.keys(data).filter(
          (key) => Object.keys(data[key]).length === 1,
        )
        const bulk =
          !isFull && reload.length > 1 && reload.length === stubs.length
        const records = await Promise.allSettled(
          bulk
            ? [getTimeSeriesAllDataApi(path)]
            : reload.map((index) =>
                getTimeSeriesDataByIdApi(path, index, isFull),
              ),
        )
        const kept = { data: held, std: entry?.std ?? {} }
        reload.forEach((key, i) => {
          const record = records[bulk ? 0 : i]
          tried.add(key)
          const got =
            record.status === "fulfilled" &&
            record.value.data[key] !== undefined
          if (!got) failed.add(key)
          const source = got ? record.value : kept
          data[key] = source.data[key]
          if (source.std[key] !== undefined) std[key] = source.std[key]
        })
      }
      return { ...response, data, std }
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const getTimeSeriesDataById = createAsyncThunk<
  {
    data: TimeSeriesData
    xrange: string[]
    std: TimeSeriesData
    meta?: PlotMetaData
  },
  // background: a request the user did not make, whose failure leaves the plot up
  { path: string; index: string; isFull?: boolean; background?: boolean }
>(
  `${DISPLAY_DATA_SLICE_NAME}/getTimeSeriesDataById`,
  async ({ path, index, isFull }, thunkAPI) => {
    try {
      const response = await getTimeSeriesDataByIdApi(path, index, isFull)
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

// Skips a record another plot already requested or loaded
export const getTimeSeriesDataByIdOnce =
  (arg: {
    path: string
    index: string
  }): ThunkAction<void, { displayData: DisplayData }, unknown, AnyAction> =>
  (dispatch, getState) => {
    if (
      getState().displayData.timeSeries[arg.path]?.byId?.[arg.index] == null
    ) {
      dispatch(getTimeSeriesDataById({ ...arg, background: true }))
    }
  }

// Forgets the records whose request failed, so they are requested once more
export const retryRejectedTimeSeries = createAction<{ path: string }>(
  `${DISPLAY_DATA_SLICE_NAME}/retryRejectedTimeSeries`,
)

export const getTimeSeriesAllData = createAsyncThunk<
  {
    data: TimeSeriesData
    xrange: string[]
    std: TimeSeriesData
    meta?: PlotMetaData
  },
  { path: string }
>(
  `${DISPLAY_DATA_SLICE_NAME}/getTimeSeriesAllData`,
  async ({ path }, thunkAPI) => {
    try {
      const response = await getTimeSeriesAllDataApi(path)
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const getHeatMapData = createAsyncThunk<
  {
    data: HeatMapData
    columns: string[]
    index: string[]
    meta?: PlotMetaData
  },
  { path: string }
>(`${DISPLAY_DATA_SLICE_NAME}/getHeatMapData`, async ({ path }, thunkAPI) => {
  try {
    const response = await getHeatMapDataApi(path)
    return response
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const getImageData = createAsyncThunk<
  { data: ImageData; meta?: PlotMetaData },
  {
    path: string
    workspaceId: number
    uniqueId?: string
    startIndex?: number
    endIndex?: number
  }
>(
  `${DISPLAY_DATA_SLICE_NAME}/getImageData`,
  async ({ path, startIndex, endIndex, workspaceId, uniqueId }, thunkAPI) => {
    try {
      const response = await getImageDataApi(path, {
        workspaceId,
        uniqueId,
        startIndex,
        endIndex,
      })
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(extractErrorPayload(e))
    }
  },
)

export const getCsvData = createAsyncThunk<
  { data: CsvData; meta?: PlotMetaData },
  { path: string; workspaceId: number }
>(
  `${DISPLAY_DATA_SLICE_NAME}/getCsvData`,
  async ({ path, workspaceId }, thunkAPI) => {
    try {
      const response = await getCsvDataApi(path, { workspaceId })
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(extractErrorPayload(e))
    }
  },
)

export const getMatlabData = createAsyncThunk<
  { data: MatlabData; meta?: PlotMetaData },
  { path: string; workspaceId: number }
>(
  `${DISPLAY_DATA_SLICE_NAME}/getMatlabData`,
  async ({ path, workspaceId }, thunkAPI) => {
    try {
      const response = await getMatlabDataApi(path, { workspaceId })
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(extractErrorPayload(e))
    }
  },
)

export const getRoiData = createAsyncThunk<
  { data: RoiData; meta?: PlotMetaData },
  { path: string; workspaceId: number; uniqueId?: string; isFull?: boolean }
>(
  `${DISPLAY_DATA_SLICE_NAME}/getRoiData`,
  async ({ path, workspaceId, uniqueId, isFull }, thunkAPI) => {
    try {
      const response = await getRoiDataApi(
        path,
        { workspaceId, uniqueId },
        isFull,
      )
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(extractErrorPayload(e))
    }
  },
)

export const cancelRoi = createAsyncThunk<
  { data: HTMLData; meta?: PlotMetaData },
  { path: string | string[]; workspaceId: number }
>(
  `${DISPLAY_DATA_SLICE_NAME}/cancelRoi`,
  async ({ path, workspaceId }, thunkAPI) => {
    try {
      const response = await cancelRoiApi(path, workspaceId)
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const addRoi = createAsyncThunk<
  { data: HTMLData; meta?: PlotMetaData },
  {
    path: string
    workspaceId: number
    data: { posx: number; posy: number; sizex: number; sizey: number }
  }
>(
  `${DISPLAY_DATA_SLICE_NAME}/addRoi`,
  async ({ path, workspaceId, data }, thunkAPI) => {
    const { dispatch } = thunkAPI
    try {
      const response = await addRoiApi(path, workspaceId, data)
      await dispatch(getStatus({ path, workspaceId }))
      await dispatch(getRoiData({ path, workspaceId }))
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const mergeRoi = createAsyncThunk<
  { data: HTMLData; meta?: PlotMetaData },
  { path: string; workspaceId: number; data: { ids: number[] } }
>(
  `${DISPLAY_DATA_SLICE_NAME}/mergeROi`,
  async ({ path, workspaceId, data }, thunkAPI) => {
    const { dispatch } = thunkAPI
    try {
      const response = await mergeRoiApi(path, workspaceId, data)
      dispatch(getStatus({ path, workspaceId }))
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const deleteRoi = createAsyncThunk<
  { data: HTMLData; meta?: PlotMetaData },
  { path: string; workspaceId: number; data: { ids: number[] } }
>(
  `${DISPLAY_DATA_SLICE_NAME}/deleteRoi`,
  async ({ path, workspaceId, data }, thunkAPI) => {
    const { dispatch } = thunkAPI
    try {
      const response = await deleteRoiApi(path, workspaceId, data)
      dispatch(getStatus({ path, workspaceId }))
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const promoteRoi = createAsyncThunk<
  { data: HTMLData; meta?: PlotMetaData },
  { path: string; workspaceId: number; data: { ids: number[] } }
>(
  `${DISPLAY_DATA_SLICE_NAME}/promoteRoi`,
  async ({ path, workspaceId, data }, thunkAPI) => {
    const { dispatch } = thunkAPI
    try {
      const response = await promoteRoiApi(path, workspaceId, data)
      dispatch(getStatus({ path, workspaceId }))
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(extractErrorPayload(e))
    }
  },
)

export const commitRoi = createAsyncThunk<
  boolean,
  { path: string; workspaceId: number }
>(
  `${DISPLAY_DATA_SLICE_NAME}/commitRoi`,
  async ({ path, workspaceId }, thunkAPI) => {
    const { dispatch } = thunkAPI
    try {
      const response = await commitRoiApi(path, workspaceId)
      dispatch(getStatus({ path, workspaceId }))
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(extractErrorPayload(e))
    }
  },
)

export const getStatus = createAsyncThunk<
  StatusROI,
  { path: string; workspaceId: number }
>(
  `${DISPLAY_DATA_SLICE_NAME}/getStatusRoi`,
  async ({ path, workspaceId }, thunkAPI) => {
    try {
      const response = await getStatusRoi(path, workspaceId)
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(extractErrorPayload(e))
    }
  },
)

export const getScatterData = createAsyncThunk<
  { data: ScatterData; meta?: PlotMetaData },
  { path: string }
>(`${DISPLAY_DATA_SLICE_NAME}/getScatterData`, async ({ path }, thunkAPI) => {
  try {
    const response = await getScatterDataApi(path)
    return response
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const getBarData = createAsyncThunk<
  { data: BarData; columns: string[]; index: string[]; meta?: PlotMetaData },
  { path: string }
>(`${DISPLAY_DATA_SLICE_NAME}/getBarData`, async ({ path }, thunkAPI) => {
  try {
    const response = await getBarDataApi(path)
    return response
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const getHTMLData = createAsyncThunk<
  { data: HTMLData; meta?: PlotMetaData },
  { path: string }
>(`${DISPLAY_DATA_SLICE_NAME}/getHTMLData`, async ({ path }, thunkAPI) => {
  try {
    const response = await getHTMLDataApi(path)
    return response
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const getHistogramData = createAsyncThunk<
  { data: HistogramData; meta?: PlotMetaData },
  { path: string }
>(`${DISPLAY_DATA_SLICE_NAME}/getHistogramData`, async ({ path }, thunkAPI) => {
  try {
    const response = await getHistogramDataApi(path)
    return response
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const getLineData = createAsyncThunk<
  { data: LineData; columns: number[]; index: number[]; meta?: PlotMetaData },
  { path: string }
>(`${DISPLAY_DATA_SLICE_NAME}/getLineData`, async ({ path }, thunkAPI) => {
  try {
    const response = await getLineDataApi(path)
    return response
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const getPieData = createAsyncThunk<
  { data: PieData; columns: string[]; meta?: PlotMetaData },
  { path: string }
>(`${DISPLAY_DATA_SLICE_NAME}/getPieData`, async ({ path }, thunkAPI) => {
  try {
    const response = await getPieDataApi(path)
    return response
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const getPolarData = createAsyncThunk<
  { data: PolarData; columns: number[]; index: number[]; meta?: PlotMetaData },
  { path: string }
>(`${DISPLAY_DATA_SLICE_NAME}/getPolarData`, async ({ path }, thunkAPI) => {
  try {
    const response = await getPolarDataApi(path)
    return response
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const getStructuredData = createAsyncThunk<
  StructuredData,
  {
    workspaceId: string
    uniqueId: string
    nodeId: string
    itemId: number
    startIndex?: number
    endIndex?: number
  }
>(
  `${DISPLAY_DATA_SLICE_NAME}/getStructuredData`,
  async ({ workspaceId, uniqueId, nodeId, startIndex, endIndex }, thunkAPI) => {
    try {
      const response = await getStructuredDataApi(
        workspaceId,
        uniqueId,
        nodeId,
        startIndex,
        endIndex,
      )
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

import { createAsyncThunk } from "@reduxjs/toolkit"

import {
  getDataviewRecordsApi,
  getExperimentsPublicApi,
  postPublishAllApi,
  postPublishApi,
  putAttributesApi,
} from "api/dataview"
import {
  DATAVIEW_SLICE_NAME,
  DataviewDTO,
  DataviewParams,
} from "store/slice/Dataview/DataviewType"

export const getDataviewRecords = createAsyncThunk<DataviewDTO, DataviewParams>(
  `${DATAVIEW_SLICE_NAME}/getDataviewRecords`,
  async (params, thunkAPI) => {
    const { rejectWithValue } = thunkAPI
    try {
      const response = await getDataviewRecordsApi(params)
      return response
    } catch (e) {
      return rejectWithValue(e)
    }
  },
)

export const getExperimentsPublicDatabase = createAsyncThunk<
  DataviewDTO,
  DataviewParams
>(
  `${DATAVIEW_SLICE_NAME}/getExperimentsPublicList`,
  async (params, thunkAPI) => {
    const { rejectWithValue } = thunkAPI
    try {
      const response = await getExperimentsPublicApi(params)
      return response
    } catch (e) {
      return rejectWithValue(e)
    }
  },
)

export const postPublish = createAsyncThunk<
  boolean,
  { id: number; status: "on" | "off"; params: DataviewParams }
>(`${DATAVIEW_SLICE_NAME}/postPublish`, async (params, thunkAPI) => {
  const { rejectWithValue, dispatch } = thunkAPI
  try {
    const response = await postPublishApi(params.id, params.status)
    await dispatch(getDataviewRecords(params.params))
    return response
  } catch (e) {
    return rejectWithValue(e)
  }
})

export const postPublishAll = createAsyncThunk<
  boolean,
  { status: "on" | "off"; params: DataviewParams; listCheck: number[] }
>(`${DATAVIEW_SLICE_NAME}/postPublishAll`, async (data, thunkAPI) => {
  const { rejectWithValue, dispatch } = thunkAPI
  const { status, listCheck, params } = data
  try {
    const response = await postPublishAllApi(status, listCheck)
    await dispatch(getDataviewRecords(params))
    return response
  } catch (e) {
    return rejectWithValue(e)
  }
})

export const putAttributes = createAsyncThunk<
  boolean,
  { id: number; attributes: string; params: DataviewParams }
>(`${DATAVIEW_SLICE_NAME}/putAttributes`, async (data, thunkAPI) => {
  const { rejectWithValue, dispatch } = thunkAPI
  try {
    const { id, attributes, params } = data
    const response = await putAttributesApi(id, attributes)
    await dispatch(getDataviewRecords(params))
    return response
  } catch (e) {
    return rejectWithValue(e)
  }
})

import { createSlice, isAnyOf } from "@reduxjs/toolkit"

import {
  getDataviewRecords,
  getExperimentsPublicDatabase,
  postPublish,
  postPublishAll,
  putAttributes,
} from "store/slice/Dataview/DataviewActions"
import {
  DATAVIEW_SLICE_NAME,
  DataviewDTO,
} from "store/slice/Dataview/DataviewType"

const initData = {
  offset: 0,
  total: 0,
  limit: 50,
  header: {
    graph_titles: [],
  },
  items: [],
}

export type TypeData = {
  public: DataviewDTO
  private: DataviewDTO
}

export const initialState: {
  data: TypeData
  loading: boolean
} = {
  data: {
    public: initData,
    private: initData,
  },
  loading: false,
}

export const databaseSlice = createSlice({
  name: DATAVIEW_SLICE_NAME,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(getDataviewRecords.pending, (state) => {
        state.data.private = initData
        state.loading = true
      })
      .addCase(getExperimentsPublicDatabase.pending, (state) => {
        state.data.public = initData
        state.loading = true
      })
      .addMatcher(
        isAnyOf(
          postPublish.pending,
          postPublishAll.pending,
          putAttributes.pending,
        ),
        (state) => {
          state.loading = true
        },
      )
      .addMatcher(isAnyOf(getDataviewRecords.fulfilled), (state, action) => {
        state.data.private = action.payload
        state.loading = false
      })
      .addMatcher(
        isAnyOf(getExperimentsPublicDatabase.fulfilled),
        (state, action) => {
          state.data.public = action.payload
          state.loading = false
        },
      )
      .addMatcher(
        isAnyOf(
          getDataviewRecords.rejected,
          getExperimentsPublicDatabase.rejected,

          postPublish.fulfilled,
          postPublish.rejected,
          postPublishAll.rejected,

          putAttributes.fulfilled,
          putAttributes.rejected,
        ),
        (state) => {
          state.loading = false
        },
      )
  },
})

export default databaseSlice.reducer

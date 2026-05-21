import { createSlice, isAnyOf } from "@reduxjs/toolkit"

import {
  getDataviewRecords,
  getPublicDataviewRecords,
  postPublish,
  postPublishAll,
  putAttributes,
} from "store/slice/Dataview/DataviewActions"
import {
  DATAVIEW_SLICE_NAME,
  DataviewDTO,
} from "store/slice/Dataview/DataviewType"

const initData: DataviewDTO = {
  offset: 0,
  total: 0,
  limit: 50,
  header: {
    workspace_id: undefined,
    workspace_name: undefined,
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
  error: {
    public: string | null
    private: string | null
  }
} = {
  data: {
    public: initData,
    private: initData,
  },
  loading: false,
  error: {
    public: null,
    private: null,
  },
}

export const databaseSlice = createSlice({
  name: DATAVIEW_SLICE_NAME,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      // All addCase calls must come before addMatcher calls
      .addCase(getDataviewRecords.pending, (state) => {
        // Don't reset data on pending - keep showing previous data while loading
        state.loading = true
        state.error.private = null
      })
      .addCase(getPublicDataviewRecords.pending, (state) => {
        // Don't reset data on pending - keep showing previous data while loading
        state.loading = true
        state.error.public = null
      })
      .addCase(getDataviewRecords.fulfilled, (state, action) => {
        state.data.private = action.payload
        state.loading = false
        state.error.private = null
      })
      .addCase(getPublicDataviewRecords.fulfilled, (state, action) => {
        state.data.public = action.payload
        state.loading = false
        state.error.public = null
      })
      .addCase(getDataviewRecords.rejected, (state, action) => {
        // Keep previous data on error, but set error state
        state.loading = false
        state.error.private = action.error.message || "Failed to load data"
      })
      .addCase(getPublicDataviewRecords.rejected, (state, action) => {
        // Keep previous data on error, but set error state
        state.loading = false
        state.error.public = action.error.message || "Failed to load data"
      })
      // addMatcher calls come after all addCase calls
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
      .addMatcher(
        isAnyOf(
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

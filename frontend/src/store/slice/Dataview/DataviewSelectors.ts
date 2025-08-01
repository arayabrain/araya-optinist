import { createSelector } from "@reduxjs/toolkit"

import { DATAVIEW_SLICE_NAME } from "store/slice/Dataview/DataviewType"
import { RootState } from "store/store"

const selectDataviewSlice = (state: RootState) => state[DATAVIEW_SLICE_NAME]

export const selectDataviewPrivateData = createSelector(
  selectDataviewSlice,
  (dataview) => dataview.data.private,
)

export const selectDataviewPublicData = createSelector(
  selectDataviewSlice,
  (dataview) => dataview.data.public,
)

export const selectDataviewLoading = createSelector(
  selectDataviewSlice,
  (dataview) => dataview.loading,
)

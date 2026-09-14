import { createSlice } from "@reduxjs/toolkit"

import { getHDF5Tree } from "store/slice/HDF5/HDF5Action"
import { HDF5Tree, HDF5_SLICE_NAME } from "store/slice/HDF5/HDF5Type"
import { convertToTreeNodeType } from "store/slice/HDF5/HDF5Utils"

const initialState: HDF5Tree = {
  trees: {},
}
export const HDF5Slice = createSlice({
  name: HDF5_SLICE_NAME,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(getHDF5Tree.pending, (state, action) => {
        const path = action.meta.arg.path
        state.trees[path] = {
          tree: state.trees[path]?.tree ?? [],
          isLoading: true,
        }
      })
      .addCase(getHDF5Tree.fulfilled, (state, action) => {
        state.trees[action.meta.arg.path] = {
          tree: convertToTreeNodeType(action.payload),
          isLoading: false,
        }
      })
      .addCase(getHDF5Tree.rejected, (state, action) => {
        const path = action.meta.arg.path
        state.trees[path] = {
          tree: state.trees[path]?.tree ?? [],
          isLoading: false,
        }
      })
  },
})

export default HDF5Slice.reducer

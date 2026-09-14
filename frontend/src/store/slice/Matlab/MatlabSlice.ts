import { createSlice } from "@reduxjs/toolkit"

import { getMatlabTree } from "store/slice/Matlab/MatlabAction"
import { MATLAB_SLICE_NAME, MatlabTree } from "store/slice/Matlab/MatlabType"
import { convertToTreeNodeType } from "store/slice/Matlab/MatlabUtils"

const initialState: MatlabTree = {
  trees: {},
}
export const matlabSlice = createSlice({
  name: MATLAB_SLICE_NAME,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(getMatlabTree.pending, (state, action) => {
        const path = action.meta.arg.path
        state.trees[path] = {
          tree: state.trees[path]?.tree ?? [],
          isLoading: true,
        }
      })
      .addCase(getMatlabTree.fulfilled, (state, action) => {
        state.trees[action.meta.arg.path] = {
          tree: convertToTreeNodeType(action.payload),
          isLoading: false,
        }
      })
      .addCase(getMatlabTree.rejected, (state, action) => {
        const path = action.meta.arg.path
        state.trees[path] = {
          tree: state.trees[path]?.tree ?? [],
          isLoading: false,
        }
      })
  },
})

export default matlabSlice.reducer

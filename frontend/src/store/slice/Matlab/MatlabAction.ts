import { createAsyncThunk } from "@reduxjs/toolkit"

import { getMatlabTreeApi, MatlabTreeDTO } from "api/matlab/Matlab"
import { MATLAB_SLICE_NAME } from "store/slice/Matlab/MatlabType"
import { RootState } from "store/store"

export const getMatlabTree = createAsyncThunk<
  MatlabTreeDTO[],
  { path: string; workspaceId: number }
>(
  `${MATLAB_SLICE_NAME}/getMatlabTree`,
  async ({ path, workspaceId }, thunkAPI) => {
    try {
      const response = await getMatlabTreeApi(path, workspaceId)
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
  {
    condition: ({ path }, { getState }) =>
      !(getState() as RootState).matlab.trees[path]?.isLoading,
  },
)

import { RootState } from "store/store"

export const selectHDF5 = (state: RootState) => {
  if (state.hdf5 != null) {
    return state.hdf5
  } else {
    return undefined
  }
}

export const selectHDF5Nodes =
  (filePath: string | undefined) => (state: RootState) =>
    filePath ? selectHDF5(state)?.trees[filePath]?.tree : undefined

export const selectHDF5IsLoading =
  (filePath: string | undefined) => (state: RootState) =>
    filePath ? (selectHDF5(state)?.trees[filePath]?.isLoading ?? false) : false

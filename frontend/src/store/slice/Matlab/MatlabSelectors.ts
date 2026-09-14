import { RootState } from "store/store"

export const selectMatlab = (state: RootState) => {
  if (state.matlab != null) {
    return state.matlab
  } else {
    return undefined
  }
}

export const selectMatlabNodes =
  (filePath: string | undefined) => (state: RootState) =>
    filePath ? selectMatlab(state)?.trees[filePath]?.tree : undefined

export const selectMatlabIsLoading =
  (filePath: string | undefined) => (state: RootState) =>
    filePath
      ? (selectMatlab(state)?.trees[filePath]?.isLoading ?? false)
      : false

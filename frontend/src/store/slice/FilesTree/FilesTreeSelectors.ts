import { FILE_TREE_TYPE } from "api/files/Files"
import { RootState } from "store/store"

export const selectFilesTree =
  (fileType: FILE_TREE_TYPE) => (state: RootState) => {
    if (state.filesTree.files[fileType] != null) {
      return state.filesTree.files[fileType]
    } else {
      return undefined
    }
  }

export const selectFilesTreeNodes =
  (fileType: FILE_TREE_TYPE) => (state: RootState) =>
    selectFilesTree(fileType)(state)?.tree

export const selectFilesIsLatest =
  (fileType: FILE_TREE_TYPE) => (state: RootState) =>
    selectFilesTree(fileType)(state)?.isLatest ?? false

export const selectFilesIsLoading =
  (fileType: FILE_TREE_TYPE) => (state: RootState) =>
    selectFilesTree(fileType)(state)?.isLoading ?? false

export const selectImportSampleDataLoading = (state: RootState) =>
  state.filesTree.importSampleDataLoading

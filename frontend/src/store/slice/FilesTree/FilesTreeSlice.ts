import { enqueueSnackbar } from "notistack"

import { createSlice } from "@reduxjs/toolkit"

import { FILE_TREE_TYPE_SET } from "api/files/Files"
import { getFilesTree, deleteFile } from "store/slice/FilesTree/FilesTreeAction"
import {
  FilesTreeState,
  FILES_TREE_SLICE_NAME,
} from "store/slice/FilesTree/FilesTreeType"
import { convertToTreeNodeType } from "store/slice/FilesTree/FilesTreeUtils"
import { uploadFile } from "store/slice/FileUploader/FileUploaderActions"
import { FILE_TYPE_SET } from "store/slice/InputNode/InputNodeType"
import { importSampleData } from "store/slice/Workflow/WorkflowActions"

export const initialState: FilesTreeState = {
  files: {
    IMAGE: { isLoading: false, isLatest: true, tree: [] },
    CSV: { isLoading: false, isLatest: true, tree: [] },
    HDF5: { isLoading: false, isLatest: true, tree: [] },
    ALL: { isLoading: false, isLatest: true, tree: [] },
  },
  importSampleDataLoading: false,
}

export const filesTreeSlice = createSlice({
  name: FILES_TREE_SLICE_NAME,
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(getFilesTree.pending, (state, action) => {
        const { fileType } = action.meta.arg
        state.files[fileType] = {
          ...state.files[fileType],
          isLoading: true,
          isLatest: false,
        }
      })
      .addCase(getFilesTree.fulfilled, (state, action) => {
        const { fileType } = action.meta.arg
        state.files[fileType].tree = convertToTreeNodeType(action.payload)
        state.files[fileType].isLatest = true
        state.files[fileType].isLoading = false
      })
      .addCase(deleteFile.pending, (state, action) => {
        const { fileType } = action.meta.arg
        state.files[fileType] = {
          ...state.files[fileType],
          isLoading: true,
          isLatest: false,
        }
      })
      .addCase(deleteFile.rejected, (state, action) => {
        const { fileType } = action.meta.arg
        state.files[fileType] = {
          ...state.files[fileType],
          isLoading: false,
          isLatest: true,
        }
        enqueueSnackbar("Failed to delete file", { variant: "error" })
      })
      .addCase(deleteFile.fulfilled, (state, action) => {
        const { fileType, fileName } = action.meta.arg
        const fileTree = state.files[fileType].tree
        state.files[fileType].tree = fileTree.filter(
          (node) => node.name !== fileName,
        )
        state.files[fileType].isLoading = false
        state.files[fileType].isLatest = true
      })
      .addCase(uploadFile.pending, (state, action) => {
        const { fileType } = action.meta.arg
        const targetType =
          fileType === FILE_TYPE_SET.IMAGE
            ? FILE_TREE_TYPE_SET.IMAGE
            : fileType === FILE_TYPE_SET.CSV
              ? FILE_TREE_TYPE_SET.CSV
              : fileType === FILE_TYPE_SET.HDF5
                ? FILE_TREE_TYPE_SET.HDF5
                : FILE_TREE_TYPE_SET.ALL

        if (state.files[targetType] != null) {
          state.files[targetType].isLatest = false
        } else {
          state.files[targetType] = {
            isLoading: false,
            isLatest: false,
            tree: [],
          }
        }
      })
      .addCase(uploadFile.fulfilled, (state, action) => {
        const { fileType } = action.meta.arg
        const targetType =
          fileType === FILE_TYPE_SET.IMAGE
            ? FILE_TREE_TYPE_SET.IMAGE
            : fileType === FILE_TYPE_SET.CSV
              ? FILE_TREE_TYPE_SET.CSV
              : fileType === FILE_TYPE_SET.HDF5
                ? FILE_TREE_TYPE_SET.HDF5
                : FILE_TREE_TYPE_SET.ALL

        state.files[targetType].isLatest = false
      })
      .addCase(importSampleData.pending, (state) => {
        state.importSampleDataLoading = true
        ;[
          FILE_TREE_TYPE_SET.IMAGE,
          FILE_TREE_TYPE_SET.CSV,
          FILE_TREE_TYPE_SET.HDF5,
          FILE_TREE_TYPE_SET.ALL,
        ].forEach((fileType) => {
          if (state.files[fileType] != null) {
            state.files[fileType].isLoading = true
          }
        })
      })
      .addCase(importSampleData.fulfilled, (state) => {
        state.importSampleDataLoading = false
        ;[
          FILE_TREE_TYPE_SET.IMAGE,
          FILE_TREE_TYPE_SET.CSV,
          FILE_TREE_TYPE_SET.HDF5,
          FILE_TREE_TYPE_SET.ALL,
        ].forEach((fileType) => {
          if (state.files[fileType] != null) {
            state.files[fileType].isLatest = false
            state.files[fileType].isLoading = false
          }
        })
      })
      .addCase(importSampleData.rejected, (state) => {
        state.importSampleDataLoading = false
        ;[
          FILE_TREE_TYPE_SET.IMAGE,
          FILE_TREE_TYPE_SET.CSV,
          FILE_TREE_TYPE_SET.HDF5,
          FILE_TREE_TYPE_SET.ALL,
        ].forEach((fileType) => {
          if (state.files[fileType] != null) {
            state.files[fileType].isLoading = false
          }
        })
      })
  },
})

export default filesTreeSlice.reducer

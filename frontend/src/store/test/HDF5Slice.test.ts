import { describe, expect, jest, test } from "@jest/globals"

import { uploadFile } from "store/slice/FileUploader/FileUploaderActions"
import { getHDF5Tree } from "store/slice/HDF5/HDF5Action"
import reducer from "store/slice/HDF5/HDF5Slice"
import { getMatlabTree } from "store/slice/Matlab/MatlabAction"
import { getWorkspace } from "store/slice/Workspace/WorkspaceActions"
import { RootState } from "store/store"

const payload = [
  {
    isDir: false as const,
    name: "x",
    path: "x",
    shape: [2] as [number],
    nbytes: "1",
    dataType: "array",
  },
]

describe("HDF5Slice keyed by file path", () => {
  test("trees of different files do not overwrite each other", () => {
    let state = reducer(
      undefined,
      getHDF5Tree.pending("r1", { path: "a.h5", workspaceId: 1 }),
    )
    expect(state.trees["a.h5"]).toEqual({ tree: [], isLoading: true })
    state = reducer(
      state,
      getHDF5Tree.fulfilled(payload, "r1", { path: "a.h5", workspaceId: 1 }),
    )
    state = reducer(
      state,
      getHDF5Tree.pending("r2", { path: "b.h5", workspaceId: 1 }),
    )
    expect(state.trees["a.h5"].tree).toHaveLength(1)
    expect(state.trees["a.h5"].isLoading).toBe(false)
    expect(state.trees["b.h5"].isLoading).toBe(true)
  })

  test("a refetch keeps the previous tree while loading", () => {
    let state = reducer(
      undefined,
      getHDF5Tree.fulfilled(payload, "r1", { path: "a.h5", workspaceId: 1 }),
    )
    state = reducer(
      state,
      getHDF5Tree.pending("r2", { path: "a.h5", workspaceId: 1 }),
    )
    expect(state.trees["a.h5"]).toEqual({ tree: payload, isLoading: true })
  })

  test("matlab fetches no longer land in the hdf5 slice", () => {
    const state = reducer(
      undefined,
      getMatlabTree.pending("r1", { path: "a.mat", workspaceId: 1 }),
    )
    expect(state.trees).toEqual({})
  })

  test("a same-name upload drops that file's cached tree only", () => {
    let state = reducer(
      undefined,
      getHDF5Tree.fulfilled(payload, "r1", { path: "a.h5", workspaceId: 1 }),
    )
    state = reducer(
      state,
      getHDF5Tree.fulfilled(payload, "r2", { path: "b.h5", workspaceId: 1 }),
    )
    state = reducer(
      state,
      uploadFile.fulfilled({ resultPath: "a.h5" }, "r3", {
        workspaceId: 1,
        requestId: "u",
        fileName: "a.h5",
        formData: new FormData(),
      }),
    )
    expect(state.trees["a.h5"]).toBeUndefined()
    expect(state.trees["b.h5"].tree).toHaveLength(1)
  })

  test("opening a workspace resets the cache", () => {
    let state = reducer(
      undefined,
      getHDF5Tree.fulfilled(payload, "r1", { path: "a.h5", workspaceId: 1 }),
    )
    state = reducer(state, getWorkspace.fulfilled({} as never, "r2", { id: 2 }))
    expect(state.trees).toEqual({})
  })

  test("a fetch already in flight for the file is not dispatched again", async () => {
    const dispatch = jest.fn()
    const loading = {
      hdf5: { trees: { "a.h5": { tree: [], isLoading: true } } },
    } as unknown as RootState
    await getHDF5Tree({ path: "a.h5", workspaceId: 1 })(
      dispatch,
      () => loading,
      undefined,
    )
    const types = dispatch.mock.calls.map(([a]) => (a as { type: string }).type)
    expect(types).not.toContain(getHDF5Tree.pending.type)
  })
})

import { describe, expect, test } from "@jest/globals"

import { getHDF5Tree } from "store/slice/HDF5/HDF5Action"
import reducer from "store/slice/HDF5/HDF5Slice"
import { getMatlabTree } from "store/slice/Matlab/MatlabAction"

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
})

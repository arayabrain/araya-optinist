import { describe, expect, jest, test } from "@jest/globals"

import {
  findDatasetShape,
  handleTypeForRank,
  isRankCompatible,
  isValidConnection,
  selectStructuredDatasetNdim,
} from "components/Workspace/FlowChart/FlowChartNode/FlowChartUtils"
import { RootState, store } from "store/store"

const tree = [
  {
    isDir: true as const,
    name: "data",
    path: "data",
    nodes: [
      {
        isDir: false as const,
        name: "image",
        path: "data/image",
        shape: [500, 128, 128],
      },
      {
        isDir: false as const,
        name: "behavior",
        path: "data/behavior",
        shape: [500, 8],
      },
    ],
  },
]

describe("rank based connection rule", () => {
  test("findDatasetShape walks nested groups", () => {
    expect(findDatasetShape(tree, "data/behavior")).toEqual([500, 8])
    expect(findDatasetShape(tree, "data/missing")).toBeUndefined()
    expect(findDatasetShape(undefined, "data/image")).toBeUndefined()
  })

  test("handleTypeForRank", () => {
    expect(handleTypeForRank(3, "HDF5Data")).toBe("ImageData")
    expect(handleTypeForRank(2, "HDF5Data")).toBe("FluoData")
    expect(handleTypeForRank(1, "HDF5Data")).toBe("IscellData")
    expect(handleTypeForRank(undefined, "HDF5Data")).toBe("HDF5Data")
  })

  test("only ImageData accepts 3D and only non-image accepts 1D/2D", () => {
    expect(isRankCompatible(3, "ImageData")).toBe(true)
    expect(isRankCompatible(2, "ImageData")).toBe(false)
    expect(isRankCompatible(2, "FluoData")).toBe(true)
    expect(isRankCompatible(1, "FluoData")).toBe(true)
    expect(isRankCompatible(3, "FluoData")).toBe(false)
    expect(isRankCompatible(undefined, "ImageData")).toBe(true)
    expect(isRankCompatible(3, "BaseData")).toBe(true)
  })
})

describe("isValidConnection with a structured source", () => {
  const state = {
    ...store.getState(),
    inputNode: {
      in1: {
        fileType: "hdf5",
        selectedFilePath: "f.h5",
        hdf5Path: "data/behavior",
        param: {},
      },
      in2: {
        fileType: "hdf5",
        selectedFilePath: "f.h5",
        hdf5Path: "data/image",
        param: {},
      },
      in3: {
        fileType: "hdf5",
        selectedFilePath: "other.h5",
        hdf5Path: "data/image",
        param: {},
      },
    },
    hdf5: { trees: { "f.h5": { isLoading: false, tree } } },
  } as unknown as RootState

  test("selectStructuredDatasetNdim reads the tree for the node's own file", () => {
    expect(selectStructuredDatasetNdim("in1")(state)).toBe(2)
    expect(selectStructuredDatasetNdim("in2")(state)).toBe(3)
    expect(selectStructuredDatasetNdim("in3")(state)).toBeUndefined()
    expect(selectStructuredDatasetNdim("nope")(state)).toBeUndefined()
  })

  test("2D dataset cannot reach an ImageData input, 3D can, unknown is allowed", () => {
    const spy = jest.spyOn(store, "getState").mockReturnValue(state)
    const conn = (source: string, target: string) => ({
      source,
      target: "algo",
      sourceHandle: `${source}--hdf5--HDF5Data`,
      targetHandle: `algo--image--${target}`,
    })
    expect(isValidConnection(conn("in1", "ImageData"))).toBe(false)
    expect(isValidConnection(conn("in1", "FluoData"))).toBe(true)
    expect(isValidConnection(conn("in2", "ImageData"))).toBe(true)
    expect(isValidConnection(conn("in3", "ImageData"))).toBe(true)
    spy.mockRestore()
  })

  test("typed handles keep the old rule", () => {
    const conn = (s: string, t: string) => ({
      source: "a",
      target: "b",
      sourceHandle: `a--out--${s}`,
      targetHandle: `b--in--${t}`,
    })
    expect(isValidConnection(conn("ImageData", "ImageData"))).toBe(true)
    expect(isValidConnection(conn("ImageData", "FluoData"))).toBe(false)
    expect(isValidConnection(conn("SpikingActivityData", "FluoData"))).toBe(
      true,
    )
    expect(isValidConnection(conn("FluoData", "BaseData"))).toBe(true)
  })
})

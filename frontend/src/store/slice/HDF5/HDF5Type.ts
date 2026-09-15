import { HDF5TreeDTO } from "api/hdf5/HDF5"

export const HDF5_SLICE_NAME = "hdf5"

export interface StructureTreeState<T> {
  isLoading: boolean
  tree: T[]
}

export interface HDF5Tree {
  trees: Record<string, StructureTreeState<HDF5TreeNodeType>>
}

export type HDF5TreeNodeType = HDF5TreeDTO

export const HDF5_TYPE_SET = {
  ALL: "all",
} as const

export type HDF5_TYPE = (typeof HDF5_TYPE_SET)[keyof typeof HDF5_TYPE_SET]

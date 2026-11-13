import { MatlabTreeDTO } from "api/matlab/Matlab"
import { StructureTreeState } from "store/slice/HDF5/HDF5Type"

export const MATLAB_SLICE_NAME = "matlab"

export interface MatlabTree {
  trees: Record<string, StructureTreeState<MatlabTreeNodeType>>
}

export type MatlabTreeNodeType = MatlabTreeDTO

export const MATLAB_TYPE_SET = {
  ALL: "all",
} as const

export type HDF5_TYPE = (typeof MATLAB_TYPE_SET)[keyof typeof MATLAB_TYPE_SET]

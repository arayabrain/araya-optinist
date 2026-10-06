import { createAsyncThunk } from "@reduxjs/toolkit"

import { getAlgoParamsApi } from "api/params/Params"
import { NodeDict } from "api/run/Run"
import { isAlgorithmNodePostData } from "api/run/RunUtils"
import {
  fetchWorkflowApi,
  reproduceWorkflowApi,
  importWorkflowConfigApi,
  importSampleDataApi,
  WorkflowConfigDTO,
  WorkflowWithResultDTO,
} from "api/workflow/Workflow"
import { WORKFLOW_SLICE_NAME } from "store/slice/Workflow/WorkflowType"
import { NodeParamChange, ParamMap } from "utils/param/ParamType"
import {
  convertToParamMap,
  hasOutdatedShape,
  reconcileParamMap,
} from "utils/param/ParamUtils"

type WithParamChanges<T> = T & { paramChanges: NodeParamChange[] }

export async function reconcileNodeParams(
  nodeDict: NodeDict,
): Promise<NodeParamChange[]> {
  const defaultsByAlgo = new Map<string, Promise<ParamMap>>()
  const changes: NodeParamChange[] = []
  await Promise.all(
    Object.values(nodeDict)
      .filter(isAlgorithmNodePostData)
      .map(async (node) => {
        const name = node.data?.label
        if (node.data == null || name == null) return
        let defaults = defaultsByAlgo.get(name)
        if (defaults == null) {
          defaults = getAlgoParamsApi(name).then(convertToParamMap)
          defaultsByAlgo.set(name, defaults)
        }
        try {
          const current = await defaults
          if (
            Object.keys(current).length === 0 ||
            hasOutdatedShape(node.data.param, current)
          ) {
            return
          }
          const { params, removed } = reconcileParamMap(
            node.data.param,
            current,
          )
          node.data.param = params
          if (removed.length > 0) {
            changes.push({ nodeId: node.id, name, removed })
          }
        } catch {
          // defaults unavailable for this node; its saved params stay as they are
        }
      }),
  )
  return changes
}

async function withReconciledParams<T extends WorkflowConfigDTO>(
  response: T,
): Promise<WithParamChanges<T>> {
  const paramChanges = await reconcileNodeParams(response.nodeDict)
  return { ...response, paramChanges }
}

export const fetchWorkflow = createAsyncThunk<
  WithParamChanges<WorkflowWithResultDTO>,
  number
>(`${WORKFLOW_SLICE_NAME}/fetchExperiment`, async (workspaceId, thunkAPI) => {
  try {
    const response = await fetchWorkflowApi(workspaceId)
    return await withReconciledParams(response)
  } catch (e) {
    return thunkAPI.rejectWithValue(e)
  }
})

export const reproduceWorkflow = createAsyncThunk<
  WithParamChanges<WorkflowWithResultDTO>,
  { workspaceId: number; uid: string }
>(
  `${WORKFLOW_SLICE_NAME}/reproduceWorkflow`,
  async ({ workspaceId, uid }, thunkAPI) => {
    try {
      const response = await reproduceWorkflowApi(workspaceId, uid)
      return await withReconciledParams(response)
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const importWorkflowConfig = createAsyncThunk<
  WithParamChanges<WorkflowConfigDTO>,
  { formData: FormData }
>(
  `${WORKFLOW_SLICE_NAME}/importWorkflowConfig`,
  async ({ formData }, thunkAPI) => {
    try {
      const response = await importWorkflowConfigApi(formData)
      return await withReconciledParams(response)
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const importSampleData = createAsyncThunk<
  boolean,
  { workspaceId: number; category: string }
>(
  `${WORKFLOW_SLICE_NAME}/importSampleData`,
  async ({ workspaceId, category }, thunkAPI) => {
    try {
      const response = await importSampleDataApi(workspaceId, category)
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

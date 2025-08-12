import { stringify } from "qs"

import { WorkflowWithResultDTO } from "api/workflow/Workflow"
import { DataviewDTO, DataviewParams } from "store/slice/Dataview/DataviewType"
import axios from "utils/axios"

export const getPublicDataviewRecordsApi = async (
  params: DataviewParams,
): Promise<DataviewDTO> => {
  const paramsNew = stringify(params, { indices: false })
  const response = await axios.get(`/public/dataview?${paramsNew}`)
  return response.data
}

export const getDataviewRecordsApi = async (
  params: DataviewParams,
): Promise<DataviewDTO> => {
  const paramsNew = stringify(params, { indices: false })
  const response = await axios.get(`/dataview?${paramsNew}`)
  return response.data
}

export async function publicDataviewReproduceWorkflowApi(
  workspaceId: number,
  uid: string,
): Promise<WorkflowWithResultDTO> {
  const response = await axios.get(
    `/public/dataview/workflow/reproduce/${workspaceId}/${uid}`,
  )
  return response.data
}

export const postPublishApi = async (
  id: number,
  status: "on" | "off",
): Promise<boolean> => {
  const response = await axios.post(`/dataview/publish/${id}/${status}`)
  return response.data
}

export const postPublishAllApi = async (
  status: "on" | "off",
  data: number[],
): Promise<boolean> => {
  const response = await axios.post(
    `/dataview/multiple/publish/${status}`,
    data,
  )
  return response.data
}

export const putAttributesApi = async (
  id: number,
  data: string,
): Promise<boolean> => {
  const response = await axios.put(`/dataview/metadata/${id}`, data, {
    headers: {
      "Content-Type": "application/json",
    },
  })
  return response.data
}

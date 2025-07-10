import { stringify } from "qs"

import { DataviewDTO, DataviewParams } from "store/slice/Dataview/DataviewType"
import axios from "utils/axios"

export const getExperimentsPublicApi = async (
  params: DataviewParams,
): Promise<DataviewDTO> => {
  const paramsNew = stringify(params, { indices: false })
  const response = await axios.get(`/public/experiments?${paramsNew}`)
  return response.data
}

export const getExperimentsApi = async (
  params: DataviewParams,
): Promise<DataviewDTO> => {
  const paramsNew = stringify(params, { indices: false })
  const response = await axios.get(`/expdb/experiments?${paramsNew}`)
  return response.data
}

export const postPublishApi = async (
  id: number,
  status: "on" | "off",
): Promise<boolean> => {
  const response = await axios.post(`/expdb/experiment/publish/${id}/${status}`)
  return response.data
}

export const postPublishAllApi = async (
  status: "on" | "off",
  data: number[],
): Promise<boolean> => {
  const response = await axios.post(
    `expdb/experiment/multiple/publish/${status}`,
    data,
  )
  return response.data
}

export const putAttributesApi = async (
  id: number,
  data: string,
): Promise<boolean> => {
  const response = await axios.put(`expdb/experiment/metadata/${id}`, data, {
    headers: {
      "Content-Type": "application/json",
    },
  })
  return response.data
}

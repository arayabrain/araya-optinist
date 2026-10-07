import type { AxiosRequestConfig } from "axios"

import { BASE_URL } from "const/API"
import axios from "utils/axios"
import { ParamDTO } from "utils/param/ParamType"

export async function getAlgoParamsApi(
  algoName: string,
  config?: AxiosRequestConfig,
): Promise<ParamDTO> {
  const response = await axios.get(
    `${BASE_URL}/params/${encodeURIComponent(algoName)}`,
    config,
  )
  return response.data
}

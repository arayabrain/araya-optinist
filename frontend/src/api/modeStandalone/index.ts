import axios from "utils/axios"

export const getModeStandaloneApi = async (): Promise<boolean> => {
  // Skip premium routing headers — this is a system-information endpoint
  // that must always reach the shared backend regardless of routing state.
  const res = await axios.get("/is_standalone", {
    _retryWithoutPremium: true,
  } as Parameters<typeof axios.get>[1])
  return res.data
}

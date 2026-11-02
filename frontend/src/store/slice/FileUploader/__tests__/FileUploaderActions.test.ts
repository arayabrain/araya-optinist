import { AxiosError } from "axios"

import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  jest,
} from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"

import { getStatusLoadViaUrlApi, GetStatusViaUrl } from "api/files/Files"
import {
  URL_UPLOAD_MAX_FAILED_POLLS,
  waitForUploadViaUrl,
} from "store/slice/FileUploader/FileUploaderActions"
import reducer from "store/slice/FileUploader/FileUploaderSlice"

jest.mock("api/files/Files")

const arg = {
  workspaceId: 1,
  file_name: "data.csv",
  upload_id: "this",
  requestId: "req",
}

const status = (fields: Partial<GetStatusViaUrl>): GetStatusViaUrl => ({
  total: 0,
  current: 0,
  error: null,
  done: false,
  upload_id: "this",
  ...fields,
})

const notFound = () => new AxiosError("Request failed with status code 404")

const run = async () => {
  const store = configureStore({
    reducer: { fileUploader: reducer },
    // A failed poll rejects with the axios error, as the app's other thunks do
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware({ serializableCheck: false }),
  })
  const result = await store.dispatch(waitForUploadViaUrl(arg))
  return { result, state: store.getState().fileUploader.req }
}

describe("waitForUploadViaUrl", () => {
  beforeEach(() => {
    // Polls without waiting the interval
    jest.spyOn(global, "setTimeout").mockImplementation(((cb: () => void) => {
      cb()
      return 0
    }) as unknown as typeof setTimeout)
  })

  afterEach(() => {
    jest.restoreAllMocks()
    jest.mocked(getStatusLoadViaUrlApi).mockReset()
  })

  it("finishes on done, not when current equals total", async () => {
    jest
      .mocked(getStatusLoadViaUrlApi)
      .mockResolvedValueOnce(status({}))
      .mockResolvedValueOnce(status({ total: 10, current: 10 }))
      .mockResolvedValueOnce(status({ total: 10, current: 10, done: true }))

    const { result, state } = await run()

    expect(waitForUploadViaUrl.fulfilled.match(result)).toBe(true)
    expect(getStatusLoadViaUrlApi).toHaveBeenCalledTimes(3)
    expect(getStatusLoadViaUrlApi).toHaveBeenCalledWith(1, "data.csv", "this")
    expect(state).toMatchObject({ pending: false, fulfilled: true })
  })

  it("stops on an error status, which does not read as finished", async () => {
    jest
      .mocked(getStatusLoadViaUrlApi)
      .mockResolvedValueOnce(status({ error: "No space left on device" }))

    const { result, state } = await run()

    expect(waitForUploadViaUrl.rejected.match(result)).toBe(true)
    expect(result.payload).toBe("No space left on device")
    expect(getStatusLoadViaUrlApi).toHaveBeenCalledTimes(1)
    expect(state).toMatchObject({
      pending: false,
      fulfilled: false,
      error: "No space left on device",
    })
  })

  it("keeps polling through a failed poll, which another worker answers", async () => {
    jest
      .mocked(getStatusLoadViaUrlApi)
      .mockRejectedValueOnce(notFound())
      .mockRejectedValueOnce(notFound())
      .mockResolvedValueOnce(status({ total: 10, current: 5 }))
      .mockRejectedValueOnce(notFound())
      .mockResolvedValueOnce(status({ total: 10, current: 10, done: true }))

    const { result, state } = await run()

    expect(waitForUploadViaUrl.fulfilled.match(result)).toBe(true)
    expect(getStatusLoadViaUrlApi).toHaveBeenCalledTimes(5)
    expect(state.error).toBeUndefined()
  })

  it("gives up after the limit of consecutive failed polls", async () => {
    jest.mocked(getStatusLoadViaUrlApi).mockRejectedValue(notFound())

    const { result, state } = await run()

    expect(waitForUploadViaUrl.rejected.match(result)).toBe(true)
    expect(getStatusLoadViaUrlApi).toHaveBeenCalledTimes(
      URL_UPLOAD_MAX_FAILED_POLLS,
    )
    expect(state).toMatchObject({ pending: false, fulfilled: false })
    expect(state.error).toBeTruthy()
  })
})

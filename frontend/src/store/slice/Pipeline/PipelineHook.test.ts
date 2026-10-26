import { beforeEach, describe, expect, jest, test } from "@jest/globals"
import { renderHook, waitFor } from "@testing-library/react"

// jest.mock factories may only reference `mock`-prefixed outer vars
const mockDispatch = jest.fn()
const mockUseSelector = jest.fn()
const mockEnqueue = jest.fn()
const mockLocation = { state: null }

jest.mock("react-redux", () => ({
  useDispatch: () => mockDispatch,
  useSelector: (selector: unknown) => mockUseSelector(selector),
}))
jest.mock("react-router-dom", () => ({
  useParams: () => ({ workspaceId: "1" }),
  useNavigate: () => () => undefined,
  useLocation: () => mockLocation,
}))
jest.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mockEnqueue }),
}))

// eslint-disable-next-line import/first
import { useRunPipeline } from "store/slice/Pipeline/PipelineHook"
// eslint-disable-next-line import/first
import { selectModeStandalone } from "store/slice/Standalone/StandaloneSeclector"
// eslint-disable-next-line import/first
import { fetchWorkflow } from "store/slice/Workflow/WorkflowActions"

const added = [
  { nodeId: "n1", name: "eta", added: ["ETA/sort_by_peak"], removed: [] },
]
const fetched = (paramChanges: unknown[]) =>
  Promise.resolve({
    type: fetchWorkflow.fulfilled.type,
    payload: { paramChanges },
  })

// Thunks are answered in dispatch order; plain actions pass through
function answerThunks(...results: unknown[]) {
  mockDispatch.mockImplementation((action) =>
    typeof action === "function" ? results.shift() : action,
  )
}

const notices = () =>
  mockEnqueue.mock.calls
    .map(([message]) => String(message))
    .filter((message) =>
      message.startsWith("Parameters updated to the current version"),
    )
const ETA_NOTICE =
  "Parameters updated to the current version: eta (added: ETA/sort_by_peak)"
const standalone = () =>
  mockUseSelector.mockImplementation(
    (selector) => selector === selectModeStandalone,
  )

describe("useRunPipeline: opening a workspace", () => {
  beforeEach(() => {
    mockDispatch.mockReset()
    mockEnqueue.mockReset()
    mockUseSelector.mockReset()
  })

  test("standalone: names the params added on load", async () => {
    standalone()
    answerThunks(fetched(added))
    const { unmount } = renderHook(() => useRunPipeline())

    await waitFor(() => expect(notices()).toEqual([ETA_NOTICE]))
    unmount()
  })

  test("multi-user: names the params added on load", async () => {
    mockUseSelector.mockReturnValue(false)
    answerThunks({ unwrap: () => Promise.resolve({}) }, fetched(added))
    const { unmount } = renderHook(() => useRunPipeline())

    await waitFor(() => expect(notices()).toEqual([ETA_NOTICE]))
    unmount()
  })

  test.each([
    ["nothing changed", fetched([])],
    ["the load failed", Promise.resolve({ type: fetchWorkflow.rejected.type })],
  ])("stays quiet when %s", async (_, result) => {
    standalone()
    answerThunks(result)
    const { unmount } = renderHook(() => useRunPipeline())
    await waitFor(() =>
      expect(mockDispatch).toHaveBeenCalledWith(expect.any(Function)),
    )
    await result
    unmount()
    expect(notices()).toEqual([])
  })
})

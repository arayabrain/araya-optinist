/* eslint-disable no-undef */
import "@testing-library/jest-dom"

import { beforeEach, describe, expect, it } from "@jest/globals"
import { fireEvent, render, waitFor } from "@testing-library/react"

import { ImportWorkflowConfigButton } from "components/Workspace/FlowChart/Buttons/ImportWorkflowConfig"
import { NODE_TYPE_SET } from "store/slice/FlowElement/FlowElementType"

const mockEnqueue = jest.fn()
const mockDispatch = jest.fn()

jest.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mockEnqueue }),
}))
jest.mock("react-redux", () => ({
  useDispatch: () => mockDispatch,
  useSelector: () => false,
}))

const UPLOAD_HINT =
  "Upload the workflow's input files to this workspace before running"

const node = (id: string, type: string) => ({
  id,
  position: { x: 0, y: 0 },
  data: { label: id, type },
})

const importYaml = (nodeDict: Record<string, unknown>) => {
  mockDispatch.mockImplementation(() => ({
    unwrap: () => Promise.resolve({ nodeDict, edgeDict: {}, paramChanges: [] }),
  }))
  const { container } = render(<ImportWorkflowConfigButton />)
  const input = container.querySelector(
    "input[type=\"file\"]",
  ) as HTMLInputElement
  const setValue = jest.fn()
  Object.defineProperty(input, "value", { set: setValue, get: () => "" })
  fireEvent.change(input, {
    target: { files: [new File(["a: 1"], "workflow.yaml")] },
  })
  return { input, setValue }
}

describe("ImportWorkflowConfigButton", () => {
  beforeEach(() => {
    mockEnqueue.mockReset()
    mockDispatch.mockReset()
  })

  it("asks for the input files when the yaml has an input node", async () => {
    importYaml({ input_1: node("input_1", NODE_TYPE_SET.INPUT) })

    await waitFor(() =>
      expect(mockEnqueue).toHaveBeenCalledWith(UPLOAD_HINT, {
        variant: "info",
      }),
    )
  })

  it("stays quiet about input files when the yaml has none", async () => {
    importYaml({ algo_1: node("algo_1", NODE_TYPE_SET.ALGORITHM) })

    await waitFor(() =>
      expect(mockEnqueue).toHaveBeenCalledWith("Import success", {
        variant: "success",
      }),
    )
    expect(mockEnqueue).not.toHaveBeenCalledWith(UPLOAD_HINT, expect.anything())
  })

  it("clears the input so the same file can be imported again", () => {
    const { setValue } = importYaml({})

    expect(setValue).toHaveBeenCalledWith("")
  })
})

import { OptionsObject, SnackbarKey } from "notistack"

import { describe, expect, jest, test } from "@jest/globals"

import {
  handleWorkflowYamlError,
  WORKFLOW_YAML_ERROR,
} from "store/slice/Pipeline/PipelineUtils"

describe("handleWorkflowYamlError", () => {
  const enqueue = () =>
    jest.fn<SnackbarKey, [string, OptionsObject?]>(() => "key")

  test("a 422 with a specific detail shows that detail", () => {
    const snackbar = enqueue()
    handleWorkflowYamlError(
      {
        response: {
          status: 422,
          data: {
            detail:
              "HDF5FileNode dataset 'data/behavior' has shape (500, 8) but suite2p_file_convert.image expects ImageData (3D)",
          },
        },
      },
      snackbar,
    )
    expect(snackbar.mock.calls[0][0]).toContain("expects ImageData")
    expect(snackbar.mock.calls[0][1]).toMatchObject({ variant: "warning" })
  })

  test("a 422 carrying the yaml message keeps the FAQ snackbar", () => {
    const snackbar = enqueue()
    handleWorkflowYamlError(
      { response: { status: 422, data: { detail: WORKFLOW_YAML_ERROR } } },
      snackbar,
    )
    expect(snackbar.mock.calls[0][0]).toContain(WORKFLOW_YAML_ERROR)
    expect(snackbar.mock.calls[0][1]).toHaveProperty("action")
  })

  test("a 422 without detail keeps the FAQ snackbar", () => {
    const snackbar = enqueue()
    handleWorkflowYamlError({ response: { status: 422 } }, snackbar)
    expect(snackbar.mock.calls[0][0]).toContain(WORKFLOW_YAML_ERROR)
  })

  test("a 422 yaml message with a specific key keeps text and FAQ action", () => {
    const snackbar = enqueue()
    const detail = `${WORKFLOW_YAML_ERROR}: unknown parameter 'transpose' for dpca; reset the node's parameters and run again`
    handleWorkflowYamlError(
      { response: { status: 422, data: { detail } } },
      snackbar,
    )
    expect(snackbar.mock.calls[0][0]).toContain("unknown parameter 'transpose'")
    expect(snackbar.mock.calls[0][1]).toHaveProperty("action")
  })

  test("any other status is the generic failure", () => {
    const snackbar = enqueue()
    handleWorkflowYamlError({ response: { status: 500 } }, snackbar)
    expect(snackbar.mock.calls[0][0]).toBe("Failed to Run workflow")
  })
})

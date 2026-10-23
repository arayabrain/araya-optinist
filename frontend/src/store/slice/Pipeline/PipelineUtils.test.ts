import { ReactElement } from "react"

import { OptionsObject, SnackbarKey } from "notistack"

import { describe, expect, jest, test } from "@jest/globals"

import {
  WORKFLOW_CONCURRENCY_LIMIT,
  WorkflowConcurrencyLimitDetail,
} from "api/run/Run"
import { NODE_RESULT_STATUS } from "store/slice/Pipeline/PipelineType"
import {
  convertToRunResult,
  handleWorkflowYamlError,
  WORKFLOW_YAML_ERROR,
} from "store/slice/Pipeline/PipelineUtils"

describe("convertToRunResult", () => {
  test("a success node with no outputPaths stays a success", () => {
    const result = convertToRunResult({
      slice_pos01: {
        status: "success",
        message: "data_slice success",
        name: "data_slice",
      },
    })
    expect(result.slice_pos01).toEqual({
      status: NODE_RESULT_STATUS.SUCCESS,
      message: "data_slice success",
      name: "data_slice",
      outputPaths: {},
    })
  })

  test("an error node stays an error", () => {
    const result = convertToRunResult({
      n1: { status: "error", message: "boom", name: "suite2p" },
    })
    expect(result.n1.status).toBe(NODE_RESULT_STATUS.ERROR)
  })
})

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

  test("the FAQ action opens the FAQ entry", () => {
    const snackbar = enqueue()
    const open = jest.spyOn(window, "open").mockImplementation(() => null)
    handleWorkflowYamlError({ response: { status: 422 } }, snackbar)
    const action = snackbar.mock.calls[0][1]?.action as (
      key: SnackbarKey,
    ) => ReactElement
    action("key").props.onMouseDown({ stopPropagation: jest.fn() })
    expect(open).toHaveBeenCalledWith(
      "https://github.com/oist/optinist/wiki/FAQ#error-yaml-file-error",
      "_blank",
    )
    open.mockRestore()
  })

  test("a 422 without detail keeps the FAQ snackbar", () => {
    const snackbar = enqueue()
    handleWorkflowYamlError({ response: { status: 422 } }, snackbar)
    expect(snackbar.mock.calls[0][0]).toContain(WORKFLOW_YAML_ERROR)
  })

  test("a 422 yaml message with a specific key keeps text and FAQ action", () => {
    const snackbar = enqueue()
    const detail = `${WORKFLOW_YAML_ERROR}: unknown parameters 'transpose' for dpca; reset the node's parameters and run again`
    handleWorkflowYamlError(
      { response: { status: 422, data: { detail } } },
      snackbar,
    )
    expect(snackbar.mock.calls[0][0]).toContain(
      "unknown parameters 'transpose'",
    )
    expect(snackbar.mock.calls[0][1]).toHaveProperty("action")
  })

  test("any other status is the generic failure", () => {
    const snackbar = enqueue()
    handleWorkflowYamlError({ response: { status: 500 } }, snackbar)
    expect(snackbar.mock.calls[0][0]).toBe("Failed to Run workflow")
  })
})

describe("handleWorkflowYamlError at the concurrent-run limit", () => {
  const enqueue = () =>
    jest.fn<SnackbarKey, [string, OptionsObject?]>(() => "key")

  const limitDetail = (
    upgradeAvailable: boolean,
  ): WorkflowConcurrencyLimitDetail => ({
    code: WORKFLOW_CONCURRENCY_LIMIT,
    message: "You already have 1 workflow running, and the Free plan ...",
    plan: "Free",
    limit: 1,
    active: 1,
    upgrade_available: upgradeAvailable,
  })

  test("shows the server's message as a warning, not the generic failure", () => {
    const snackbar = enqueue()
    handleWorkflowYamlError(
      { response: { status: 429, data: { detail: limitDetail(false) } } },
      snackbar,
    )
    expect(snackbar.mock.calls[0][0]).toBe(limitDetail(false).message)
    expect(snackbar.mock.calls[0][1]).toMatchObject({ variant: "warning" })
    expect(snackbar.mock.calls[0][1]?.action).toBeUndefined()
  })

  test("offers the plan page when another plan allows more runs", () => {
    const snackbar = enqueue()
    const open = jest.spyOn(window, "open").mockImplementation(() => null)
    handleWorkflowYamlError(
      { response: { status: 429, data: { detail: limitDetail(true) } } },
      snackbar,
    )
    const action = snackbar.mock.calls[0][1]?.action as (
      key: SnackbarKey,
    ) => ReactElement
    action("key").props.onMouseDown({ stopPropagation: jest.fn() })
    expect(open).toHaveBeenCalledWith("/subscription", "_blank")
    open.mockRestore()
  })

  test("a 429 without the limit's code is the generic failure", () => {
    const snackbar = enqueue()
    handleWorkflowYamlError(
      { response: { status: 429, data: { detail: "Too Many Requests" } } },
      snackbar,
    )
    expect(snackbar.mock.calls[0][0]).toBe("Failed to Run workflow")
  })
})

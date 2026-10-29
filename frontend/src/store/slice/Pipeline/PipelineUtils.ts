import { createElement, MouseEvent } from "react"

import { OptionsObject, SnackbarKey } from "notistack"

interface ApiError {
  response?: {
    status?: number
    data?: unknown
  }
  message?: string
}

import {
  RunPostData,
  RunResultDTO,
  OutputPathsDTO,
  WORKFLOW_CONCURRENCY_LIMIT,
  WorkflowConcurrencyLimitDetail,
} from "api/run/Run"
import { toDataType } from "store/slice/DisplayData/DisplayDataUtils"
import { NODE_TYPE_SET } from "store/slice/FlowElement/FlowElementType"
import {
  RunResult,
  OutputPaths,
  NODE_RESULT_STATUS,
  PipelineType,
  RUN_STATUS,
  StartedPipeline,
  NodeResultPending,
  NodeResultError,
  NodeResultSuccess,
  NodeResult,
  PROCESS_TYPE,
} from "store/slice/Pipeline/PipelineType"

export function isNodeResultPending(
  nodeResult: NodeResult,
): nodeResult is NodeResultPending {
  return nodeResult.status === NODE_RESULT_STATUS.PENDING
}

export function isNodeResultSuccess(
  nodeResult: NodeResult,
): nodeResult is NodeResultSuccess {
  return nodeResult.status === NODE_RESULT_STATUS.SUCCESS
}

export function isNodeResultError(
  nodeResult: NodeResult,
): nodeResult is NodeResultError {
  return nodeResult.status === NODE_RESULT_STATUS.ERROR
}

export function isStartedPipeline(
  pipeline: PipelineType,
): pipeline is StartedPipeline {
  return (
    pipeline.status === RUN_STATUS.START_SUCCESS ||
    pipeline.status === RUN_STATUS.FINISHED ||
    pipeline.status === RUN_STATUS.ABORTED
  )
}

export function getInitialRunResult(runPostData: RunPostData) {
  const initialResult: RunResult = {}
  Object.values(runPostData.nodeDict)
    .filter(({ data }) => data?.type === NODE_TYPE_SET.ALGORITHM)
    .forEach(({ id, data }) => {
      initialResult[id] = {
        status: NODE_RESULT_STATUS.PENDING,
        name: data?.label ?? "",
      }
    })

  // NOTE: Force the addition of a parameter here
  //     to confirm post_process processing.
  initialResult[PROCESS_TYPE.POST_PROCESS.id] = {
    status: NODE_RESULT_STATUS.PENDING,
    name: PROCESS_TYPE.POST_PROCESS.name,
  }

  return initialResult
}

export function convertToRunResult(dto: RunResultDTO) {
  const result: RunResult = {}
  Object.entries(dto).forEach(([nodeId, nodeResultDto]) => {
    if (nodeResultDto.status === "success") {
      result[nodeId] = {
        status: NODE_RESULT_STATUS.SUCCESS,
        message: nodeResultDto.message,
        name: nodeResultDto.name,
        outputPaths: convertToOutputPath(nodeResultDto.outputPaths ?? {}),
      }
    } else if (nodeResultDto.status === "running") {
      result[nodeId] = {
        status: NODE_RESULT_STATUS.PENDING,
        name: nodeResultDto.name,
      }
    } else {
      result[nodeId] = {
        status: NODE_RESULT_STATUS.ERROR,
        message: nodeResultDto.message,
        name: nodeResultDto.name,
      }
    }
  })
  return result
}

function convertToOutputPath(dto: OutputPathsDTO) {
  const result: OutputPaths = {}
  Object.entries(dto).forEach(([functionPath, value]) => {
    result[functionPath] = {
      path: value.path,
      data_shape: value.data_shape,
      type: toDataType(value.type),
    }
  })
  return result
}

// Handle workflow yaml error occurring when using v1.0 yaml in v2.0
export const WORKFLOW_YAML_ERROR = "Workflow yaml error, see FAQ"
// Matched as a prefix so rewording either side of the wire keeps the FAQ link.
const WORKFLOW_YAML_ERROR_PREFIX = "Workflow yaml error"

const SUBSCRIPTION_PAGE = "/subscription"

function isConcurrencyLimitDetail(
  detail: unknown,
): detail is WorkflowConcurrencyLimitDetail {
  if (typeof detail !== "object" || detail === null) return false
  const { code, message } = detail as Partial<WorkflowConcurrencyLimitDetail>
  return code === WORKFLOW_CONCURRENCY_LIMIT && typeof message === "string"
}

// A native link: focusable, and opened by Enter as well as by a click
function linkAction(label: string, url: string) {
  return function LinkAction(_key: SnackbarKey) {
    return createElement(
      "a",
      {
        href: url,
        target: "_blank",
        rel: "noopener noreferrer",
        onMouseDown: (e: MouseEvent<HTMLAnchorElement>) => e.stopPropagation(),
        style: {
          cursor: "pointer",
          textDecoration: "underline",
        },
        className: "text-inherit",
      },
      label,
    )
  }
}

export function handleWorkflowYamlError(
  error: ApiError,
  enqueueSnackbar: (message: string, options?: OptionsObject) => SnackbarKey,
): void {
  const detail = (error?.response?.data as { detail?: unknown } | undefined)
    ?.detail
  // At the plan's concurrent-run limit: the server's message says what to do
  if (error?.response?.status === 429 && isConcurrencyLimitDetail(detail)) {
    enqueueSnackbar(detail.message, {
      variant: "warning",
      autoHideDuration: 30000,
      action: detail.upgrade_available
        ? linkAction("See plans", SUBSCRIPTION_PAGE)
        : undefined,
    })
    return
  }
  if (
    error?.response?.status === 422 &&
    typeof detail === "string" &&
    !detail.startsWith(WORKFLOW_YAML_ERROR_PREFIX)
  ) {
    enqueueSnackbar(detail, { variant: "warning", autoHideDuration: 30000 })
    return
  }
  // Catch workflow yaml parameter errors
  if (error?.response?.status === 422) {
    const snackbarOptions: OptionsObject = {
      variant: "warning",
      autoHideDuration: 30000,
      action: linkAction(
        "Click here",
        "https://github.com/oist/optinist/wiki/FAQ#error-yaml-file-error",
      ),
    }
    enqueueSnackbar(
      `${typeof detail === "string" ? detail : WORKFLOW_YAML_ERROR}\n`,
      snackbarOptions,
    )
  } else {
    enqueueSnackbar("Failed to Run workflow", { variant: "error" })
  }
}

import type { Node } from "reactflow"

import { beforeEach, describe, expect, it, jest } from "@jest/globals"

import { getAlgoParamsApi } from "api/params/Params"
import type { AlgorithmNodePostData, NodeDict } from "api/run/Run"
import {
  RECONCILE_TIMEOUT_MS,
  reconcileNodeParams,
} from "store/slice/Workflow/WorkflowActions"
import { convertToParamMap } from "utils/param/ParamUtils"

jest.mock("api/params/Params")

const mockedGetAlgoParams = getAlgoParamsApi as jest.MockedFunction<
  typeof getAlgoParamsApi
>

function algoNode(
  id: string,
  label: string,
  param: Record<string, unknown>,
  extra: Partial<AlgorithmNodePostData> = {},
): Node<AlgorithmNodePostData> {
  return {
    id,
    type: "AlgorithmNode",
    position: { x: 0, y: 0 },
    data: {
      label,
      type: "algorithm",
      path: `caiman/${label}`,
      param: convertToParamMap(param),
      ...extra,
    } as AlgorithmNodePostData,
  }
}

const mcDefaults = { border_nan: "copy", advanced: { nb: 2 } }

describe("reconcileNodeParams", () => {
  beforeEach(() => {
    mockedGetAlgoParams.mockReset()
  })

  it("drops stale keys, reports them, and fetches defaults once per algorithm", async () => {
    mockedGetAlgoParams.mockResolvedValue(mcDefaults)
    const nodeDict: NodeDict = {
      a: algoNode("a", "caiman_mc", {
        border_nan: "min",
        use_cuda: false,
        advanced: { nb: 2 },
      }),
      b: algoNode("b", "caiman_mc", {
        border_nan: "copy",
        advanced: { nb: 3 },
      }),
    }
    const changes = await reconcileNodeParams(nodeDict)
    expect(mockedGetAlgoParams).toHaveBeenCalledTimes(1)
    expect(mockedGetAlgoParams).toHaveBeenCalledWith("caiman_mc", {
      timeout: RECONCILE_TIMEOUT_MS,
    })
    expect(changes).toEqual([
      { nodeId: "a", name: "caiman_mc", removed: ["use_cuda"] },
    ])
    expect(nodeDict.a.data?.param).toEqual(
      convertToParamMap({ border_nan: "min", advanced: { nb: 2 } }),
    )
    expect(nodeDict.b.data?.param).toEqual(
      convertToParamMap({ border_nan: "copy", advanced: { nb: 3 } }),
    )
  })

  it("keeps saved params when the defaults request fails", async () => {
    mockedGetAlgoParams.mockRejectedValue(new Error("network"))
    const saved = { border_nan: "min", use_cuda: false }
    const nodeDict: NodeDict = { a: algoNode("a", "caiman_mc", saved) }
    await expect(reconcileNodeParams(nodeDict)).resolves.toEqual([])
    expect(nodeDict.a.data?.param).toEqual(convertToParamMap(saved))
  })

  it("keeps saved params when the algorithm has no defaults", async () => {
    mockedGetAlgoParams.mockResolvedValue({})
    const saved = { k: 1, j: 2 }
    const nodeDict: NodeDict = { a: algoNode("a", "plugin_algo", saved) }
    expect(await reconcileNodeParams(nodeDict)).toEqual([])
    expect(nodeDict.a.data?.param).toEqual(convertToParamMap(saved))
  })

  it("leaves a v1-shaped tree alone so the backend can reject it", async () => {
    mockedGetAlgoParams.mockResolvedValue(mcDefaults)
    const saved = { border_nan: "copy", nb: 2 } // nb lives under advanced today
    const nodeDict: NodeDict = { a: algoNode("a", "caiman_mc", saved) }
    expect(await reconcileNodeParams(nodeDict)).toEqual([])
    expect(nodeDict.a.data?.param).toEqual(convertToParamMap(saved))
  })

  it("does not touch dataFilterParam", async () => {
    mockedGetAlgoParams.mockResolvedValue(mcDefaults)
    const dataFilterParam = { dim1: [{ start: 0, end: 5 }] }
    const nodeDict: NodeDict = {
      a: algoNode(
        "a",
        "caiman_mc",
        { border_nan: "copy", use_cuda: false, advanced: { nb: 2 } },
        { dataFilterParam },
      ),
    }
    await reconcileNodeParams(nodeDict)
    expect(nodeDict.a.data?.dataFilterParam).toBe(dataFilterParam)
  })
})

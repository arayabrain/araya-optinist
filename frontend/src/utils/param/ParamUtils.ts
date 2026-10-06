import type { OptionsObject, SnackbarKey } from "notistack"

import {
  NodeParamChange,
  ParamDTO,
  ParamChild,
  ParamParent,
  ParamType,
  ParamMap,
} from "utils/param/ParamType"

export function getChildParam(
  path: string,
  ParamMap: ParamMap,
): ParamChild | null {
  let result: ParamChild | null = null
  for (const node of Object.values(ParamMap)) {
    if (isParamChild(node)) {
      if (node.path === path) {
        result = node
      }
    } else {
      result = getChildParam(path, node.children)
    }
    if (result != null) {
      break
    }
  }
  return result
}

export function isParamChild(param: ParamType): param is ParamChild {
  return param.type === "child"
}

export function isParamParent(param: ParamType): param is ParamParent {
  return param.type === "parent"
}

function isDictObject(value: unknown): value is { [key: string]: unknown } {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

const PATH_SEPARATOR = "/"

export function convertToParamMap(dto: ParamDTO, keyList?: string[]): ParamMap {
  const ParamMap: ParamMap = {}
  Object.entries(dto).forEach(([name, value]) => {
    const kList = keyList ?? []
    if (isDictObject(value)) {
      kList.push(name)
      ParamMap[name] = {
        type: "parent",
        children: convertToParamMap(value, kList),
      }
    } else {
      ParamMap[name] = {
        type: "child",
        value,
        path: kList.concat([name]).join(PATH_SEPARATOR),
      }
    }
  })
  return ParamMap
}

export function equalsParamMap(a: ParamMap, b: ParamMap) {
  if (a === b) {
    return true
  }
  const aArray = Object.keys(a)
  const bArray = Object.keys(b)
  return (
    aArray.length === bArray.length &&
    aArray.every((aKey) => {
      const aValue = a[aKey]
      const bValue = b[aKey]
      return equalsParam(aValue, bValue)
    })
  )
}

function equalsParam(a: ParamType, b: ParamType): boolean {
  if (a === b) {
    return true
  }
  if (isParamChild(a) && isParamChild(b)) {
    return equalsParamChild(a, b)
  } else if (isParamParent(a) && isParamParent(b)) {
    const aArray = Object.keys(a)
    const bArray = Object.keys(b)
    return (
      aArray.length === bArray.length &&
      aArray.every((aKey) => {
        const aValue = a.children[aKey]
        const bValue = b.children[aKey]
        return equalsParam(aValue, bValue)
      })
    )
  } else {
    return false
  }
}

function equalsParamChild(a: ParamChild, b: ParamChild) {
  return a.path === b.path && a.value === b.value
}

/**
 * Format params for display by removing type/path properties and flattening structure
 * This function reverses the structure created by convertToParamMap():
 * - Removes 'type' and 'path' properties that were added by convertToParamMap
 * - Promotes 'value' to parent level for child nodes (reverses ParamChild structure)
 * - Flattens 'children' by promoting them one level up (reverses ParamParent structure)
 *
 * @param params - The ParamMap or param object to format
 * @returns Simplified object structure for display
 */
export function formatParamsForDisplay(
  params: Record<string, unknown>,
): Record<string, unknown> {
  const formatted: Record<string, unknown> = {}

  const processValue = (value: unknown): unknown => {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const obj = value as Record<string, unknown>

      // Handle ParamChild structure (type: 'child', value, path)
      if (obj.type === "child" && "value" in obj) {
        return obj.value
      }

      // Handle ParamParent structure (type: 'parent', children)
      if (obj.type === "parent" && "children" in obj) {
        return formatParamsForDisplay(obj.children as Record<string, unknown>)
      }

      // Process regular objects recursively
      const processedObj: Record<string, unknown> = {}
      for (const [key, val] of Object.entries(obj)) {
        // Skip type and path properties
        if (key !== "type" && key !== "path") {
          processedObj[key] = processValue(val)
        }
      }
      return processedObj
    }

    return value
  }

  for (const [key, value] of Object.entries(params)) {
    formatted[key] = processValue(value)
  }

  return formatted
}

export function hasOutdatedShape(saved: ParamMap, defaults: ParamMap): boolean {
  const savedKeys = Object.keys(saved)
  const unknown = savedKeys.filter((key) => !(key in defaults))
  if (unknown.length === 0) return false
  const noOverlap = unknown.length === savedKeys.length
  const missingGroup = Object.entries(defaults).some(
    ([key, param]) => isParamParent(param) && !(key in saved),
  )
  return noOverlap || missingGroup
}

export function reconcileParamMap(
  saved: ParamMap,
  defaults: ParamMap,
): { params: ParamMap; removed: string[] } {
  const removed: string[] = []
  const walk = (s: ParamMap, d: ParamMap, prefix: string[]): ParamMap => {
    const out: ParamMap = {}
    Object.entries(s).forEach(([key, sParam]) => {
      const dParam = d[key]
      if (dParam == null || dParam.type !== sParam.type) {
        removed.push(prefix.concat(key).join(PATH_SEPARATOR))
      } else if (isParamParent(sParam) && isParamParent(dParam)) {
        out[key] = {
          type: "parent",
          children: walk(sParam.children, dParam.children, prefix.concat(key)),
        }
      } else {
        out[key] = sParam
      }
    })
    return out
  }
  return { params: walk(saved, defaults, []), removed }
}

export function describeNodeParamChanges(changes: NodeParamChange[]): string {
  return changes
    .map(({ name, removed }) => `${name} (removed: ${removed.join(", ")})`)
    .join(" / ")
}

export function notifyParamChanges(
  changes: NodeParamChange[] | undefined,
  enqueueSnackbar: (message: string, options?: OptionsObject) => SnackbarKey,
): void {
  if (changes == null || changes.length === 0) return
  enqueueSnackbar(
    `Parameters updated to the current version: ${describeNodeParamChanges(changes)}`,
    { variant: "info", autoHideDuration: 15000 },
  )
}

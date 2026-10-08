// Stacking only makes sense for several traces: a lone trace keeps its own units,
// so a normalised result (z-scored, min-max) reads as what the node produced.
export function stackedTrace(
  y: number[],
  index: number,
  span: number,
  traceCount: number,
): number[] {
  if (traceCount < 2) return y
  const mean = y.reduce((a, b) => a + b, 0) / y.length
  const std =
    span *
    Math.sqrt(y.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / y.length)
  return y.map((value) => (value - mean) / (std + 1e-10) + index)
}

export function visibleTraceCount(
  drawOrderList: string[],
  dataKeys: string[],
): number {
  return drawOrderList.filter((key) => dataKeys.includes(key)).length
}

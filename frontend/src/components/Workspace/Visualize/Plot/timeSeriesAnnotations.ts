import { Annotations } from "plotly.js"

export type AnnotationTrace = { y: (number | undefined)[] }

// Offset in pixels, not data units, so an axis not starting at 0 keeps the label on the plot.
export function buildAnnotations(
  drawOrderList: string[],
  xValues: string[],
  data: { [key: string]: AnnotationTrace },
): Partial<Annotations>[] {
  const lastX = Number(xValues[xValues.length - 1])
  if (!Number.isFinite(lastX)) return []

  return drawOrderList
    .filter((key) => Number.isFinite(lastY(data[key])))
    .map((key) => ({
      x: lastX,
      xanchor: "left" as const,
      xshift: 8,
      y: lastY(data[key]),
      xref: "x" as const,
      yref: "y" as const,
      text: `cell: ${key}`,
      arrowhead: 1,
      ax: 0,
      ay: -10,
    }))
}

function lastY(trace: AnnotationTrace | undefined) {
  return trace?.y[trace.y.length - 1]
}

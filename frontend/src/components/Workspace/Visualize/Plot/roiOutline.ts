export function outlineRoi(roi: (number | null)[][]): (number | null)[][] {
  const at = (y: number, x: number) => roi[y]?.[x]
  return roi.map((row, y) =>
    row.map((id, x) =>
      id != null &&
      (at(y - 1, x) !== id ||
        at(y + 1, x) !== id ||
        at(y, x - 1) !== id ||
        at(y, x + 1) !== id)
        ? id
        : null,
    ),
  )
}

export function roiIdAt(
  point: { curveNumber: number; x: unknown; y: unknown; z?: number | null },
  roi: (number | null)[][],
): number | null {
  // an outline gap has no hover point on the ROI trace, so the click lands on the image trace
  const id =
    point.curveNumber >= 1 && point.z != null
      ? point.z
      : roi[Math.round(Number(point.y))]?.[Math.round(Number(point.x))]
  return id != null && id >= 0 ? id : null
}

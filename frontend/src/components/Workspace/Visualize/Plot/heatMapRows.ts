import { HeatMapData } from "api/visualizations/Outputs"
import { HeatMapZRangeMode } from "store/slice/VisualizeItem/VisualizeItemType"

export function filterHeatMapRows(
  z: HeatMapData,
  index: string[],
  selectedRois: number[] | undefined,
): { z: HeatMapData; index: string[] } {
  if (!selectedRois?.length) return { z, index }

  const keep = index.flatMap((roi, i) =>
    selectedRois.includes(Number(roi)) ? [i] : [],
  )
  return { z: keep.map((i) => z[i]), index: keep.map((i) => index[i]) }
}

// For ROI-by-ROI matrices such as correlation, the columns follow the same selection.
export function filterHeatMapColumns(
  z: HeatMapData,
  columns: (number | string)[],
  selectedRois: number[] | undefined,
): { z: HeatMapData; columns: (number | string)[] } {
  if (!selectedRois?.length) return { z, columns }

  const keep = columns.flatMap((roi, i) =>
    selectedRois.includes(Number(roi)) ? [i] : [],
  )
  return {
    z: z.map((row) => keep.map((i) => row[i])),
    columns: keep.map((i) => columns[i]),
  }
}

// Colour range of the whole matrix: the finite min and max (auto), the p-th to
// (100 - p)-th percentile of the finite values, or the typed range. A percentile or
// manual range that is empty falls back to auto.
export function heatMapZRange(
  z: HeatMapData | undefined,
  mode: HeatMapZRangeMode = "auto",
  percentile = 2,
  manual: [number, number] | null = null,
): [number, number] | undefined {
  if (mode === "manual" && manual != null && manual[0] < manual[1]) {
    return manual
  }
  const values: number[] = []
  z?.forEach((row) =>
    row.forEach((v) => {
      if (Number.isFinite(v)) values.push(v)
    }),
  )
  if (values.length === 0) return undefined
  values.sort((a, b) => a - b)
  const last = values.length - 1
  if (mode === "percentile" && percentile > 0 && percentile < 50) {
    const lo = values[Math.floor((last * percentile) / 100)]
    const hi = values[Math.ceil((last * (100 - percentile)) / 100)]
    if (lo < hi) return [lo, hi]
  }
  return [values[0], values[last]]
}

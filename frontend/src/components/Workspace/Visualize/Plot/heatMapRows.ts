import { HeatMapData } from "api/visualizations/Outputs"
import {
  HeatMapZRange,
  HeatMapZRangeMode,
} from "store/slice/VisualizeItem/VisualizeItemType"

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
// (100 - p)-th percentile of the finite values, or the typed range, where a bound
// left empty is the data's. A percentile or manual range that is empty falls back
// to auto.
export function heatMapZRange(
  z: HeatMapData | undefined,
  mode: HeatMapZRangeMode = "auto",
  percentile = 2,
  manual: HeatMapZRange | null = null,
): [number, number] | undefined {
  let min = Infinity
  let max = -Infinity
  let count = 0
  z?.forEach((row) =>
    row.forEach((v) => {
      if (Number.isFinite(v)) {
        if (v < min) min = v
        if (v > max) max = v
        count++
      }
    }),
  )
  if (mode === "manual" && manual != null) {
    const lo = manual[0] ?? min
    const hi = manual[1] ?? max
    if (lo < hi) return [lo, hi]
  }
  if (min > max) return undefined
  if (mode === "percentile" && percentile > 0 && percentile < 50) {
    // a typed array sorts numerically without a comparator, several times faster
    const values = new Float64Array(count)
    let i = 0
    z?.forEach((row) =>
      row.forEach((v) => {
        if (Number.isFinite(v)) values[i++] = v
      }),
    )
    values.sort()
    const last = values.length - 1
    const lo = values[Math.floor((last * percentile) / 100)]
    const hi = values[Math.ceil((last * (100 - percentile)) / 100)]
    if (lo < hi) return [lo, hi]
  }
  return [min, max]
}

// True when a typed manual range is empty on this data, so the data range is drawn.
export function manualZRangeIgnored(
  z: HeatMapData | undefined,
  manual: HeatMapZRange | null,
): boolean {
  if (manual == null || (manual[0] == null && manual[1] == null)) return false
  const data = heatMapZRange(z)
  if (data == null) return false
  return !((manual[0] ?? data[0]) < (manual[1] ?? data[1]))
}

import { HeatMapData } from "api/visualizations/Outputs"

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

export function heatMapZRange(
  z: HeatMapData | undefined,
): [number, number] | undefined {
  let min = Infinity
  let max = -Infinity
  z?.forEach((row) =>
    row.forEach((v) => {
      if (!Number.isFinite(v)) return
      if (v < min) min = v
      if (v > max) max = v
    }),
  )
  return min <= max ? [min, max] : undefined
}

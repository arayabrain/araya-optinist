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

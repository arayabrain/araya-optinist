// Plotly writes its autorange result back into the layout object it is given, so a
// null end in `range` turns into a stale partial range on the next redraw and the
// set end is lost. A one-sided bound is expressed as an autorange limit instead.
export function frameAxisRange(
  left: number | null | undefined,
  right: number | null | undefined,
) {
  if (left != null && right != null) {
    return { range: [left, right] }
  }
  if (left != null) {
    return {
      autorange: true,
      autorangeoptions: { include: left, minallowed: left },
    }
  }
  if (right != null) {
    return {
      autorange: true,
      autorangeoptions: { include: right, maxallowed: right },
    }
  }
  return { autorange: true }
}

// Keyed by frame like y; Object.values would order "0".."n" before "-10".
// A null std (NaN on the backend) becomes undefined, which plotly draws as no bar.
export function errorBarArray(
  xValues: string[],
  std: Record<string, number | null | undefined> | undefined,
): (number | undefined)[] {
  return xValues.map((x) => std?.[x] ?? undefined)
}

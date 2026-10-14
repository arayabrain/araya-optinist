export const SHADE_COLORS = [
  "rgba(128, 128, 128, 0.25)",
  "rgba(31, 119, 180, 0.25)",
  "rgba(255, 127, 14, 0.25)",
  "rgba(44, 160, 44, 0.25)",
  "rgba(148, 103, 189, 0.25)",
]

export const SHADE_RECORD_CAP = 500
export const SHADE_TOTAL_CAP = 2000

export function eventSpans(
  x: number[],
  y: number[],
  threshold = 0.5,
  cap = SHADE_RECORD_CAP,
): { spans: [number, number][]; capped: boolean } {
  const spans: [number, number][] = []
  const n = x.length
  const endX = n > 1 ? 2 * x[n - 1] - x[n - 2] : x[0] + 1
  let start = -1
  for (let i = 0; i <= y.length; i++) {
    const on = i < y.length && y[i] > threshold
    if (on && start < 0) {
      start = i
    } else if (!on && start >= 0) {
      if (spans.length === cap) return { spans, capped: true }
      spans.push([x[start], i < n ? x[i] : endX])
      start = -1
    }
  }
  return { spans, capped: false }
}

export function buildShadeShapes(
  xrange: string[],
  data: { [key: string]: { [x: string]: number } },
  keys: string[],
  scale: number,
  colors: string[],
  isLoaded: (key: string) => boolean,
) {
  const x = xrange.map((v) => Number(v) / scale)
  const shapes: object[] = []
  let capped = false
  keys.forEach((key, position) => {
    const record = data[key]
    if (!record || !isLoaded(key)) return
    const cap = Math.min(SHADE_RECORD_CAP, SHADE_TOTAL_CAP - shapes.length)
    const result = eventSpans(
      x,
      xrange.map((v) => Number(record[v])),
      0.5,
      cap,
    )
    capped = capped || result.capped
    const fillcolor = colors[position % colors.length]
    result.spans.forEach(([x0, x1]) =>
      shapes.push({
        type: "rect",
        xref: "x",
        yref: "paper",
        y0: 0,
        y1: 1,
        x0,
        x1,
        layer: "below",
        line: { width: 0 },
        fillcolor,
      }),
    )
  })
  return { shapes, capped }
}

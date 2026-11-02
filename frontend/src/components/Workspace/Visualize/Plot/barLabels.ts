// The API serves each bar row as an array, so its keys are positions, not the saved columns.
export function barXLabels(
  row: object | undefined,
  columns: (number | string)[] | undefined,
): string[] {
  const keys = Object.keys(row ?? {})
  return columns?.length === keys.length ? columns.map(String) : keys
}

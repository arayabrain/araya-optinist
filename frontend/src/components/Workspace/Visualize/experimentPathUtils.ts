/**
 * Extract experiment UID from a visualize data file path.
 * Path format: "{workspaceId}/{uniqueId}/{functionNodeId}/filename"
 * (from experiment config outputPaths).
 */
export function getExperimentUidFromFilePath(
  filePath: string | null | undefined,
): string {
  if (!filePath) return ""
  const segments = filePath.split("/")
  return segments.length >= 2 ? segments[1] : ""
}

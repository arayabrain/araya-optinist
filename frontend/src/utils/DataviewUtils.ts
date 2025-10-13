/**
 * Checks whether the request is for public outputs from the Dataview screen.
 */
export const isDataviewPublicOutputsRequest = (url: string): boolean => {
  // Check if we're on the public dataview screen from path
  const path = window.location.pathname
  const isPublicDataviewPage =
    path === "/" || (path === "/dataview" && !path.includes("/console"))

  // Checks whether the request is to the outputs API
  const isOutputsApi = !!url && url.includes("/outputs/")

  return isPublicDataviewPage && isOutputsApi
}

export const DATAVIEW_PUBLIC_REQUEST_KEY = "DATAVIEW_PUBLIC_REQUEST"

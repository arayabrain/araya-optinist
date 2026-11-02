import { FC, useEffect } from "react"
import { useLocation } from "react-router-dom"

import routeHead from "const/routeHeadMetadata.json"

const ROUTES: Record<string, { title: string; description: string }> =
  routeHead.routes

const find = (tag: string, key: string, value: string) =>
  document.head.querySelector(`${tag}[${key}="${value}"]`)

// Keeps the origin the server wrote and swaps only the path.
const setPath = (element: Element | null, attr: string, path: string) => {
  const current = element?.getAttribute(attr)
  if (current) element?.setAttribute(attr, new URL(path, current).href)
}

// Mirrors rewrite_route_metadata(), which only runs on full page loads.
const applyRouteHeadMetadata = (pathname: string) => {
  const route = `/${pathname.replace(/^\/+|\/+$/g, "").toLowerCase()}`
  const meta = ROUTES[route]
  const title = meta?.title ?? routeHead.default.title
  const description = meta?.description ?? routeHead.default.description
  const social = meta?.description ?? routeHead.default.socialDescription

  document.title = title
  find("meta", "property", "og:title")?.setAttribute("content", title)
  find("meta", "name", "twitter:title")?.setAttribute("content", title)
  find("meta", "name", "description")?.setAttribute("content", description)
  find("meta", "property", "og:description")?.setAttribute("content", social)
  find("meta", "name", "twitter:description")?.setAttribute("content", social)
  setPath(find("link", "rel", "canonical"), "href", meta ? route : "/")
  setPath(find("meta", "property", "og:url"), "content", meta ? route : "/")
}

const RouteHeadMetadata: FC = () => {
  const { pathname } = useLocation()

  useEffect(() => applyRouteHeadMetadata(pathname), [pathname])

  return null
}

export default RouteHeadMetadata

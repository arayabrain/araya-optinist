import { FC } from "react"
import { MemoryRouter, useNavigate } from "react-router-dom"

import { beforeEach, describe, expect, it } from "@jest/globals"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import RouteHeadMetadata from "components/common/RouteHeadMetadata"
import routeHead from "const/routeHeadMetadata.json"

const ORIGIN = "https://www.araya-optinist.com"
const LOGIN = routeHead.routes["/login"]

const Go: FC<{ to: string }> = ({ to }) => {
  const navigate = useNavigate()
  return <button onClick={() => navigate(to)}>{to}</button>
}

const head = () => {
  const attr = (tag: string, key: string, value: string, name: string) =>
    document.head.querySelector(`${tag}[${key}="${value}"]`)?.getAttribute(name)
  return {
    title: document.title,
    ogTitle: attr("meta", "property", "og:title", "content"),
    twitterTitle: attr("meta", "name", "twitter:title", "content"),
    description: attr("meta", "name", "description", "content"),
    ogDescription: attr("meta", "property", "og:description", "content"),
    twitterDescription: attr("meta", "name", "twitter:description", "content"),
    canonical: attr("link", "rel", "canonical", "href"),
    ogUrl: attr("meta", "property", "og:url", "content"),
  }
}

describe("RouteHeadMetadata", () => {
  beforeEach(() => {
    // The shell as the server serves it for /login.
    document.head.innerHTML = `
      <title>${LOGIN.title}</title>
      <meta name="description" content="${LOGIN.description}" />
      <meta property="og:title" content="${LOGIN.title}" />
      <meta property="og:description" content="${LOGIN.description}" />
      <meta property="og:url" content="${ORIGIN}/login" />
      <meta name="twitter:title" content="${LOGIN.title}" />
      <meta name="twitter:description" content="${LOGIN.description}" />
      <link rel="canonical" href="${ORIGIN}/login" />`
    document.title = LOGIN.title
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <RouteHeadMetadata />
        <Go to="/register" />
        <Go to="/workspaces" />
      </MemoryRouter>,
    )
  })

  it("follows a client-side move to another listed route", async () => {
    await userEvent.click(screen.getByText("/register"))

    const { title, description } = routeHead.routes["/register"]
    expect(head()).toEqual({
      title,
      ogTitle: title,
      twitterTitle: title,
      description,
      ogDescription: description,
      twitterDescription: description,
      canonical: `${ORIGIN}/register`,
      ogUrl: `${ORIGIN}/register`,
    })
  })

  it("restores the site defaults on an unlisted route", async () => {
    await userEvent.click(screen.getByText("/workspaces"))

    const { title, description, socialDescription } = routeHead.default
    expect(head()).toEqual({
      title,
      ogTitle: title,
      twitterTitle: title,
      description,
      ogDescription: socialDescription,
      twitterDescription: socialDescription,
      canonical: `${ORIGIN}/`,
      ogUrl: `${ORIGIN}/`,
    })
  })
})

/* eslint-disable no-undef */
import "@testing-library/jest-dom"
import { Provider } from "react-redux"
import { MemoryRouter } from "react-router-dom"

import configureMockStore from "redux-mock-store"

import { describe, it } from "@jest/globals"
import { render, screen } from "@testing-library/react"

import Header from "components/Layout/Header"

jest.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: jest.fn() }),
}))

jest.mock("components/Layout/Profile", () => {
  const MockProfile = () => <div>profile</div>
  return MockProfile
})

const mockStore = configureMockStore([])

const renderHeader = () =>
  render(
    <Provider
      store={mockStore({
        mode: { mode: false, loading: false },
        pipeline: { run: { status: "" } },
        workspace: { currentWorkspace: { workspaceId: 1, selectedTab: 0 } },
      })}
    >
      <MemoryRouter>
        <Header handleDrawerOpen={jest.fn()} />
      </MemoryRouter>
    </Provider>,
  )

describe("authenticated header legal links", () => {
  it("links to the terms and privacy pages", () => {
    renderHeader()

    expect(screen.getByRole("link", { name: "Terms" })).toHaveAttribute(
      "href",
      "/terms",
    )
    expect(screen.getByRole("link", { name: "Privacy" })).toHaveAttribute(
      "href",
      "/privacy",
    )
  })
})

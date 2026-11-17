/* eslint-disable no-undef */
import { Provider } from "react-redux"
import { MemoryRouter } from "react-router-dom"

import configureMockStore from "redux-mock-store"

import { afterEach, describe, expect, it } from "@jest/globals"
import { render, screen, waitFor } from "@testing-library/react"

import Header from "components/Layout/Header"
import { BASE_URL } from "const/API"

jest.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: jest.fn() }),
}))

jest.mock("components/Layout/Profile", () => {
  const MockProfile = () => <div>profile</div>
  return MockProfile
})

const originalFetch = global.fetch

const renderHeader = (standalone: boolean) => {
  const fetchMock = jest.fn<Promise<unknown>, []>().mockResolvedValue({
    ok: true,
    json: async () => ({ id: "a1", message: "Maintenance tonight" }),
  })
  global.fetch = fetchMock as unknown as typeof fetch
  render(
    <Provider
      store={configureMockStore([])({
        mode: { mode: standalone, loading: false },
        pipeline: { run: { status: "" } },
        workspace: { currentWorkspace: { workspaceId: 1, selectedTab: 0 } },
      })}
    >
      <MemoryRouter>
        <Header handleDrawerOpen={jest.fn()} />
      </MemoryRouter>
    </Provider>,
  )
  return fetchMock
}

describe("announcement notice in the header", () => {
  afterEach(() => {
    global.fetch = originalFetch
  })

  it("shows a live announcement in the multi-user header", async () => {
    renderHeader(false)
    expect(
      await screen.findByRole("button", {
        name: "Announcement: Maintenance tonight",
      }),
    ).toBeTruthy()
  })

  it("neither fetches nor shows it in standalone", async () => {
    const fetchMock = renderHeader(true)
    await waitFor(() => expect(screen.getByAltText("logo")).toBeTruthy())
    expect(fetchMock).not.toHaveBeenCalledWith(
      `${BASE_URL}/announcement`,
      expect.anything(),
    )
    expect(screen.queryByRole("status")).toBeNull()
  })
})

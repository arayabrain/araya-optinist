/* eslint-disable no-undef */
import "@testing-library/jest-dom"
import { Provider } from "react-redux"

import configureMockStore from "redux-mock-store"

import { describe, it } from "@jest/globals"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"

import Tooltips from "components/Layout/Tooltips"

jest.mock("notistack", () => ({
  useSnackbar: () => ({
    enqueueSnackbar: jest.fn(),
  }),
}))

const mockStore = configureMockStore([])

const renderWithWorkspaceId = (workspaceId: number | undefined) => {
  const store = mockStore({
    workspace: { currentWorkspace: { workspaceId } },
  })
  return render(
    <Provider store={store}>
      <Tooltips />
    </Provider>,
  )
}

const openDocumentationMenu = () => {
  fireEvent.click(screen.getByTestId("MenuBookIcon").closest("button")!)
}

describe("Tooltips documentation menu", () => {
  it("disables 'Import sample data' before the workspace loads", () => {
    renderWithWorkspaceId(undefined)
    openDocumentationMenu()

    const item = screen.getByText("Import sample data").closest("li")
    expect(item).toHaveAttribute("aria-disabled", "true")
  })

  it("enables 'Import sample data' once the workspace is loaded", () => {
    renderWithWorkspaceId(1)
    openDocumentationMenu()

    const item = screen.getByText("Import sample data").closest("li")
    expect(item).not.toHaveAttribute("aria-disabled", "true")
  })

  it("closes the menu when opening the import dialog so its backdrop stops blocking clicks", async () => {
    renderWithWorkspaceId(1)
    openDocumentationMenu()

    fireEvent.click(screen.getByText("Import sample data"))

    expect(screen.getByText("Import sample data?")).toBeInTheDocument()
    await waitFor(() =>
      expect(
        screen.queryByText("Go to documentation page"),
      ).not.toBeInTheDocument(),
    )
  })
})

/* eslint-disable no-undef */
import "@testing-library/jest-dom"
import { ReactNode } from "react"
import { Provider } from "react-redux"

import configureMockStore from "redux-mock-store"
import thunk from "redux-thunk"

import { describe, it } from "@jest/globals"
import { render, screen } from "@testing-library/react"

import App from "App"

jest.mock("react-plotlyjs-ts", () => () => null)
jest.mock("api/modeStandalone", () => ({
  getModeStandaloneApi: () => new Promise(() => {}),
}))
jest.mock("components/Layout", () => {
  const MockLayout = ({ children }: { children: ReactNode }) => <>{children}</>
  return MockLayout
})
jest.mock("pages/LandingPage", () => {
  const MockLandingPage = () => <div>landing</div>
  return MockLandingPage
})
jest.mock("pages/Workspace/Workspace", () => {
  const MockWorkspace = () => <div>editor</div>
  return MockWorkspace
})

const renderAt = (path: string, standalone: boolean) => {
  window.history.pushState({}, "", path)
  render(
    <Provider
      store={configureMockStore([thunk])({
        mode: { mode: standalone, loading: false },
      })}
    >
      <App />
    </Provider>,
  )
}

describe("App routes", () => {
  it.each(["/", "/anything"])(
    "opens the editor at %s in standalone",
    (path) => {
      renderAt(path, true)

      expect(screen.getByText("editor")).toBeInTheDocument()
      expect(screen.queryByText("landing")).toBeNull()
    },
  )

  it("opens the landing page at / in multiuser", () => {
    renderAt("/", false)

    expect(screen.getByText("landing")).toBeInTheDocument()
  })
})

import { Provider } from "react-redux"

import { describe, expect, it } from "@jest/globals"
import { configureStore } from "@reduxjs/toolkit"
import { fireEvent, render } from "@testing-library/react"

import { DisplayDataContext } from "components/Workspace/Visualize/DataContext"
import { HTMLPlot } from "components/Workspace/Visualize/Plot/HTMLPlot"
import displayData from "store/slice/DisplayData/DisplayDataSlice"

const PATH = "/output/glm/textout.html"
const HTML = "<table><tr><td>coef</td></tr></table><img src=x onerror=alert(1)>"

const renderPlot = () => {
  const store = configureStore({
    reducer: { displayData },
    preloadedState: {
      displayData: {
        ...displayData(undefined, { type: "@@INIT" }),
        html: {
          [PATH]: {
            type: "html",
            data: HTML,
            pending: false,
            fulfilled: true,
            error: null,
          },
        },
      },
    },
  })
  return render(
    <Provider store={store}>
      <DisplayDataContext.Provider
        value={{ nodeId: "n", filePath: PATH, dataType: "html", itemId: 0 }}
      >
        <HTMLPlot />
      </DisplayDataContext.Provider>
    </Provider>,
  )
}

describe("HTMLPlot", () => {
  it("renders output HTML in an iframe that cannot run scripts", () => {
    const { container } = renderPlot()

    const frame = container.querySelector("iframe")!
    expect(frame.getAttribute("sandbox")).toBe("allow-same-origin")
    expect(frame.getAttribute("srcdoc")).toBe(HTML)
  })

  it("grows the iframe to its content height", () => {
    const { container } = renderPlot()
    const frame = container.querySelector("iframe")!
    Object.defineProperty(
      frame.contentDocument!.documentElement,
      "scrollHeight",
      { value: 640 },
    )

    fireEvent.load(frame)

    expect(frame.style.height).toBe("640px")
  })
})

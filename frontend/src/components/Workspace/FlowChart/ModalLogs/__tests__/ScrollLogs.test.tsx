import { createRef } from "react"

import { describe, expect, it } from "@jest/globals"
import { render, screen } from "@testing-library/react"

import ScrollLogs from "components/Workspace/FlowChart/ModalLogs/ScrollLogs"

const LINE = "<img src=x onerror=alert(1)> loaded"

describe("ScrollLogs", () => {
  it("renders a matched log line as text, not HTML", () => {
    const { container } = render(
      <ScrollLogs
        ref={createRef<HTMLDivElement>()}
        logs={[{ text: LINE, id: "1" }]}
        keyword="loaded"
      />,
    )

    expect(container.querySelector("img")).toBeNull()
    expect(screen.getByText("loaded").tagName).toBe("SPAN")
    expect(container.textContent).toBe(LINE)
  })
})

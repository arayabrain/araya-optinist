import { describe, expect, it, jest } from "@jest/globals"
import { fireEvent, render, screen } from "@testing-library/react"

import { DraftNumberField } from "components/Workspace/Visualize/Editor/HeatmapItemEditor"

describe("DraftNumberField", () => {
  it("keeps what is typed and commits each finite value", () => {
    const onCommit = jest.fn()
    const { rerender } = render(
      <DraftNumberField label="Min" value={1} onCommit={onCommit} />,
    )
    const input = screen.getByRole("spinbutton") as HTMLInputElement

    fireEvent.change(input, { target: { value: "" } }) // cleared, or a lone minus
    expect(onCommit).not.toHaveBeenCalled()
    expect(input.value).toBe("")

    fireEvent.change(input, { target: { value: "-2" } })
    expect(onCommit).toHaveBeenLastCalledWith(-2)
    rerender(<DraftNumberField label="Min" value={-2} onCommit={onCommit} />)

    fireEvent.change(input, { target: { value: "-2.50" } })
    expect(onCommit).toHaveBeenLastCalledWith(-2.5)
    rerender(<DraftNumberField label="Min" value={-2.5} onCommit={onCommit} />)
    expect(input.value).toBe("-2.50")

    rerender(<DraftNumberField label="Min" value={7} onCommit={onCommit} />)
    expect(input.value).toBe("7")
  })

  it("shows but does not commit a value the field rejects", () => {
    const onCommit = jest.fn()
    render(
      <DraftNumberField
        label="Clip"
        value={2}
        accepts={(value) => value < 50}
        onCommit={onCommit}
      />,
    )
    const input = screen.getByRole("spinbutton") as HTMLInputElement
    fireEvent.change(input, { target: { value: "60" } })
    expect(onCommit).not.toHaveBeenCalled()
    expect(input.value).toBe("60")
  })
})

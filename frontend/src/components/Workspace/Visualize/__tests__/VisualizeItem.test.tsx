/* eslint-disable no-undef */
import { Provider } from "react-redux"

import { describe, expect, it, jest } from "@jest/globals"
import { render, screen } from "@testing-library/react"

import { VisualizeProvider } from "components/Workspace/Visualize/VisualizeContext"
import { VisualizeItem } from "components/Workspace/Visualize/VisualizeItem"
import { pushInitialItemToNewRow } from "store/slice/VisualizeItem/VisualizeItemSlice"
import { store } from "store/store"

// plotly's ESM build is untransformable in jest, and the header is the subject
jest.mock("components/Workspace/Visualize/DisplayDataItem", () => ({
  DisplayDataItem: () => null,
}))

describe("VisualizeItem", () => {
  it("lets the header wrap so a narrow item keeps its number chip in view", () => {
    store.dispatch(pushInitialItemToNewRow())
    const itemId = store.getState().visualaizeItem.layout[0][0]
    render(
      <Provider store={store}>
        <VisualizeProvider>
          <VisualizeItem itemId={itemId} />
        </VisualizeProvider>
      </Provider>,
    )

    // chip label -> Chip root -> the flex-grow box -> the header
    const chip = screen.getByText(String(itemId)).parentElement
    const header = chip?.parentElement?.parentElement
    // jsdom has no layout, so this pins the flex rule, not the rendered pixels
    expect(getComputedStyle(header as Element).flexWrap).toBe("wrap")
  })
})

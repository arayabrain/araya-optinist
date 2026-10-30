import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  jest,
} from "@jest/globals"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import AnnouncementNotice, {
  ANNOUNCEMENT_DISMISSED_KEY,
  ANNOUNCEMENT_POLL_INTERVAL_MS,
} from "components/Layout/AnnouncementNotice"
import { BASE_URL } from "const/API"
import { tabSync, TabSyncMessage } from "utils/crossTabSync"

const originalFetch = global.fetch
const MESSAGE = "Maintenance Wed 13:00-14:00 JST"

const mockFetchSequence = (...bodies: Array<object | null>) => {
  const fn = jest.fn<Promise<unknown>, []>()
  bodies.forEach((body) =>
    fn.mockResolvedValueOnce(
      body === null
        ? { ok: false, json: async () => ({}) }
        : { ok: true, json: async () => body },
    ),
  )
  fn.mockResolvedValue({ ok: true, json: async () => bodies.at(-1) ?? {} })
  global.fetch = fn as unknown as typeof fetch
  return fn
}

describe("AnnouncementNotice", () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    global.fetch = originalFetch
    jest.useRealTimers()
    jest.restoreAllMocks()
  })

  it("fetches from the backend without auth headers", async () => {
    const fetchMock = mockFetchSequence({})
    render(<AnnouncementNotice />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(fetchMock).toHaveBeenCalledWith(`${BASE_URL}/announcement`, {
      cache: "no-store",
    })
  })

  it("shows a live announcement as plain text", async () => {
    mockFetchSequence({ id: "a1", message: "<b>Down</b>" })
    render(<AnnouncementNotice />)
    expect(await screen.findByText("<b>Down</b>")).toBeTruthy()
    expect(screen.getByRole("status")).toBeTruthy()
  })

  it("shows nothing when there is no announcement", async () => {
    const fetchMock = mockFetchSequence({})
    render(<AnnouncementNotice />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("stays hidden once that announcement was dismissed", async () => {
    localStorage.setItem(ANNOUNCEMENT_DISMISSED_KEY, "a1")
    const fetchMock = mockFetchSequence({ id: "a1", message: MESSAGE })
    render(<AnnouncementNotice />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByText(MESSAGE)).toBeNull()
  })

  it("shows a changed announcement despite an older dismissal", async () => {
    localStorage.setItem(ANNOUNCEMENT_DISMISSED_KEY, "old")
    mockFetchSequence({ id: "new", message: "Rescheduled to Thu" })
    render(<AnnouncementNotice />)
    expect(await screen.findByText("Rescheduled to Thu")).toBeTruthy()
  })

  it("shows the first line in the header and the full text in a window", async () => {
    mockFetchSequence({ id: "a1", message: "Down Wed\n\nDetails here" })
    render(<AnnouncementNotice />)
    const button = await screen.findByRole("button", {
      name: "Announcement: Down Wed",
    })
    expect(screen.queryByText(/Details here/)).toBeNull()

    await userEvent.click(button)
    expect(await screen.findByRole("dialog")).toBeTruthy()
    expect(screen.getByText(/Details here/)).toBeTruthy()

    await userEvent.click(screen.getByRole("button", { name: "Close" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(localStorage.getItem(ANNOUNCEMENT_DISMISSED_KEY)).toBeNull()
    expect(screen.getByRole("button", { name: /Announcement/ })).toBeTruthy()
  })

  it("hiding from the window stores the id, broadcasts it, and hides the notice", async () => {
    const broadcast = jest.spyOn(tabSync, "broadcastAlertDismissed")
    mockFetchSequence({ id: "a1", message: MESSAGE })
    render(<AnnouncementNotice />)
    await userEvent.click(
      await screen.findByRole("button", { name: `Announcement: ${MESSAGE}` }),
    )
    await userEvent.click(
      await screen.findByRole("button", { name: "Hide until next sign-in" }),
    )
    expect(localStorage.getItem(ANNOUNCEMENT_DISMISSED_KEY)).toBe("a1")
    expect(broadcast).toHaveBeenCalledWith("announcement:a1")
    await waitFor(() => expect(screen.queryByText(MESSAGE)).toBeNull())
  })

  it("hides when another tab dismisses it", async () => {
    let handler: ((message: TabSyncMessage) => void) | undefined
    jest.spyOn(tabSync, "on").mockImplementation((_type, fn) => {
      handler = fn
      return () => {}
    })
    mockFetchSequence({ id: "a1", message: MESSAGE })
    render(<AnnouncementNotice />)
    expect(await screen.findByText(MESSAGE)).toBeTruthy()

    act(() =>
      handler?.({
        type: "ALERT_DISMISSED",
        payload: { alertId: "limitAlert" },
      }),
    )
    expect(screen.getByText(MESSAGE)).toBeTruthy()

    act(() =>
      handler?.({
        type: "ALERT_DISMISSED",
        payload: { alertId: "announcement:a1" },
      }),
    )
    expect(screen.queryByText(MESSAGE)).toBeNull()
  })

  it("keeps the notice when a poll fails and removes it once cleared", async () => {
    jest.useFakeTimers()
    mockFetchSequence({ id: "a1", message: MESSAGE }, null, {})
    render(<AnnouncementNotice />)
    expect(await screen.findByText(MESSAGE)).toBeTruthy()

    await act(async () => {
      jest.advanceTimersByTime(ANNOUNCEMENT_POLL_INTERVAL_MS)
    })
    expect(screen.getByText(MESSAGE)).toBeTruthy()

    await act(async () => {
      jest.advanceTimersByTime(ANNOUNCEMENT_POLL_INTERVAL_MS)
    })
    await waitFor(() => expect(screen.queryByText(MESSAGE)).toBeNull())
  })
})

"""Whether `/health` is answered while another request's blocking work runs.

The app is served in-process, on the test's event loop. The held work waits for
`/health`'s answer, 2 s at most: run on the event loop it holds `/health` too,
so the wait times out; run in a thread it is released by the answer.
"""

import asyncio
import threading

import httpx

from studio.__main_unit__ import app


class Hold:
    """Blocking work that waits until /health is answered."""

    def __init__(self):
        self.started, self.answered = threading.Event(), threading.Event()
        # True when the answer, not the timeout, released the work
        self.released_by_answer = []

    def __call__(self, *args, **kwargs):
        self.started.set()
        self.released_by_answer.append(self.answered.wait(2))


async def wait_until(event: threading.Event, timeout: float = 5.0):
    for _ in range(int(timeout / 0.01)):
        if event.is_set():
            return
        await asyncio.sleep(0.01)


async def health_while_held(hold: Hold, method: str, url: str, **kwargs):
    """The held request's response and /health's, asked while the work is held."""
    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        held = asyncio.create_task(http.request(method, url, **kwargs))
        await wait_until(hold.started)
        health = await http.get("/health")
        hold.answered.set()
        return await held, health

"""POST /run/filter rewrites the node's outputs off the event loop, one filter
at a time per process."""

import asyncio

import httpx
import pytest

from studio.__main_unit__ import app
from studio.app.common.routers import run
from studio.tests.app.common.loop_probe import (
    REQUEST_CLIENT_ID,
    Hold,
    health_while_held,
    wait_until,
)


def fake_filter(on_filter):
    """WorkflowNodeDataFilter with on_filter(node_id) for the filter's work."""

    class FakeFilter:
        def __init__(self, workspace_id, unique_id, node_id):
            self.node_id = node_id

        def filter_node_data(self, params):
            on_filter(self.node_id)

    return FakeFilter


@pytest.fixture(autouse=True)
def no_usage_update(monkeypatch):
    monkeypatch.setattr(
        run.WorkspaceDataCapacityService,
        "update_experiment_data_usage",
        lambda *args: None,
    )


@pytest.mark.asyncio
async def test_health_is_answered_while_a_data_filter_runs(monkeypatch):
    hold = Hold()
    monkeypatch.setattr(run, "WorkflowNodeDataFilter", fake_filter(hold))

    held, health = await health_while_held(hold, "POST", "/run/filter/1/uid/node")

    assert health.status_code == 200
    assert held.status_code == 200, held.text
    assert hold.released_by_answer == [True]
    # the filter thread logs with the request's client id
    assert hold.client_ids == [REQUEST_CLIENT_ID]


@pytest.mark.asyncio
async def test_a_second_data_filter_waits_for_the_first(monkeypatch):
    hold, filtered = Hold(), []

    def on_filter(node_id):
        filtered.append(node_id)
        if node_id == "first":
            hold()

    monkeypatch.setattr(run, "WorkflowNodeDataFilter", fake_filter(on_filter))

    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        first = asyncio.create_task(http.post("/run/filter/1/uid/first"))
        await wait_until(hold.started)
        second = asyncio.create_task(http.post("/run/filter/1/uid/second"))
        # time for the second filter to start, were filters run side by side
        await asyncio.sleep(0.2)
        filtered_while_first_runs = list(filtered)
        hold.answered.set()
        responses = await asyncio.gather(first, second)

    assert hold.released_by_answer == [True]
    assert filtered_while_first_runs == ["first"]
    assert [r.status_code for r in responses] == [200, 200]

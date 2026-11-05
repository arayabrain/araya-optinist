"""POST /run/filter rewrites the node's outputs off the event loop."""

import pytest

from studio.app.common.routers import run
from studio.tests.app.common.loop_probe import Hold, health_while_held


@pytest.mark.asyncio
async def test_health_is_answered_while_a_data_filter_runs(monkeypatch):
    hold = Hold()

    class FakeFilter:
        def __init__(self, workspace_id, unique_id, node_id):
            pass

        def filter_node_data(self, params):
            hold()

    monkeypatch.setattr(run, "WorkflowNodeDataFilter", FakeFilter)
    monkeypatch.setattr(
        run.WorkspaceDataCapacityService,
        "update_experiment_data_usage",
        lambda *args: None,
    )

    held, health = await health_while_held(hold, "POST", "/run/filter/1/uid/node")

    assert health.status_code == 200
    assert held.status_code == 200, held.text
    assert hold.released_by_answer == [True]

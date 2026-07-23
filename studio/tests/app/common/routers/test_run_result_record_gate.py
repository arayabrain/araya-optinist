"""Behavior tests for the run_result record-existence gate (issue #740 item #1).

The executor writes the experiment_records row asynchronously after a run
finishes, so a poll can observe all nodes finished before the row lands. The
poll must keep reporting "processing" until the record exists, so the dataview
is never opened against a missing record.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from studio.app.common.core.workflow.workflow import NodeItem
from studio.app.common.routers.run import run_result
from studio.app.common.schemas.workflow import CompleteStatus


def _poll(record_present):
    """Drive run_result with all nodes finished; record_exists is toggled."""
    with patch(
        "studio.app.common.routers.run.ExptConfigReader.ensure_synced_async",
        new=AsyncMock(),
    ), patch("studio.app.common.routers.run.WorkflowResult") as mock_wfr, patch(
        "studio.app.common.routers.run.RemoteStorageController.is_available",
        return_value=False,
    ), patch(
        "studio.app.common.routers.run.ExperimentRecordService.is_available",
        return_value=True,
    ), patch(
        "studio.app.common.routers.run.NodeResult.is_all_nodes_already_finished",
        return_value=True,
    ), patch(
        "studio.app.common.routers.run.ExptConfigReader.read",
        return_value=Mock(),
    ), patch(
        "studio.app.common.routers.run.ExperimentRecordService.record_exists",
        return_value=record_present,
    ):
        mock_wfr.return_value.observe = AsyncMock(return_value={})
        return asyncio.run(
            run_result(
                workspace_id="1",
                uid="exp123",
                nodeDict=NodeItem(pendingNodeIdList=[]),
                background_tasks=Mock(),
                remote_bucket_name="",
            )
        )


def test_run_result_holds_processing_when_record_absent():
    """All nodes finished but the record has not landed yet -> keep polling."""
    resp = _poll(record_present=False)
    assert resp.completeStatus == CompleteStatus.PROCESSING.value


def test_run_result_completes_when_record_present():
    """Record present -> completion is not forced back to processing."""
    resp = _poll(record_present=True)
    assert resp.completeStatus is None

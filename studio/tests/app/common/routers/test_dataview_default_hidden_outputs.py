"""
Default visibility of Dataview outputs: intermediate processing steps are
hidden once a later processing step builds on them; everything else is public.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import mysql

from studio.app.common.core.dataview.dataview_services import DataviewService
from studio.app.common.core.experiment.experiment_record_services import (
    ExperimentRecordService,
)
from studio.app.common.routers.dataview import (
    PublishFlags,
    get_hidden_outputs,
    multiple_publish_dataview_records,
    publish_dataview_records,
    put_hidden_outputs,
)
from studio.app.common.schemas.dataview import HiddenOutputChange, LocalSyncStatus

SERVICES = "studio.app.common.core.dataview.dataview_services."
ROUTER = "studio.app.common.routers.dataview."


def _workflow(nodes, edges):
    """nodes: {node_id: algorithm path or None for an input}; edges: [(src, dst)]"""
    workflow = SimpleNamespace(
        nodeDict={
            node_id: SimpleNamespace(
                type="AlgorithmNode" if path else "ImageFileNode",
                data=SimpleNamespace(path=path or ["movie.tif"]),
            )
            for node_id, path in nodes.items()
        },
        edgeDict={
            f"e{i}": SimpleNamespace(source=s, target=t)
            for i, (s, t) in enumerate(edges)
        },
    )
    expt = SimpleNamespace(
        function={
            node_id: SimpleNamespace(
                outputPaths={"out": SimpleNamespace(path=f"1/uid/{node_id}/out.json")}
                if path
                else {}
            )
            for node_id, path in nodes.items()
        }
    )
    return workflow, expt


def _hidden_nodes(nodes, edges):
    workflow, expt = _workflow(nodes, edges)
    with patch(SERVICES + "WorkflowConfigReader.read", return_value=workflow), patch(
        SERVICES + "ExptConfigReader.read", return_value=expt
    ):
        hidden = DataviewService.default_hidden_outputs("1", "uid")
    return sorted(path.split("/")[2] for path in hidden)


CONVERT = "suite2p/suite2p_file_convert"
REGISTRATION = "suite2p/suite2p_registration"
ROI = "suite2p/suite2p_roi"
SPIKES = "suite2p/suite2p_spike_deconv"
MC = "caiman/caiman_mc"
CNMF = "caiman/caiman_cnmf"
LCCD = "lccd/lccd_cell_detection"
CORRELATION = "optinist/neural_population_analysis/correlation"
SLICE = "utils/data_slice"


class TestDefaultHiddenOutputs:
    def test_suite2p_shows_the_rois_and_the_analysis(self):
        nodes = {"in": None, "conv": CONVERT, "reg": REGISTRATION, "roi": ROI}
        nodes["corr"] = CORRELATION
        edges = [("in", "conv"), ("conv", "reg"), ("reg", "roi"), ("roi", "corr")]
        assert _hidden_nodes(nodes, edges) == ["conv", "reg"]

    def test_adding_spike_deconvolution_keeps_the_rois_public(self):
        nodes = {"in": None, "conv": CONVERT, "reg": REGISTRATION, "roi": ROI}
        nodes["spks"] = SPIKES
        edges = [("in", "conv"), ("conv", "reg"), ("reg", "roi"), ("roi", "spks")]
        assert _hidden_nodes(nodes, edges) == ["conv", "reg"]

    def test_every_chained_motion_correction_is_hidden(self):
        nodes = {"in": None, "mc1": MC, "mc2": MC, "cnmf": CNMF}
        edges = [("in", "mc1"), ("mc1", "mc2"), ("mc2", "cnmf")]
        assert _hidden_nodes(nodes, edges) == ["mc1", "mc2"]

    def test_suite2p_registration_feeding_lccd_is_hidden(self):
        nodes = {"in": None, "conv": CONVERT, "reg": REGISTRATION, "lccd": LCCD}
        edges = [("in", "conv"), ("conv", "reg"), ("reg", "lccd")]
        assert _hidden_nodes(nodes, edges) == ["conv", "reg"]

    def test_a_step_reaching_detection_through_a_utility_is_hidden(self):
        nodes = {"in": None, "mc": MC, "slice": SLICE, "roi": ROI}
        edges = [("in", "mc"), ("mc", "slice"), ("slice", "roi")]
        assert _hidden_nodes(nodes, edges) == ["mc"]

    def test_motion_correction_that_ends_the_pipeline_stays_public(self):
        nodes = {"in": None, "mc": MC, "corr": CORRELATION}
        edges = [("in", "mc"), ("mc", "corr")]
        assert _hidden_nodes(nodes, edges) == []

    def test_one_branch_reaching_detection_hides_the_step(self):
        nodes = {"in": None, "mc": MC, "cnmf": CNMF, "corr": CORRELATION}
        edges = [("in", "mc"), ("mc", "cnmf"), ("mc", "corr")]
        assert _hidden_nodes(nodes, edges) == ["mc"]

    def test_analysis_only_workflow_hides_nothing(self):
        nodes = {"in": None, "slice": SLICE, "corr": CORRELATION}
        edges = [("in", "slice"), ("slice", "corr")]
        assert _hidden_nodes(nodes, edges) == []


def _record(hidden_outputs, publish_status=0):
    return SimpleNamespace(
        id=7,
        version=3,
        workspace_id=1,
        uid="uid",
        hidden_outputs=hidden_outputs,
        publish_status=publish_status,
    )


class TestEffectiveHiddenOutputs:
    def test_a_stored_list_wins_even_when_empty(self):
        with patch.object(DataviewService, "default_hidden_outputs") as default:
            assert DataviewService.effective_hidden_outputs(_record([])) == []
        default.assert_not_called()

    def test_unset_and_unpublished_gets_the_defaults(self):
        with patch.object(
            DataviewService, "default_hidden_outputs", return_value=["1/uid/a"]
        ):
            assert DataviewService.effective_hidden_outputs(_record(None)) == [
                "1/uid/a"
            ]

    def test_unset_on_a_published_record_stays_all_public(self):
        with patch.object(DataviewService, "default_hidden_outputs") as default:
            assert DataviewService.effective_hidden_outputs(_record(None, 1)) == []
        default.assert_not_called()

    def test_unreadable_workflow_falls_back_to_all_public(self):
        with patch.object(
            DataviewService, "default_hidden_outputs", side_effect=AssertionError
        ):
            assert DataviewService.effective_hidden_outputs(_record(None)) == []


class TestRoutes:
    @pytest.mark.asyncio
    async def test_get_returns_the_defaults_without_writing(self):
        db = MagicMock()
        with patch(
            ROUTER + "DataviewService.find_user_owned_dataview_record",
            return_value=_record(None),
        ), patch.object(
            DataviewService, "default_hidden_outputs", return_value=["1/uid/a"]
        ):
            result = await get_hidden_outputs(7, db=db, current_user=MagicMock(id=1))
        assert result == ["1/uid/a"]
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_not_owned_is_404(self):
        with patch(
            ROUTER + "DataviewService.find_user_owned_dataview_record",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc:
                await get_hidden_outputs(7, db=MagicMock(), current_user=MagicMock())
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_first_toggle_starts_from_the_defaults(self):
        db = MagicMock()
        db.execute.return_value.rowcount = 1
        with patch(
            ROUTER + "DataviewService.find_user_owned_dataview_record",
            return_value=_record(None),
        ), patch.object(
            DataviewService, "default_hidden_outputs", return_value=["1/uid/a"]
        ):
            result = await put_hidden_outputs(
                7,
                HiddenOutputChange(path="1/uid/b", hidden=True),
                db=db,
                current_user=MagicMock(id=1),
            )
        assert result == ["1/uid/a", "1/uid/b"]

    @staticmethod
    async def _publish(record):
        db = MagicMock()
        db.execute.return_value.rowcount = 1
        with patch(
            ROUTER + "DataviewService.find_user_owned_dataview_record",
            return_value=record,
        ), patch(
            ROUTER + "_validate_experiment_exists_in_s3", return_value=(True, None)
        ), patch(
            ROUTER + "PublishValidator.validate",
            return_value=MagicMock(can_publish=True),
        ), patch.object(
            DataviewService, "default_hidden_outputs", return_value=["1/uid/a"]
        ):
            await publish_dataview_records(
                id=7,
                flag=PublishFlags.on,
                db=db,
                current_user=MagicMock(remote_bucket_name="b"),
            )
        return db.execute.call_args[0][0].compile(dialect=mysql.dialect()).params

    @pytest.mark.asyncio
    async def test_publishing_stores_the_defaults(self):
        record = _record(None)
        record.local_sync_status = LocalSyncStatus.synced.value
        params = await self._publish(record)
        assert params["hidden_outputs"] == ["1/uid/a"]

    @pytest.mark.asyncio
    async def test_publishing_keeps_the_owners_choice(self):
        record = _record([])
        record.local_sync_status = LocalSyncStatus.synced.value
        params = await self._publish(record)
        assert "hidden_outputs" not in params

    @pytest.mark.asyncio
    async def test_bulk_publish_stores_the_defaults_only_where_unset(self):
        db = MagicMock()
        records = {1: _record(None), 2: _record(["1/uid/kept"])}
        with patch(
            ROUTER + "DataviewService.find_user_owned_dataview_record",
            side_effect=lambda _db, record_id, _user: records[record_id],
        ), patch(
            ROUTER + "RemoteStorageController.is_available", return_value=False
        ), patch(
            ROUTER + "PublishValidator.validate",
            return_value=MagicMock(can_publish=True),
        ), patch(
            ROUTER + "DataviewService.multiple_publish_dataview_records"
        ), patch.object(
            DataviewService, "default_hidden_outputs", return_value=["1/uid/a"]
        ):
            await multiple_publish_dataview_records(
                [1, 2],
                PublishFlags.on,
                db=db,
                current_user=MagicMock(remote_bucket_name="b"),
            )
        writes = [
            c.args[0].compile(dialect=mysql.dialect())
            for c in db.execute.call_args_list
        ]
        assert len(writes) == 1
        assert "hidden_outputs IS NULL" in str(writes[0])
        assert writes[0].params["hidden_outputs"] == ["1/uid/a"]


def test_a_copy_of_an_unset_record_stays_unset():
    source = MagicMock(hidden_outputs=None)
    db = MagicMock()
    db.query.return_value.filter.return_value.one.return_value = source

    ExperimentRecordService.copy_record(db, "1", "uid", "uid2", "copy")

    assert db.add.call_args.args[0].hidden_outputs is None

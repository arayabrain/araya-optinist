"""
Tests for per-output hiding on published Dataview records.

Two gates must hold: the public reproduce payload omits hidden outputs, and a
public visualization read of a hidden path is denied (403).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import mysql

from studio.app.common.core.auth.auth_dependencies import (
    get_current_user_with_dataview_outputs_check,
)
from studio.app.common.core.dataview.dataview_services import DataviewService
from studio.app.common.core.experiment.experiment_record_services import (
    ExperimentRecordService,
)
from studio.app.common.routers.dataview import (
    public_reproduce_experiment,
    put_hidden_outputs,
)
from studio.app.common.schemas.dataview import HiddenOutputChange, LocalSyncStatus
from studio.app.common.schemas.workflow import WorkflowWithResults
from studio.app.dir_path import DIRPATH

HIDDEN = "1/uid/func1/heatmap"
VISIBLE = "1/uid/func1/fluorescence"
ROUTER = "studio.app.common.routers.dataview."


def _record(hidden_outputs):
    record = MagicMock()
    record.id = 7
    record.version = 3
    record.workspace_id = 1
    record.uid = "uid"
    record.hidden_outputs = hidden_outputs
    record.local_sync_status = LocalSyncStatus.synced.value
    return record


def _public_request(path):
    req = MagicMock()
    req.headers = {DataviewService.DATAVIEW_PUBLIC_REQUEST_KEY.lower(): "true"}
    req.url.path = path
    req.query_params = {}
    return req


def _reproduce_result(*paths):
    return WorkflowWithResults(
        workspace_id="1",
        unique_id="uid",
        name="exp",
        started_at="",
        finished_at=None,
        success="success",
        hasNWB=False,
        function={
            "func1": {
                "unique_id": "func1",
                "name": "f",
                "success": "success",
                "hasNWB": False,
                "outputPaths": {
                    f"out{i}": {"path": p, "type": "timeSeries", "max_index": 1}
                    for i, p in enumerate(paths)
                },
            }
        },
        nodeDict={},
        edgeDict={},
    )


class TestPublicReproduceGate:
    @pytest.mark.asyncio
    async def test_hidden_outputs_are_stripped_from_the_public_payload(self):
        # A legacy absolute path must be matched after normalization too.
        absolute_hidden = f"{DIRPATH.OUTPUT_DIR}/{HIDDEN}"
        with patch(
            ROUTER + "DataviewService.find_dataview_record",
            return_value=_record([HIDDEN]),
        ), patch(
            ROUTER + "RemoteSyncStatusFileUtil.check_sync_status_unsynced",
            return_value=False,
        ), patch(
            ROUTER + "RemoteStorageController.is_available", return_value=False
        ), patch(
            ROUTER + "PublishValidator.validate_for_display",
            return_value=MagicMock(is_displayable=True),
        ), patch(
            ROUTER + "reproduce_experiment",
            new=AsyncMock(return_value=_reproduce_result(absolute_hidden, VISIBLE)),
        ):
            result = await public_reproduce_experiment("1", "uid", db=MagicMock())

        paths = [v["path"] for v in result.function["func1"]["outputPaths"].values()]
        assert paths == [VISIBLE]

    @pytest.mark.asyncio
    async def test_nothing_hidden_leaves_the_payload_untouched(self):
        with patch(
            ROUTER + "DataviewService.find_dataview_record",
            return_value=_record(None),
        ), patch(
            ROUTER + "RemoteSyncStatusFileUtil.check_sync_status_unsynced",
            return_value=False,
        ), patch(
            ROUTER + "RemoteStorageController.is_available", return_value=False
        ), patch(
            ROUTER + "PublishValidator.validate_for_display",
            return_value=MagicMock(is_displayable=True),
        ), patch(
            ROUTER + "reproduce_experiment",
            new=AsyncMock(return_value=_reproduce_result(HIDDEN, VISIBLE)),
        ):
            result = await public_reproduce_experiment("1", "uid", db=MagicMock())

        assert len(result.function["func1"]["outputPaths"]) == 2


class TestPublicOutputsReadGate:
    _FIND = (
        "studio.app.common.core.dataview.dataview_services."
        "DataviewService.find_dataview_record"
    )

    @pytest.mark.asyncio
    async def test_public_read_of_hidden_path_is_403(self):
        mock_db = MagicMock()
        with patch(self._FIND, return_value=_record([HIDDEN])):
            with pytest.raises(HTTPException) as exc:
                await get_current_user_with_dataview_outputs_check(
                    req=_public_request(f"/api/visualizations/inittimedata/{HIDDEN}"),
                    res=MagicMock(),
                    ex_token=None,
                    credential=None,
                    db=mock_db,
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_public_read_of_visible_sibling_is_allowed(self):
        with patch(self._FIND, return_value=_record([HIDDEN])):
            result = await get_current_user_with_dataview_outputs_check(
                req=_public_request(f"/api/visualizations/inittimedata/{VISIBLE}"),
                res=MagicMock(),
                ex_token=None,
                credential=None,
                db=MagicMock(),
            )
        assert result is None

    def test_file_under_a_hidden_directory_is_hidden(self):
        record = _record([HIDDEN])
        assert DataviewService.is_hidden_output(record, HIDDEN + "/data.json")
        assert DataviewService.is_hidden_output(
            record, f"{DIRPATH.OUTPUT_DIR}/{HIDDEN}"
        )
        assert not DataviewService.is_hidden_output(record, HIDDEN + "_other")

    @pytest.mark.parametrize(
        "path",
        [
            "1/uid/func1//heatmap/data.json",
            "1/uid/func1/./heatmap/data.json",
            "1//uid/func1/heatmap",
            "1/uid/func1/heatmap/",
            "1/uid/other/../func1/heatmap/data.json",
            "/1/uid/func1/heatmap",
        ],
    )
    def test_non_canonical_spellings_are_still_hidden(self, path):
        assert DataviewService.is_hidden_output(_record([HIDDEN]), path)

    @pytest.mark.asyncio
    async def test_public_read_with_doubled_slash_is_403(self):
        with patch(self._FIND, return_value=_record([HIDDEN])):
            with pytest.raises(HTTPException) as exc:
                await get_current_user_with_dataview_outputs_check(
                    req=_public_request(
                        "/api/visualizations/inittimedata/1/uid/func1//heatmap"
                    ),
                    res=MagicMock(),
                    ex_token=None,
                    credential=None,
                    db=MagicMock(),
                )
        assert exc.value.status_code == 403

    def test_empty_path_is_not_hidden(self):
        assert not DataviewService.is_hidden_output(_record([HIDDEN]), None)

    def test_thumbnail_path_is_not_hidden(self):
        record = _record([HIDDEN])
        assert not DataviewService.is_hidden_output(record, "1/uid/roi")


class TestPutHiddenOutputs:
    @staticmethod
    async def _put(change, stored=(), rowcount=1):
        mock_db = MagicMock()
        mock_db.execute.return_value.rowcount = rowcount
        with patch(
            ROUTER + "DataviewService.find_user_owned_dataview_record",
            return_value=_record(list(stored)),
        ):
            result = await put_hidden_outputs(
                7, change, db=mock_db, current_user=MagicMock(id=1)
            )
        return result, mock_db

    @pytest.mark.asyncio
    async def test_hide_adds_the_canonical_path_and_guards_version(self):
        result, mock_db = await self._put(
            HiddenOutputChange(
                path=f"{DIRPATH.OUTPUT_DIR}/1/uid/func1//heatmap", hidden=True
            ),
            stored=[VISIBLE],
        )

        assert result == sorted([HIDDEN, VISIBLE])
        compiled = mock_db.execute.call_args[0][0].compile(dialect=mysql.dialect())
        assert compiled.params["hidden_outputs"] == sorted([HIDDEN, VISIBLE])
        versions = {k: v for k, v in compiled.params.items() if k.startswith("version")}
        assert sorted(versions.values()) == [1, 3]
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_show_removes_only_that_path(self):
        # The stored list is the base, so other hidden outputs stay hidden.
        result, _ = await self._put(
            HiddenOutputChange(path=HIDDEN, hidden=False), stored=[HIDDEN, VISIBLE]
        )
        assert result == [VISIBLE]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path", ["1/other/func1/heatmap", "1/uid/../other/x", "/etc/passwd", "1/uid"]
    )
    async def test_path_outside_the_record_is_422(self, path):
        with pytest.raises(HTTPException) as exc:
            await self._put(HiddenOutputChange(path=path, hidden=True))
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_version_conflict_is_409(self):
        with pytest.raises(HTTPException) as exc:
            await self._put(HiddenOutputChange(path=HIDDEN, hidden=True), rowcount=0)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_not_owned_is_404(self):
        with patch(
            ROUTER + "DataviewService.find_user_owned_dataview_record",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc:
                await put_hidden_outputs(
                    7,
                    HiddenOutputChange(path=HIDDEN, hidden=True),
                    db=MagicMock(),
                    current_user=MagicMock(id=1),
                )
        assert exc.value.status_code == 404


def test_a_copied_record_hides_the_same_outputs_under_its_own_uid():
    source = MagicMock(hidden_outputs=[HIDDEN, "2/elsewhere/x"])
    db = MagicMock()
    db.query.return_value.filter.return_value.one.return_value = source

    ExperimentRecordService.copy_record(db, "1", "uid", "uid2", "copy")

    assert db.add.call_args.args[0].hidden_outputs == ["1/uid2/func1/heatmap"]

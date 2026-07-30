"""
Unit tests for WorkspaceDataCapacityService._update_exp_data_usage_db.

Guards that the check-then-write is serialized by a MySQL advisory lock so
concurrent writers do not create duplicate experiment_records rows (the table
has no unique constraint on (workspace_id, uid)), and that the lock is always
released.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

MODULE = "studio.app.common.core.workspace.workspace_data_capacity_services"

WORKSPACE_ID = "9"
UNIQUE_ID = "c2f43bda"


def _session_scope(db_mock):
    @contextmanager
    def _cm():
        yield db_mock

    return _cm


def _sql_texts(db):
    return [str(call.args[0]) for call in db.execute.call_args_list if call.args]


def test_inserts_under_advisory_lock_when_absent():
    from studio.app.common.core.workspace.workspace_data_capacity_services import (
        WorkspaceDataCapacityService,
    )

    db = MagicMock()
    db.execute.return_value.scalar.return_value = 1  # GET_LOCK acquired
    db.execute.return_value.first.return_value = None  # record absent

    with patch(f"{MODULE}.session_scope", _session_scope(db)):
        WorkspaceDataCapacityService._update_exp_data_usage_db(
            WORKSPACE_ID, UNIQUE_ID, 4096
        )

    texts = _sql_texts(db)
    assert any("GET_LOCK" in t for t in texts)
    assert any("RELEASE_LOCK" in t for t in texts)
    # Absent -> insert branch.
    db.add.assert_called_once()


def test_updates_without_insert_when_present():
    from studio.app.common.core.workspace.workspace_data_capacity_services import (
        WorkspaceDataCapacityService,
    )

    db = MagicMock()
    db.execute.return_value.scalar.return_value = 1  # GET_LOCK acquired
    db.execute.return_value.first.return_value = (5,)  # record present

    with patch(f"{MODULE}.session_scope", _session_scope(db)):
        WorkspaceDataCapacityService._update_exp_data_usage_db(
            WORKSPACE_ID, UNIQUE_ID, 4096
        )

    texts = _sql_texts(db)
    assert any("GET_LOCK" in t for t in texts)
    assert any("RELEASE_LOCK" in t for t in texts)
    # Present -> UPDATE branch, no ORM insert.
    db.add.assert_not_called()


def test_proceeds_and_skips_release_when_lock_unavailable():
    """If GET_LOCK errors (e.g. non-MySQL backend), the write still proceeds and
    no RELEASE_LOCK is attempted."""
    from studio.app.common.core.workspace.workspace_data_capacity_services import (
        WorkspaceDataCapacityService,
    )

    db = MagicMock()

    def _execute(statement, *args, **kwargs):
        result = MagicMock()
        if "GET_LOCK" in str(statement):
            raise RuntimeError("no GET_LOCK on this backend")
        result.first.return_value = None  # record absent
        return result

    db.execute.side_effect = _execute

    with patch(f"{MODULE}.session_scope", _session_scope(db)):
        WorkspaceDataCapacityService._update_exp_data_usage_db(
            WORKSPACE_ID, UNIQUE_ID, 4096
        )

    texts = _sql_texts(db)
    assert not any("RELEASE_LOCK" in t for t in texts)
    # Write still proceeds despite the missing lock.
    db.add.assert_called_once()

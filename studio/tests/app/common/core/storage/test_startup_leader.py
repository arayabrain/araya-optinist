"""Tests for the MySQL GET_LOCK-based leader election used by startup sync."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from studio.app.common.core.storage.startup_leader import (
    _LEADER_LOCK_NAME,
    startup_sync_leader_lock,
)


def _scoped_session_mock(get_lock_result):
    """Build a session_scope context manager whose `db.execute()` returns
    `get_lock_result` for GET_LOCK and a no-op for RELEASE_LOCK.
    """
    db = MagicMock()
    get_lock_row = MagicMock()
    get_lock_row.scalar.return_value = get_lock_result
    release_row = MagicMock()

    def execute(sql, params=None):
        # Distinguish GET_LOCK vs RELEASE_LOCK by inspecting the bound text.
        return get_lock_row if "GET_LOCK" in str(sql) else release_row

    db.execute.side_effect = execute

    @contextmanager
    def session_scope():
        yield db

    return session_scope, db


class TestStartupSyncLeaderLock:
    def test_acquires_lock_and_releases_on_normal_exit(self):
        session_scope, db = _scoped_session_mock(get_lock_result=1)
        with patch(
            "studio.app.common.core.storage.startup_leader.session_scope",
            new=session_scope,
        ):
            with startup_sync_leader_lock() as acquired:
                assert acquired is True

        # Two calls: GET_LOCK on entry, RELEASE_LOCK on exit.
        sql_strings = [str(call.args[0]) for call in db.execute.call_args_list]
        assert any("GET_LOCK" in s for s in sql_strings)
        assert any("RELEASE_LOCK" in s for s in sql_strings)

    def test_does_not_release_when_not_acquired(self):
        session_scope, db = _scoped_session_mock(get_lock_result=0)
        with patch(
            "studio.app.common.core.storage.startup_leader.session_scope",
            new=session_scope,
        ):
            with startup_sync_leader_lock() as acquired:
                assert acquired is False

        sql_strings = [str(call.args[0]) for call in db.execute.call_args_list]
        assert any("GET_LOCK" in s for s in sql_strings)
        assert not any("RELEASE_LOCK" in s for s in sql_strings)

    def test_releases_lock_when_block_raises(self):
        session_scope, db = _scoped_session_mock(get_lock_result=1)
        with patch(
            "studio.app.common.core.storage.startup_leader.session_scope",
            new=session_scope,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                with startup_sync_leader_lock() as acquired:
                    assert acquired is True
                    raise RuntimeError("boom")

        sql_strings = [str(call.args[0]) for call in db.execute.call_args_list]
        assert any("RELEASE_LOCK" in s for s in sql_strings)

    def test_null_return_treated_as_not_acquired(self):
        # MySQL GET_LOCK returns NULL on internal errors (lock service down,
        # killed connection, etc.). Must NOT yield True and must NOT release.
        session_scope, db = _scoped_session_mock(get_lock_result=None)
        with patch(
            "studio.app.common.core.storage.startup_leader.session_scope",
            new=session_scope,
        ):
            with startup_sync_leader_lock() as acquired:
                assert acquired is False

        sql_strings = [str(call.args[0]) for call in db.execute.call_args_list]
        assert not any("RELEASE_LOCK" in s for s in sql_strings)

    def test_get_lock_called_with_correct_name_and_timeout(self):
        session_scope, db = _scoped_session_mock(get_lock_result=1)
        with patch(
            "studio.app.common.core.storage.startup_leader.session_scope",
            new=session_scope,
        ):
            with startup_sync_leader_lock():
                pass

        get_lock_call = next(
            c for c in db.execute.call_args_list if "GET_LOCK" in str(c.args[0])
        )
        # Bound parameters live in args[1]; non-blocking acquisition uses timeout=0.
        params = get_lock_call.args[1]
        assert params == {"name": _LEADER_LOCK_NAME, "timeout": 0}

    def test_release_lock_called_with_correct_name(self):
        session_scope, db = _scoped_session_mock(get_lock_result=1)
        with patch(
            "studio.app.common.core.storage.startup_leader.session_scope",
            new=session_scope,
        ):
            with startup_sync_leader_lock():
                pass

        release_call = next(
            c for c in db.execute.call_args_list if "RELEASE_LOCK" in str(c.args[0])
        )
        params = release_call.args[1]
        assert params == {"name": _LEADER_LOCK_NAME}

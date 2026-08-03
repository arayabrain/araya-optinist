"""
Unit tests for update_user_storage_usage concurrency-safe write behavior.

Guards two properties of the Core-UPDATE rewrite:
  - an existing row is updated via a Core UPDATE (no ORM load-mutate, no insert)
  - the "Updated storage usage" success log is emitted only after commit
"""

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

MODULE = "studio.app.common.core.cloud.storage_tracking"


def _session_scope(db_mock, commit_raises=False):
    """Build a session_scope replacement whose context-exit optionally raises,
    simulating a commit failure at the end of the with-block."""

    @contextmanager
    def _cm():
        try:
            yield db_mock
        finally:
            if commit_raises:
                raise RuntimeError("commit failed")

    return _cm


def test_existing_row_updated_via_core_update(caplog):
    from studio.app.common.core.cloud.storage_tracking import update_user_storage_usage

    db = MagicMock()
    # Existence SELECT returns a row.
    db.execute.return_value.first.return_value = (123,)

    with patch(f"{MODULE}.session_scope", _session_scope(db)):
        with caplog.at_level(logging.INFO):
            result = update_user_storage_usage(9, 111)

    assert result is True
    # SELECT (existence) + UPDATE, and no ORM insert.
    assert db.execute.call_count == 2
    db.add.assert_not_called()
    assert any("Updated storage usage for user 9" in r.message for r in caplog.records)


def test_success_log_not_emitted_when_commit_fails(caplog):
    from studio.app.common.core.cloud.storage_tracking import update_user_storage_usage

    db = MagicMock()
    db.execute.return_value.first.return_value = (123,)

    with patch(f"{MODULE}.session_scope", _session_scope(db, commit_raises=True)):
        with caplog.at_level(logging.INFO):
            result = update_user_storage_usage(9, 111)

    assert result is False
    # The success log lives after the with-block, so a commit failure suppresses it.
    assert not any(
        "Updated storage usage for user 9" in r.message for r in caplog.records
    )
    assert any("Failed to update storage usage" in r.message for r in caplog.records)


def test_genuine_db_error_reports_failure_not_success(caplog):
    """A genuine write error (StaleDataError) must NOT be swallowed as the
    benign 'table not accessible' fallback: the function reports failure."""
    from sqlalchemy.orm.exc import StaleDataError

    from studio.app.common.core.cloud.storage_tracking import update_user_storage_usage

    db = MagicMock()
    db.execute.side_effect = StaleDataError("stale")

    with patch(f"{MODULE}.session_scope", _session_scope(db)):
        with caplog.at_level(logging.WARNING):
            result = update_user_storage_usage(9, 111)

    assert result is False
    # Must not be misclassified as the benign missing-table fallback...
    assert not any("table not accessible" in r.message for r in caplog.records)
    # ...nor logged as success.
    assert not any(
        "Updated storage usage for user 9" in r.message for r in caplog.records
    )
    assert any("Failed to update storage usage" in r.message for r in caplog.records)

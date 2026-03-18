"""
Unit tests for POST /users/me/frontend-errors endpoint.

Tests the endpoint handler, rate limiting, and stale entry cleanup.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from studio.app.common.core.utils.log_reader import FRONTEND_LOG_PREFIX
from studio.app.common.routers.users_me import (
    FrontendErrorBatch,
    FrontendErrorItem,
    _cleanup_stale_rate_limits,
    _frontend_error_timestamps,
    log_frontend_errors,
)


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 1
    user.uid = "test-user-uid"
    return user


@pytest.fixture(autouse=True)
def clear_rate_limiter():
    _frontend_error_timestamps.clear()
    yield
    _frontend_error_timestamps.clear()


def _make_batch(count=1, level="error"):
    return FrontendErrorBatch(
        errors=[
            FrontendErrorItem(
                level=level,
                message=f"test message {i}",
                url="http://localhost/page",
            )
            for i in range(count)
        ]
    )


class TestLogFrontendErrors:
    @pytest.mark.asyncio
    async def test_logs_single_error(self, mock_user):
        batch = _make_batch(1)
        result = await log_frontend_errors(batch=batch, current_user=mock_user)
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_logs_multiple_errors(self, mock_user):
        batch = _make_batch(3)
        result = await log_frontend_errors(batch=batch, current_user=mock_user)
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_logs_warn_level(self, mock_user):
        batch = _make_batch(1, level="warn")
        result = await log_frontend_errors(batch=batch, current_user=mock_user)
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_rate_limit_rejects_after_threshold(self, mock_user):
        from fastapi import HTTPException

        # Fill up rate limit
        _frontend_error_timestamps[mock_user.id] = [time.time()] * 10

        batch = _make_batch(1)
        with pytest.raises(HTTPException) as exc_info:
            await log_frontend_errors(batch=batch, current_user=mock_user)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_rate_limit_allows_after_window_expires(self, mock_user):
        # Timestamps from 61 seconds ago (outside 60s window)
        _frontend_error_timestamps[mock_user.id] = [time.time() - 61] * 10

        batch = _make_batch(1)
        result = await log_frontend_errors(batch=batch, current_user=mock_user)
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_records_timestamps_for_rate_limiting(self, mock_user):
        batch = _make_batch(2)
        await log_frontend_errors(batch=batch, current_user=mock_user)
        # Rate limit counts per request, not per item
        assert len(_frontend_error_timestamps[mock_user.id]) == 1

    @pytest.mark.asyncio
    async def test_logs_with_frontend_prefix(self, mock_user):
        batch = _make_batch(1)
        with patch("studio.app.common.routers.users_me.logger") as mock_logger:
            await log_frontend_errors(batch=batch, current_user=mock_user)
            call_args = mock_logger.error.call_args
            assert FRONTEND_LOG_PREFIX in call_args[0][0]

    @pytest.mark.asyncio
    async def test_logs_warn_uses_logger_warning(self, mock_user):
        batch = _make_batch(1, level="warn")
        with patch("studio.app.common.routers.users_me.logger") as mock_logger:
            await log_frontend_errors(batch=batch, current_user=mock_user)
            mock_logger.warning.assert_called_once()
            mock_logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_logs_error_uses_logger_error(self, mock_user):
        batch = _make_batch(1, level="error")
        with patch("studio.app.common.routers.users_me.logger") as mock_logger:
            await log_frontend_errors(batch=batch, current_user=mock_user)
            mock_logger.error.assert_called_once()
            mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_logs_source_and_url(self, mock_user):
        batch = FrontendErrorBatch(
            errors=[
                FrontendErrorItem(
                    level="error",
                    message="test",
                    url="http://localhost/page",
                    source="http://localhost/main.js",
                )
            ]
        )
        with patch("studio.app.common.routers.users_me.logger") as mock_logger:
            await log_frontend_errors(batch=batch, current_user=mock_user)
            logged = mock_logger.error.call_args[0]
            # Format string contains url and source placeholders
            full_message = logged[0] % logged[1:]
            assert "url=http://localhost/page" in full_message
            assert "source=http://localhost/main.js" in full_message


class TestRateLimitCleanup:
    def test_removes_expired_entries(self):
        old_time = time.time() - 120
        _frontend_error_timestamps[1] = [old_time]
        _frontend_error_timestamps[2] = [old_time]
        _frontend_error_timestamps[3] = [time.time()]

        _cleanup_stale_rate_limits()

        assert 1 not in _frontend_error_timestamps
        assert 2 not in _frontend_error_timestamps
        assert 3 in _frontend_error_timestamps

    def test_removes_empty_entries(self):
        _frontend_error_timestamps[1] = []
        _cleanup_stale_rate_limits()
        assert 1 not in _frontend_error_timestamps

    @pytest.mark.asyncio
    async def test_cleanup_triggered_at_threshold(self, mock_user):
        """Cleanup runs when dict exceeds threshold size."""
        # Fill with 101 stale entries
        old_time = time.time() - 120
        for i in range(101):
            _frontend_error_timestamps[i + 100] = [old_time]

        batch = _make_batch(1)
        await log_frontend_errors(batch=batch, current_user=mock_user)

        # Stale entries should have been cleaned up
        assert len(_frontend_error_timestamps) < 101


class TestFrontendErrorValidation:
    def test_rejects_invalid_level(self):
        with pytest.raises(Exception):
            FrontendErrorItem(level="info", message="test")

    def test_accepts_error_level(self):
        item = FrontendErrorItem(level="error", message="test")
        assert item.level == "error"

    def test_accepts_warn_level(self):
        item = FrontendErrorItem(level="warn", message="test")
        assert item.level == "warn"

    def test_rejects_too_many_items(self):
        with pytest.raises(Exception):
            FrontendErrorBatch(
                errors=[
                    FrontendErrorItem(level="error", message=f"msg {i}")
                    for i in range(21)
                ]
            )

    def test_accepts_max_items(self):
        batch = FrontendErrorBatch(
            errors=[
                FrontendErrorItem(level="error", message=f"msg {i}") for i in range(20)
            ]
        )
        assert len(batch.errors) == 20

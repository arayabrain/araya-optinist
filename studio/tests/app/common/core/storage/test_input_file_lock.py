"""
Unit tests for InputFileLock.

Tests advisory locking around on-demand input-file sync: lock path layout,
serialization of concurrent acquires on the same file, independence across
different files, fd cleanup on flock failure, and timeout fallback.
"""

import asyncio
import os
from unittest.mock import patch

import pytest

from studio.app.common.core.storage.remote_storage_controller import (
    InputFileLock,
    RemoteStorageDownloadUtils,
)


@pytest.fixture
def input_dir(tmp_path, monkeypatch):
    """Point DIRPATH.INPUT_DIR at a sandbox for the duration of the test."""
    monkeypatch.setattr(
        "studio.app.common.core.storage.remote_storage_controller.DIRPATH.INPUT_DIR",
        str(tmp_path),
    )
    return str(tmp_path)


@pytest.fixture(autouse=True)
def fast_lock_poll(monkeypatch):
    """Tighten the LOCK_NB poll interval so concurrent-acquire tests don't wait
    a full second between retries."""
    monkeypatch.setattr(InputFileLock, "LOCK_WAIT_POLL_INTERVAL", 0.01)


class TestLockPath:
    def test_lock_path_layout(self, input_dir):
        path = InputFileLock._lock_path("ws1", "data.tif")
        assert path == os.path.join(input_dir, "ws1", ".locks", "data.tif.lock")

    def test_lock_path_nested_filename(self, input_dir):
        path = InputFileLock._lock_path("ws1", "sub/dir/data.tif")
        assert path == os.path.join(
            input_dir, "ws1", ".locks", "sub", "dir", "data.tif.lock"
        )


class TestAcquireRelease:
    @pytest.mark.asyncio
    async def test_creates_lock_file_under_locks_dir(self, input_dir):
        async with InputFileLock.acquire("ws1", "data.tif"):
            assert os.path.isfile(
                os.path.join(input_dir, "ws1", ".locks", "data.tif.lock")
            )

    @pytest.mark.asyncio
    async def test_lock_file_persists_after_release(self, input_dir):
        """Lock file is intentionally not deleted — it's reused by the next caller."""
        async with InputFileLock.acquire("ws1", "data.tif"):
            pass
        assert os.path.isfile(os.path.join(input_dir, "ws1", ".locks", "data.tif.lock"))

    @pytest.mark.asyncio
    async def test_serializes_same_file(self, input_dir):
        """Two acquires on the same file run strictly one-at-a-time."""
        events = []

        async def hold(label, hold_for):
            async with InputFileLock.acquire("ws1", "data.tif"):
                events.append(f"{label}-enter")
                await asyncio.sleep(hold_for)
                events.append(f"{label}-exit")

        await asyncio.gather(hold("a", 0.1), hold("b", 0.05))

        # Whichever ran first must fully exit before the other enters.
        first = events[0].split("-")[0]
        second = "b" if first == "a" else "a"
        assert events == [
            f"{first}-enter",
            f"{first}-exit",
            f"{second}-enter",
            f"{second}-exit",
        ]

    @pytest.mark.asyncio
    async def test_independent_files_do_not_block(self, input_dir):
        """Locks on different files are independent."""
        b_started = asyncio.Event()
        a_can_finish = asyncio.Event()

        async def hold_a():
            async with InputFileLock.acquire("ws1", "a.tif"):
                # Wait until b has clearly entered its own critical section.
                await asyncio.wait_for(b_started.wait(), timeout=1.0)
                a_can_finish.set()

        async def hold_b():
            async with InputFileLock.acquire("ws1", "b.tif"):
                b_started.set()
                await asyncio.wait_for(a_can_finish.wait(), timeout=1.0)

        # If the locks were shared, this would deadlock and the timeouts fire.
        await asyncio.gather(hold_a(), hold_b())

    @pytest.mark.asyncio
    async def test_workspace_isolation(self, input_dir):
        """Same filename in different workspaces does not contend."""
        ws2_started = asyncio.Event()
        ws1_can_finish = asyncio.Event()

        async def hold_ws1():
            async with InputFileLock.acquire("ws1", "data.tif"):
                await asyncio.wait_for(ws2_started.wait(), timeout=1.0)
                ws1_can_finish.set()

        async def hold_ws2():
            async with InputFileLock.acquire("ws2", "data.tif"):
                ws2_started.set()
                await asyncio.wait_for(ws1_can_finish.wait(), timeout=1.0)

        await asyncio.gather(hold_ws1(), hold_ws2())


class TestFailureCleanup:
    @pytest.mark.asyncio
    async def test_flock_failure_closes_fd(self, input_dir):
        """If fcntl.flock raises, the just-opened fd must be closed (no leak)."""
        opened_fds = []
        real_open = os.open
        real_close = os.close

        def tracking_open(*args, **kwargs):
            fd = real_open(*args, **kwargs)
            opened_fds.append(fd)
            return fd

        closed = []

        def tracking_close(fd):
            closed.append(fd)
            real_close(fd)

        with patch(
            "studio.app.common.core.storage.remote_storage_controller.os.open",
            side_effect=tracking_open,
        ), patch(
            "studio.app.common.core.storage.remote_storage_controller.os.close",
            side_effect=tracking_close,
        ), patch(
            "studio.app.common.core.storage.remote_storage_controller.fcntl.flock",
            side_effect=OSError("simulated flock failure"),
        ):
            with pytest.raises(OSError, match="simulated flock failure"):
                async with InputFileLock.acquire("ws1", "data.tif"):
                    pass

        assert opened_fds, "expected os.open to be called"
        assert closed == opened_fds, f"fd leak: opened {opened_fds}, closed {closed}"


class TestTimeout:
    @pytest.mark.asyncio
    async def test_acquire_times_out_and_proceeds_without_lock(
        self, input_dir, monkeypatch, caplog
    ):
        """A blocked acquire should give up after LOCK_WAIT_MAX_SECONDS, log a
        warning, and let the body run anyway — atomic rename in the actual
        download still prevents partial reads."""
        import logging

        monkeypatch.setattr(InputFileLock, "LOCK_WAIT_MAX_SECONDS", 0.1)

        body_entered = asyncio.Event()
        holder_can_release = asyncio.Event()

        async def hold_forever():
            async with InputFileLock.acquire("ws1", "data.tif"):
                await holder_can_release.wait()

        holder = asyncio.create_task(hold_forever())
        # Wait for the holder to actually own the lock.
        await asyncio.sleep(0.05)

        with caplog.at_level(logging.WARNING):

            async def attempt():
                async with InputFileLock.acquire("ws1", "data.tif"):
                    body_entered.set()

            await asyncio.wait_for(attempt(), timeout=2.0)

        assert (
            body_entered.is_set()
        ), "acquire should fall through and run the body on timeout"
        assert any(
            "InputFileLock acquire timeout" in r.message for r in caplog.records
        ), "expected a timeout warning to be logged"

        holder_can_release.set()
        await holder


class TestEnsureInputFileSynced:
    """ensure_input_file_synced no longer locks directly — it delegates to
    download_input_data, which acquires InputFileLock at the implementation
    layer (S3 / Mock). These tests cover the orchestrator's own behavior:
    fast-path, exception propagation."""

    @pytest.mark.asyncio
    async def test_already_local_short_circuits_without_lock(self, input_dir):
        """If the file already exists locally, no controller is constructed."""
        ws, fn = "ws1", "data.tif"
        local = os.path.join(input_dir, ws, fn)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        open(local, "wb").close()

        with patch(
            "studio.app.common.core.storage.remote_storage_controller."
            "RemoteStorageController"
        ) as mock_ctrl:
            result = await RemoteStorageDownloadUtils.ensure_input_file_synced(
                ws, fn, "bucket-x"
            )

        assert result is True
        mock_ctrl.assert_not_called()
        # No lock file should have been created either.
        assert not os.path.exists(os.path.join(input_dir, ws, ".locks", f"{fn}.lock"))

    @pytest.mark.asyncio
    async def test_download_exception_propagates(self, input_dir):
        """Exceptions from download_input_data are surfaced to the caller."""
        from unittest.mock import AsyncMock

        ws, fn = "ws1", "data.tif"

        mock_controller = AsyncMock()
        mock_controller.download_input_data.side_effect = RuntimeError("boom")

        with patch(
            "studio.app.common.core.storage.remote_storage_controller."
            "RemoteStorageController",
            return_value=mock_controller,
        ), patch(
            "studio.app.common.core.storage.remote_storage_controller."
            "RemoteStorageController.is_available",
            return_value=True,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await RemoteStorageDownloadUtils.ensure_input_file_synced(
                    ws, fn, "bucket-x"
                )

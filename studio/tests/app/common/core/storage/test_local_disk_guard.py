"""The local disk guard: the reserve, the thresholds and the backoff."""

import errno
import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from studio.app.common.core.storage import local_disk_guard as guard

GIB = guard.GIB


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(
        guard,
        "_state",
        {"low_warned_at": None, "reclaimed_at": None, "reclaimed_bytes": 0},
    )
    monkeypatch.delenv(guard.MIN_FREE_DISK_ENV, raising=False)
    monkeypatch.setattr(guard.MODE, "IS_STANDALONE", False)


def _usage(*frees):
    """A disk_usage stand-in answering each call with the next free value and
    recording the path it was asked about."""
    calls = []
    values = list(frees)

    def disk_usage(path):
        calls.append(path)
        free = values.pop(0) if len(values) > 1 else values[0]
        return SimpleNamespace(total=100 * GIB, used=100 * GIB - free, free=free)

    disk_usage.calls = calls
    return disk_usage


@pytest.fixture
def reclaim(monkeypatch):
    mock = MagicMock(return_value=0)
    monkeypatch.setattr(guard, "reclaim_local_caches", mock)
    return mock


class TestMinFreeBytes:
    def test_defaults_to_8_gib(self):
        assert guard.min_free_bytes() == 8 * GIB

    def test_standalone_defaults_to_disabled(self, monkeypatch):
        monkeypatch.setattr(guard.MODE, "IS_STANDALONE", True)
        assert guard.min_free_bytes() == 0

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv(guard.MIN_FREE_DISK_ENV, "2.5")
        assert guard.min_free_bytes() == int(2.5 * GIB)

    @pytest.mark.parametrize("raw", ["0", "-1"])
    def test_zero_disables_without_touching_the_disk(self, monkeypatch, raw):
        monkeypatch.setenv(guard.MIN_FREE_DISK_ENV, raw)
        monkeypatch.setattr(
            guard.shutil, "disk_usage", lambda p: pytest.fail("disk_usage called")
        )
        guard.ensure_local_disk_headroom()
        guard.require_free_bytes(10**12, "/")

    def test_garbage_falls_back_to_the_default_with_a_warning(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv(guard.MIN_FREE_DISK_ENV, "lots")
        with caplog.at_level(logging.WARNING):
            assert guard.min_free_bytes() == 8 * GIB
        assert "OPTINIST_MIN_FREE_DISK_GIB" in caplog.text


class TestEnsureLocalDiskHeadroom:
    def test_checks_the_output_dir(self, monkeypatch, reclaim):
        usage = _usage(50 * GIB)
        monkeypatch.setattr(guard.shutil, "disk_usage", usage)
        guard.ensure_local_disk_headroom()
        assert usage.calls == [guard.DIRPATH.OUTPUT_DIR]
        reclaim.assert_not_called()

    def test_plenty_free_is_silent(self, monkeypatch, reclaim, caplog):
        monkeypatch.setattr(guard.shutil, "disk_usage", _usage(20 * GIB))
        with caplog.at_level(logging.WARNING):
            guard.ensure_local_disk_headroom()
        assert "LOCAL_DISK" not in caplog.text
        reclaim.assert_not_called()

    def test_under_twice_the_reserve_warns_once(self, monkeypatch, reclaim, caplog):
        monkeypatch.setattr(guard.shutil, "disk_usage", _usage(12 * GIB))
        with caplog.at_level(logging.WARNING):
            guard.ensure_local_disk_headroom()
            guard.ensure_local_disk_headroom()
        assert caplog.text.count("LOCAL_DISK_LOW") == 1
        reclaim.assert_not_called()

    def test_under_the_reserve_reclaims_then_passes(self, monkeypatch, reclaim):
        # 5 GiB free, 9 GiB after the reclaim
        monkeypatch.setattr(guard.shutil, "disk_usage", _usage(5 * GIB, 9 * GIB))
        reclaim.return_value = 4 * GIB
        guard.ensure_local_disk_headroom()
        reclaim.assert_called_once_with(target_free_bytes=8 * GIB)

    def test_still_under_after_reclaim_refuses(self, monkeypatch, reclaim, caplog):
        monkeypatch.setattr(guard.shutil, "disk_usage", _usage(5 * GIB))
        with caplog.at_level(logging.ERROR), pytest.raises(
            guard.LocalDiskFullError
        ) as info:
            guard.ensure_local_disk_headroom()
        assert "not your storage quota" in str(info.value)
        assert info.value.free_bytes == 5 * GIB
        assert info.value.required_bytes == 8 * GIB
        assert "LOCAL_DISK_FULL" in caplog.text

    def test_extra_bytes_raise_the_bar(self, monkeypatch, reclaim):
        monkeypatch.setattr(guard.shutil, "disk_usage", _usage(9 * GIB))
        guard.ensure_local_disk_headroom()
        with pytest.raises(guard.LocalDiskFullError) as info:
            guard.ensure_local_disk_headroom(extra_bytes=2 * GIB)
        assert info.value.required_bytes == 10 * GIB
        reclaim.assert_called_once_with(target_free_bytes=10 * GIB)

    def test_unmeasurable_disk_fails_open(self, monkeypatch, reclaim, caplog):
        def broken(path):
            raise OSError(errno.EIO, "statfs failed")

        monkeypatch.setattr(guard.shutil, "disk_usage", broken)
        with caplog.at_level(logging.WARNING):
            guard.ensure_local_disk_headroom()
        assert "LOCAL_DISK_CHECK_FAILED" in caplog.text
        reclaim.assert_not_called()

    def test_a_reclaim_that_freed_too_little_is_not_repeated(
        self, monkeypatch, reclaim
    ):
        """On a host whose space is docker's, every request would otherwise
        walk the data directory and refuse anyway."""
        monkeypatch.setattr(guard.shutil, "disk_usage", _usage(5 * GIB))
        reclaim.return_value = 1 * GIB
        with pytest.raises(guard.LocalDiskFullError):
            guard.ensure_local_disk_headroom()
        with pytest.raises(guard.LocalDiskFullError):
            guard.ensure_local_disk_headroom()
        assert reclaim.call_count == 1

    def test_a_reclaim_that_covered_the_shortfall_is_tried_again(
        self, monkeypatch, reclaim
    ):
        monkeypatch.setattr(
            guard.shutil, "disk_usage", _usage(5 * GIB, 9 * GIB, 5 * GIB, 9 * GIB)
        )
        reclaim.return_value = 4 * GIB
        guard.ensure_local_disk_headroom()
        guard.ensure_local_disk_headroom()
        assert reclaim.call_count == 2


class TestRequireFreeBytes:
    def test_fits_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(guard.shutil, "disk_usage", _usage(20 * GIB))
        guard.require_free_bytes(2 * GIB, "/data")

    def test_eating_into_the_reserve_is_enospc_naming_the_sizes(self, monkeypatch):
        usage = _usage(10 * GIB)
        monkeypatch.setattr(guard.shutil, "disk_usage", usage)
        with pytest.raises(OSError) as info:
            guard.require_free_bytes(3 * GIB, "/data")
        assert info.value.errno == errno.ENOSPC
        assert "3.2 GB" in str(info.value)  # 3 GiB
        assert "10.7 GB is free" in str(info.value)
        assert "8.6 GB must stay free" in str(info.value)
        assert usage.calls == ["/data"]

    def test_unmeasurable_disk_is_a_no_op(self, monkeypatch):
        def broken(path):
            raise OSError(errno.EIO, "statfs failed")

        monkeypatch.setattr(guard.shutil, "disk_usage", broken)
        guard.require_free_bytes(10**12, "/data")


class TestReserveFile:
    """The reserve check and the allocation happen under one host-wide lock,
    so two workers cannot both pass the check on the same free space."""

    @pytest.fixture
    def events(self, monkeypatch):
        import filelock

        log = []

        class Lock:
            def __init__(self, path, timeout):
                log.append(("lock", os.path.basename(path), timeout))

            def acquire(self):
                log.append("acquire")

            def release(self):
                log.append("release")

        monkeypatch.setattr(filelock, "FileLock", Lock)
        monkeypatch.setattr(
            os,
            "posix_fallocate",
            lambda fd, off, n: log.append(("fallocate", n)),
            raising=False,
        )
        monkeypatch.setattr(guard.shutil, "disk_usage", _usage(20 * GIB))
        return log

    def test_checks_and_allocates_under_the_lock(self, tmp_path, events):
        path = str(tmp_path / "a.mmap")
        guard.reserve_file(path, 4096)
        assert os.path.exists(path)
        assert events[1:] == ["acquire", ("fallocate", 4096), "release"]
        assert events[0][1].endswith("reserve_local_disk.lock")

    def test_a_refused_reservation_leaves_no_file(self, tmp_path, events, monkeypatch):
        monkeypatch.setattr(guard.shutil, "disk_usage", _usage(9 * GIB))
        path = str(tmp_path / "a.mmap")
        with pytest.raises(OSError) as info:
            guard.reserve_file(path, 2 * GIB)
        assert info.value.errno == errno.ENOSPC
        assert not os.path.exists(path)
        assert events[-1] == "release"

    def test_a_busy_lock_proceeds_unlocked(self, tmp_path, events, monkeypatch):
        import filelock

        class Busy:
            def __init__(self, *a, **k):
                pass

            def acquire(self):
                raise filelock.Timeout("busy")

        monkeypatch.setattr(filelock, "FileLock", Busy)
        path = str(tmp_path / "a.mmap")
        guard.reserve_file(path, 4096)
        assert os.path.exists(path)


class TestCaimanTempRoot:
    def test_resolves_as_caiman_paths_does(self, monkeypatch):
        monkeypatch.delenv("CAIMAN_TEMP", raising=False)
        monkeypatch.delenv("CAIMAN_DATA", raising=False)
        monkeypatch.setenv("HOME", "/home/u")
        assert guard.caiman_temp_root() == "/home/u/caiman_data/temp"
        monkeypatch.setenv("CAIMAN_DATA", "/opt/cd")
        assert guard.caiman_temp_root() == "/opt/cd/temp"
        monkeypatch.setenv("CAIMAN_TEMP", "/scratch")
        assert guard.caiman_temp_root() == "/scratch"

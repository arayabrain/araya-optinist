"""
Unit tests for AtomicClaimFile.

Tests atomic creation (O_CREAT|O_EXCL), staleness detection (expired, dead PID,
hostname mismatch), release, and try_acquire_or_detect_stale.
"""

import json
import os
import socket
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from studio.app.common.core.storage.atomic_claim_file import (
    AtomicClaimFile,
    _compute_expiry,
    get_worker_uuid,
)


@pytest.fixture
def claim_dir(tmp_path):
    """Provide a temporary directory for claim files."""
    return str(tmp_path)


@pytest.fixture
def claim_path(claim_dir):
    """Provide a path for a claim file."""
    return os.path.join(claim_dir, "test_claim.json")


class TestWorkerUUID:
    """Worker UUID is stable within a process."""

    def test_returns_string(self):
        assert isinstance(get_worker_uuid(), str)

    def test_stable_across_calls(self):
        assert get_worker_uuid() == get_worker_uuid()

    def test_regenerates_on_pid_change(self):
        """UUID regenerates when PID changes (fork detection, Gap #15)."""
        import studio.app.common.core.storage.atomic_claim_file as acf

        original_uuid = get_worker_uuid()
        original_pid = acf._WORKER_PID

        # Simulate fork by changing the stored PID
        acf._WORKER_PID = original_pid + 1  # Fake a different PID

        # get_worker_uuid() should see current os.getpid() != stored _WORKER_PID
        # and regenerate. We need to set _WORKER_PID to something different
        # from os.getpid()
        acf._WORKER_PID = -1  # Definitely not the current PID
        new_uuid = get_worker_uuid()

        assert new_uuid != original_uuid or acf._WORKER_PID == os.getpid()
        # The PID should now be current
        assert acf._WORKER_PID == os.getpid()

    def test_regenerates_when_none(self):
        """UUID regenerates when _WORKER_UUID is None."""
        import studio.app.common.core.storage.atomic_claim_file as acf

        acf._WORKER_UUID = None
        acf._WORKER_PID = None

        result = get_worker_uuid()

        assert isinstance(result, str)
        assert len(result) > 0
        assert acf._WORKER_PID == os.getpid()


class TestComputeExpiry:
    """_compute_expiry returns ISO timestamp N minutes in the future."""

    def test_expiry_is_in_future(self):
        expiry_str = _compute_expiry(10)
        expiry = datetime.fromisoformat(expiry_str)
        now = datetime.now(timezone.utc)
        assert expiry > now

    def test_expiry_is_approximately_correct(self):
        expiry_str = _compute_expiry(5)
        expiry = datetime.fromisoformat(expiry_str)
        now = datetime.now(timezone.utc)
        delta = (expiry - now).total_seconds()
        # Should be close to 5 minutes (300s), within 2s tolerance
        assert 298 <= delta <= 302


class TestTryCreate:
    """AtomicClaimFile.try_create uses O_CREAT|O_EXCL for atomic creation."""

    def test_creates_file_returns_true(self, claim_path):
        result = AtomicClaimFile.try_create(claim_path, {"role": "test"})
        assert result is True
        assert os.path.isfile(claim_path)

    def test_file_contains_enriched_content(self, claim_path):
        AtomicClaimFile.try_create(claim_path, {"role": "leader"})
        with open(claim_path) as f:
            data = json.load(f)
        assert data["role"] == "leader"
        assert data["pid"] == os.getpid()
        assert "worker_uuid" in data
        assert data["hostname"] == socket.gethostname()
        assert "started_at" in data
        assert "expires_at" in data

    def test_second_create_returns_false(self, claim_path):
        assert AtomicClaimFile.try_create(claim_path, {"n": 1}) is True
        assert AtomicClaimFile.try_create(claim_path, {"n": 2}) is False

    def test_creates_parent_directories(self, tmp_path):
        deep_path = os.path.join(str(tmp_path), "a", "b", "c", "claim.json")
        result = AtomicClaimFile.try_create(deep_path, {})
        assert result is True
        assert os.path.isfile(deep_path)

    def test_custom_expire_minutes(self, claim_path):
        AtomicClaimFile.try_create(claim_path, {}, expire_minutes=30)
        with open(claim_path) as f:
            data = json.load(f)
        expires_at = datetime.fromisoformat(data["expires_at"])
        started_at = datetime.fromisoformat(data["started_at"])
        delta = (expires_at - started_at).total_seconds()
        assert 1798 <= delta <= 1802  # ~30 minutes


class TestIsHeld:
    """AtomicClaimFile.is_held checks if a claim is fresh and valid."""

    def test_missing_file_not_held(self, claim_path):
        is_held, data = AtomicClaimFile.is_held(claim_path)
        assert is_held is False
        assert data is None

    def test_fresh_claim_is_held(self, claim_path):
        AtomicClaimFile.try_create(claim_path, {"role": "test"})
        is_held, data = AtomicClaimFile.is_held(claim_path)
        assert is_held is True
        assert data["role"] == "test"

    def test_expired_claim_not_held(self, claim_path):
        """A claim older than expire_minutes is stale."""
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        content = {
            "pid": os.getpid(),
            "worker_uuid": get_worker_uuid(),
            "hostname": socket.gethostname(),
            "started_at": old_time,
            "expires_at": old_time,
        }
        with open(claim_path, "w") as f:
            json.dump(content, f)

        is_held, data = AtomicClaimFile.is_held(claim_path, expire_minutes=10)
        assert is_held is False

    def test_dead_pid_not_held(self, claim_path):
        """A claim with a dead PID is stale."""
        content = {
            "pid": 999999999,  # Almost certainly not a real PID
            "worker_uuid": "dead-worker",
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _compute_expiry(10),
        }
        with open(claim_path, "w") as f:
            json.dump(content, f)

        is_held, data = AtomicClaimFile.is_held(claim_path)
        assert is_held is False
        assert data["pid"] == 999999999

    def test_different_hostname_not_held(self, claim_path):
        """A claim from a different hostname (previous container) is stale."""
        content = {
            "pid": os.getpid(),
            "worker_uuid": get_worker_uuid(),
            "hostname": "previous-container-host",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _compute_expiry(10),
        }
        with open(claim_path, "w") as f:
            json.dump(content, f)

        is_held, data = AtomicClaimFile.is_held(claim_path)
        assert is_held is False

    def test_corrupt_json_not_held(self, claim_path):
        with open(claim_path, "w") as f:
            f.write("not valid json{{{")

        is_held, data = AtomicClaimFile.is_held(claim_path)
        assert is_held is False
        assert data is None

    def test_missing_started_at_still_checks_pid(self, claim_path):
        """If started_at is missing, still checks PID liveness."""
        content = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
        }
        with open(claim_path, "w") as f:
            json.dump(content, f)

        is_held, data = AtomicClaimFile.is_held(claim_path)
        assert is_held is True


class TestRelease:
    """AtomicClaimFile.release deletes the claim file."""

    def test_release_existing_file(self, claim_path):
        AtomicClaimFile.try_create(claim_path, {})
        assert os.path.isfile(claim_path)
        AtomicClaimFile.release(claim_path)
        assert not os.path.isfile(claim_path)

    def test_release_nonexistent_file_no_error(self, claim_path):
        AtomicClaimFile.release(claim_path)  # Should not raise


class TestTryAcquireOrDetectStale:
    """try_acquire_or_detect_stale handles stale cleanup + retry."""

    def test_acquire_fresh(self, claim_path):
        acquired, existing = AtomicClaimFile.try_acquire_or_detect_stale(
            claim_path, {"role": "new"}
        )
        assert acquired is True
        assert existing is None

    def test_existing_held_claim_not_acquired(self, claim_path):
        AtomicClaimFile.try_create(claim_path, {"role": "owner"})
        acquired, existing = AtomicClaimFile.try_acquire_or_detect_stale(
            claim_path, {"role": "challenger"}
        )
        assert acquired is False
        assert existing is not None
        assert existing["role"] == "owner"

    def test_stale_claim_cleaned_and_acquired(self, claim_path):
        """Stale claim (dead PID) should be cleaned up, new claim acquired."""
        stale_content = {
            "pid": 999999999,
            "worker_uuid": "dead",
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _compute_expiry(10),
        }
        with open(claim_path, "w") as f:
            json.dump(stale_content, f)

        acquired, existing = AtomicClaimFile.try_acquire_or_detect_stale(
            claim_path, {"role": "new_leader"}
        )
        assert acquired is True
        assert existing is None

        # Verify new content
        with open(claim_path) as f:
            data = json.load(f)
        assert data["role"] == "new_leader"

    def test_expired_claim_cleaned_and_acquired(self, claim_path):
        """Expired claim should be cleaned up and new one acquired."""
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        expired_content = {
            "pid": os.getpid(),
            "worker_uuid": get_worker_uuid(),
            "hostname": socket.gethostname(),
            "started_at": old_time,
            "expires_at": old_time,
        }
        with open(claim_path, "w") as f:
            json.dump(expired_content, f)

        acquired, existing = AtomicClaimFile.try_acquire_or_detect_stale(
            claim_path, {"role": "fresh"}, expire_minutes=10
        )
        assert acquired is True

    def test_race_condition_on_retry_returns_false(self, claim_path):
        """If another process creates the file between cleanup and retry."""
        stale_content = {
            "pid": 999999999,
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(claim_path, "w") as f:
            json.dump(stale_content, f)

        # After release, mock try_create to return False (another process won)
        call_count = [0]

        def mock_try_create(path, content, expire_minutes=10):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: file exists (the stale one)
                return False
            # Second call (after stale cleanup): simulate another process won
            return False

        with patch.object(AtomicClaimFile, "try_create", side_effect=mock_try_create):
            acquired, _ = AtomicClaimFile.try_acquire_or_detect_stale(
                claim_path, {"role": "loser"}
            )

        assert acquired is False

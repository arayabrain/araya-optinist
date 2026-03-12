"""
Unit tests for startup leader election.

Tests try_become_startup_leader and release_startup_leader using
AtomicClaimFile under the hood.
"""

import json
import os

import pytest

from studio.app.common.core.storage.startup_leader import (
    _LEADER_FILE,
    release_startup_leader,
    try_become_startup_leader,
)


@pytest.fixture(autouse=True)
def clean_leader_file():
    """Ensure leader file is removed before/after each test."""
    if os.path.exists(_LEADER_FILE):
        os.remove(_LEADER_FILE)
    yield
    if os.path.exists(_LEADER_FILE):
        os.remove(_LEADER_FILE)


class TestTryBecomeStartupLeader:
    """Leader election: only one process wins."""

    def test_first_caller_becomes_leader(self):
        assert try_become_startup_leader() is True

    def test_leader_file_created(self):
        try_become_startup_leader()
        assert os.path.isfile(_LEADER_FILE)

    def test_leader_file_contains_role(self):
        try_become_startup_leader()
        with open(_LEADER_FILE) as f:
            data = json.load(f)
        assert data["role"] == "startup_leader"
        assert data["pid"] == os.getpid()

    def test_second_caller_deferred(self):
        """Second call returns False when first leader is still alive."""
        assert try_become_startup_leader() is True
        assert try_become_startup_leader() is False

    def test_stale_leader_allows_new_election(self):
        """Stale leader (dead PID) is cleaned up, new leader elected."""
        # Create a stale leader file with a dead PID
        import socket
        from datetime import datetime, timezone

        from studio.app.common.core.storage.atomic_claim_file import _compute_expiry

        stale = {
            "role": "startup_leader",
            "pid": 999999999,
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _compute_expiry(15),
        }
        os.makedirs(os.path.dirname(_LEADER_FILE), exist_ok=True)
        with open(_LEADER_FILE, "w") as f:
            json.dump(stale, f)

        # New process should win
        assert try_become_startup_leader() is True


class TestReleaseStartupLeader:
    """release_startup_leader removes the leader file."""

    def test_release_after_election(self):
        try_become_startup_leader()
        assert os.path.isfile(_LEADER_FILE)
        release_startup_leader()
        assert not os.path.isfile(_LEADER_FILE)

    def test_release_without_election_no_error(self):
        release_startup_leader()  # Should not raise

    def test_full_lifecycle(self):
        """Acquire -> release -> re-acquire."""
        assert try_become_startup_leader() is True
        release_startup_leader()
        assert try_become_startup_leader() is True


class TestTimedExpiry:
    """Tests for time-based leader expiry (Gap #16)."""

    def test_expired_leader_allows_new_election(self):
        """Leader file past 15-minute timeout is treated as stale."""
        import socket
        from datetime import datetime, timedelta, timezone

        # Create a leader file with an expired timestamp (16 minutes ago)
        expired_time = datetime.now(timezone.utc) - timedelta(minutes=16)
        stale = {
            "role": "startup_leader",
            "pid": os.getpid(),  # Use current PID so it's not detected as dead
            "hostname": socket.gethostname(),
            "started_at": expired_time.isoformat(),
            "expires_at": expired_time.isoformat(),  # Already expired
        }
        os.makedirs(os.path.dirname(_LEADER_FILE), exist_ok=True)
        with open(_LEADER_FILE, "w") as f:
            json.dump(stale, f)

        # Should win because the existing leader is expired
        assert try_become_startup_leader() is True

    def test_non_expired_leader_blocks_new_election(self):
        """Leader file within 15-minute timeout blocks new election."""
        import socket
        from datetime import datetime, timezone

        from studio.app.common.core.storage.atomic_claim_file import _compute_expiry

        # Create a leader file with a valid (non-expired) timestamp
        current = {
            "role": "startup_leader",
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": _compute_expiry(15),
        }
        os.makedirs(os.path.dirname(_LEADER_FILE), exist_ok=True)
        with open(_LEADER_FILE, "w") as f:
            json.dump(current, f)

        # Should be blocked since leader is still valid
        assert try_become_startup_leader() is False

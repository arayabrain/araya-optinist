"""The size scans' shared S3 client, their thread limit and their key locks."""

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

from studio.app.common.core.storage import storage_scan
from studio.app.common.core.storage.storage_scan import (
    MAX_CONCURRENT_SCANS,
    get_s3_client,
    run_storage_scan,
    storage_scan_lock,
)
from studio.tests.app.common.db_pool_probe import wait_until

CALLERS = 8


def test_callers_racing_for_the_first_client_share_one(monkeypatch):
    monkeypatch.setattr(storage_scan, "_s3_client", None)
    created = []

    def slow_client(self, service_name):
        # Wide enough for every caller to arrive while the first one creates
        time.sleep(0.05)
        created.append(object())
        return created[-1]

    start = threading.Barrier(CALLERS)
    clients = []

    def caller():
        start.wait()
        clients.append(get_s3_client())

    with patch.object(storage_scan.boto3.session.Session, "client", slow_client):
        threads = [threading.Thread(target=caller) for _ in range(CALLERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

    assert len(created) == 1
    assert clients == created * CALLERS


@pytest.mark.asyncio
async def test_scans_past_the_limit_wait_for_a_slot_off_the_loop():
    release = threading.Event()
    counter = threading.Lock()
    running, peak = [0], [0]

    def scan():
        with counter:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        release.wait(2)
        with counter:
            running[0] -= 1
        return "done"

    scans = [
        asyncio.create_task(run_storage_scan(scan))
        for _ in range(MAX_CONCURRENT_SCANS + 2)
    ]
    # The loop keeps running while the scans are held
    await wait_until(lambda: running[0] >= MAX_CONCURRENT_SCANS)
    await asyncio.sleep(0.05)
    assert running[0] == MAX_CONCURRENT_SCANS
    release.set()

    assert await asyncio.gather(*scans) == ["done"] * (MAX_CONCURRENT_SCANS + 2)
    assert peak[0] == MAX_CONCURRENT_SCANS


@pytest.mark.asyncio
async def test_a_key_is_held_by_one_at_a_time_and_dropped_when_free():
    release = asyncio.Event()
    holding = []

    async def hold(key, name):
        async with storage_scan_lock(key):
            holding.append(name)
            await release.wait()

    tasks = [
        asyncio.create_task(hold(key, name))
        for key, name in (("user:1", "a"), ("user:1", "b"), ("user:2", "c"))
    ]
    # Another key runs at once; the same key waits
    await wait_until(lambda: len(holding) == 2)
    await asyncio.sleep(0.05)
    assert holding == ["a", "c"]
    release.set()
    await asyncio.gather(*tasks)

    assert holding == ["a", "c", "b"]
    # No entry is kept for a key no one holds or waits for
    assert storage_scan._scan_locks.get() == {}

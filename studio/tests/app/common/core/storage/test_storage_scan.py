"""The size scans' shared S3 client, and their thread limit."""

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

"""
Test environment, applied before any `studio` module is imported.

Supersedes the `environment` block of `docker-compose.test.yml`: it sets the
same variables, and overrides that block's `TZ` and `STRIPE_*` with identical
values, so the container lane is unchanged. Three differences make the suite
runnable outside the container as well:

- `OPTINIST_DIR` derives from this file's location, so it resolves to `/app` in
  the container and to the checkout root anywhere else. Under xdist each worker
  gets a temporary copy instead, so files reusing workspace and run ids cannot
  collide.
- Values are set unconditionally, so a shell configured for running the app
  natively cannot leak into a test run.
- Stripe credentials are dummies. The suite never calls Stripe, and this drops
  its dependency on the gitignored `studio/config/.env`.

Add new test environment variables here, not to `docker-compose.test.yml` alone.
"""

import atexit
import os
import shutil
import tempfile
import time
from pathlib import Path

_ROOT_DIR = Path(__file__).resolve().parent
_DATA_DIR = _ROOT_DIR / "studio" / "test_data"
_MOCK_STORAGE_DIR = "/tmp/studio/mock_storage"

# Test files reuse workspace and run ids, so each xdist worker gets its own copy
_WORKER = os.environ.get("PYTEST_XDIST_WORKER")
if _WORKER:
    _worker_dir = Path(tempfile.mkdtemp(prefix=f"optinist-xdist-{_WORKER}-"))
    atexit.register(shutil.rmtree, _worker_dir, ignore_errors=True)
    _source = _DATA_DIR
    _DATA_DIR = _worker_dir / "test_data"
    shutil.copytree(
        _source,
        _DATA_DIR,
        ignore=lambda d, names: ["output"] if Path(d) == _source else [],
    )
    _MOCK_STORAGE_DIR = str(_worker_dir / "mock_storage")

_TEST_ENV = {
    # Expirations round-trip through the DB as naive datetimes, so a non-UTC
    # zone shifts them by its offset.
    "TZ": "UTC",
    "OPTINIST_DIR": str(_DATA_DIR),
    "IS_TEST": "True",
    "IS_STANDALONE": "True",
    "REMOTE_STORAGE_TYPE": "1",  # See studio/config/.env
    # The container's literal path, or under xdist one inside the worker's copy.
    # `DIRPATH.LOCKFILE_DIR` stays shared; lock names hash the data path.
    "MOCK_STORAGE_DIR": _MOCK_STORAGE_DIR,  # See studio/config/.env
    "STRIPE_SECRET_KEY": "sk_test_dummy_123",
    "STRIPE_WEBHOOK_SECRET": "whsec_dummy_123",
    "STRIPE_CALLBACK_URL": "http://localhost:8000",
}

os.environ.update(_TEST_ENV)

# `TZ` is cached on first use, so make the C library re-read it.
if hasattr(time, "tzset"):
    time.tzset()

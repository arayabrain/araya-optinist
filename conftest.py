"""
Test environment, applied before any `studio` module is imported.

Mirrors the `environment` block of `docker-compose.test.yml`, with three
differences that make the suite runnable outside the container as well:

- Paths derive from this file's location, so the same values resolve to `/app`
  in the container and to the checkout root anywhere else.
- Values are set unconditionally, so a shell configured for running the app
  natively cannot leak into a test run.
- Stripe credentials are dummies. The suite never calls Stripe, and this drops
  its dependency on the gitignored `studio/config/.env`.

Add new test environment variables here, not to `docker-compose.test.yml` alone.
"""

import os
import time
from pathlib import Path

_ROOT_DIR = Path(__file__).resolve().parent

_TEST_ENV = {
    # Expirations round-trip through the DB as naive datetimes, so a non-UTC
    # zone shifts them by its offset.
    "TZ": "UTC",
    "OPTINIST_DIR": str(_ROOT_DIR / "studio" / "test_data"),
    "IS_TEST": "True",
    "IS_STANDALONE": "True",
    "REMOTE_STORAGE_TYPE": "1",  # See studio/config/.env
    "MOCK_STORAGE_DIR": "/tmp/studio/mock_storage",  # See studio/config/.env
    "STRIPE_SECRET_KEY": "sk_test_dummy_123",
    "STRIPE_WEBHOOK_SECRET": "whsec_dummy_123",
    "STRIPE_CALLBACK_URL": "http://localhost:8000",
}

os.environ.update(_TEST_ENV)

# `TZ` is cached on first use, so make the C library re-read it.
if hasattr(time, "tzset"):
    time.tzset()

"""The container's database wait: infrastructure/scripts/wait_for_database.sh.

Runs the real script with ``mysql`` stubbed on PATH. It needs bash 5.1 or
later, as cloud-startup.sh does: from bash 4.1, ``set -e`` also exits on an
arithmetic command that evaluates to 0, so a ``((counter++))`` from 0 ends the
wait after its first failed attempt. An older bash would hide that.

The bounds are shrunk through the script's test overrides. ``SECONDS`` counts
whole seconds, so the whole wait can overrun its bound by under a second.
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CLOUD_STARTUP_SH = PROJECT_ROOT / "cloud-startup.sh"
WAIT_FOR_DATABASE_SH = (
    PROJECT_ROOT / "infrastructure" / "scripts" / "wait_for_database.sh"
)

BASH = shutil.which("bash")

# Records each call's arguments, then fails STUB_FAILURES times before
# succeeding; with STUB_BLOCK set, a failing call blocks instead.
MYSQL_STUB = """#!/bin/sh
echo "$*" >> "$STUB_CALLS"
if [ "$(wc -l < "$STUB_CALLS")" -gt "$STUB_FAILURES" ]; then
    exit 0
fi
if [ -n "$STUB_BLOCK" ]; then
    exec sleep 30
fi
echo "ERROR 2003 (HY000): Can't connect to MySQL server" >&2
exit 1
"""


def _bash_version() -> tuple:
    if BASH is None:
        return (0, 0)
    result = subprocess.run(
        [BASH, "-c", 'echo "${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return tuple(int(part) for part in result.stdout.split())


def _run_wait(
    tmp_path: Path,
    *,
    failures: int,
    block: bool = False,
    ssl_mode: str | None = None,
    wait: int = 30,
    attempt_timeout: int = 5,
    retry_interval: int = 0,
):
    """Run the script; return (result, elapsed seconds, recorded mysql calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "mysql"
    stub.write_text(MYSQL_STUB)
    stub.chmod(0o755)
    calls = tmp_path / "mysql_calls"
    calls.touch()

    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "MYSQL_SERVER": "db.invalid",
        "MYSQL_USER": "optinist",
        "MYSQL_PASSWORD": "secret",
        "MYSQL_DATABASE": "optinist",
        "DB_WAIT_SECONDS": str(wait),
        "DB_ATTEMPT_TIMEOUT_SECONDS": str(attempt_timeout),
        "DB_RETRY_INTERVAL_SECONDS": str(retry_interval),
        "STUB_CALLS": str(calls),
        "STUB_FAILURES": str(failures),
    }
    if block:
        env["STUB_BLOCK"] = "1"
    if ssl_mode is not None:
        env["MYSQL_SSL_MODE"] = ssl_mode

    started = time.monotonic()
    result = subprocess.run(
        [BASH, str(WAIT_FOR_DATABASE_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=wait + 15,
    )
    elapsed = time.monotonic() - started
    return result, elapsed, calls.read_text().splitlines()


@pytest.mark.skipif(
    _bash_version() < (5, 1) or shutil.which("timeout") is None,
    reason="needs bash >= 5.1 and timeout, as in the app image",
)
class TestWaitForDatabase:
    def test_retries_until_the_database_answers(self, tmp_path):
        result, _, calls = _run_wait(tmp_path, failures=2)

        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines() == [
            "Attempt 1: Waiting for Database...",
            "Attempt 2: Waiting for Database...",
            "Database connection successful",
        ]
        assert len(calls) == 3

    def test_gives_up_within_the_bound_when_every_attempt_fails(self, tmp_path):
        wait = 3
        result, elapsed, calls = _run_wait(
            tmp_path, failures=1000, wait=wait, attempt_timeout=1, retry_interval=1
        )

        assert result.returncode == 1
        assert "Attempt 1: Waiting for Database..." in result.stdout
        assert "Failed to connect to Database" in result.stdout
        assert len(calls) >= 2
        assert elapsed < wait + 1.5

    def test_bounds_an_attempt_that_blocks(self, tmp_path):
        """An RDS Proxy with no target holds the connection open; without a
        per-attempt bound one attempt alone outlasts the whole wait."""
        wait = 3
        result, elapsed, _ = _run_wait(
            tmp_path, failures=1000, block=True, wait=wait, attempt_timeout=1
        )

        assert result.returncode == 1
        assert "Attempt 1: no answer within 1 s" in result.stdout
        assert "Failed to connect to Database" in result.stdout
        assert elapsed < wait + 1.5

    @pytest.mark.parametrize(
        "ssl_mode,expected",
        [(None, set()), ("DISABLED", {"--skip-ssl"}), ("REQUIRED", {"--ssl"})],
    )
    def test_ssl_mode_selects_the_client_option(self, tmp_path, ssl_mode, expected):
        result, _, calls = _run_wait(tmp_path, failures=0, ssl_mode=ssl_mode)

        assert result.returncode == 0, result.stdout + result.stderr
        assert set(calls[0].split()) & {"--ssl", "--skip-ssl"} == expected


@pytest.mark.parametrize("script", [CLOUD_STARTUP_SH, WAIT_FOR_DATABASE_SH])
def test_no_post_increment_statement_under_set_e(script):
    """``((n++))`` from 0 evaluates to 0 and returns 1; under ``set -e`` that
    exits the script. Covers loops no test runs, such as the load balancer
    check, which is off unless AWS_SERVICE_URL is set."""
    statements = re.findall(
        r"^\s*\(\(\s*\w+\s*(?:\+\+|--)\s*\)\)\s*$", script.read_text(), re.MULTILINE
    )
    assert statements == []

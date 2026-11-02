"""Stop-signal forwarding in cloud-startup.sh.

The script forwards a stop from two places: its trap, and the end of the
script once ``wait -n`` returns. Each child must get SIGTERM once: uvicorn
0.24's multi-worker parent can deadlock on a second one inside its signal
handler, and the cleanup worker logs a traceback for it.

Runs the script's own function definitions in bash, with ``kill`` replaced by
a function that records its arguments, so the PIDs need no real processes.
The trap is raised with the real ``kill`` builtin.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CLOUD_STARTUP_SH = PROJECT_ROOT / "cloud-startup.sh"

BASH = shutil.which("bash")

CLEANUP, APP, LB_CHECK = "101", "102", "103"
EACH_ONCE = [f"-TERM {pid}" for pid in (CLEANUP, APP, LB_CHECK)]
TRAP_LINE = "Received shutdown signal, forwarding to child processes..."

STOP = "builtin kill -TERM $$"
END_STEP = "_forward_shutdown || true"


def _function_definitions() -> str:
    """Every top-level function of cloud-startup.sh, as source."""
    definitions = re.findall(
        r"^[A-Za-z_]\w*\(\) *\{\n.*?^\}\n",
        CLOUD_STARTUP_SH.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    assert any(d.startswith("_forward_shutdown()") for d in definitions)
    return "".join(definitions)


def _run(tmp_path: Path, *steps: str, kill_status: int = 0):
    """Run the steps after the script's functions and its trap; return the
    result and the recorded kills."""
    kills = tmp_path / "kills"
    kills.touch()
    script = "\n".join(
        [
            "set -e",
            f'kill() {{ echo "$*" >> "{kills}"; return {kill_status}; }}',
            _function_definitions(),
            "trap _forward_shutdown TERM INT",
            *steps,
            'echo "reached the end"',
        ]
    )
    result = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, timeout=10
    )
    return result, kills.read_text().splitlines()


@pytest.mark.skipif(BASH is None, reason="needs bash")
class TestForwardShutdown:
    def test_a_stop_signals_each_child_once(self, tmp_path):
        """The trap forwards, then ``wait -n`` returns and the end step runs."""
        result, kills = _run(
            tmp_path,
            f"CLEANUP_PID={CLEANUP} APP_PID={APP} LB_CHECK_PID={LB_CHECK}",
            STOP,
            END_STEP,
        )

        assert result.stdout.splitlines() == [TRAP_LINE, TRAP_LINE, "reached the end"]
        assert kills == EACH_ONCE

    def test_a_stop_in_the_startup_sleep_reaches_the_load_balancer_check(
        self, tmp_path
    ):
        """bash runs the trap when the sleep returns, before the load balancer
        check starts; only the end step can signal that check."""
        result, kills = _run(
            tmp_path,
            f"CLEANUP_PID={CLEANUP} APP_PID={APP}",
            STOP,
            f"LB_CHECK_PID={LB_CHECK}",
            END_STEP,
        )

        assert "reached the end" in result.stdout
        assert kills == EACH_ONCE

    def test_a_stop_before_the_app_starts_reaches_the_app(self, tmp_path):
        """The trap is installed before the children start. A single
        "already forwarded" flag would skip the app at the end step."""
        result, kills = _run(
            tmp_path,
            f"CLEANUP_PID={CLEANUP}",
            STOP,
            f"APP_PID={APP} LB_CHECK_PID={LB_CHECK}",
            END_STEP,
        )

        assert "reached the end" in result.stdout
        assert kills == EACH_ONCE

    def test_a_child_exiting_on_its_own_then_a_stop_signals_each_once(self, tmp_path):
        """No trap ran, so the end step signals the rest; a stop arriving
        during the drain that follows sends nothing more."""
        result, kills = _run(
            tmp_path,
            f"CLEANUP_PID={CLEANUP} APP_PID={APP} LB_CHECK_PID={LB_CHECK}",
            END_STEP,
            STOP,
        )

        assert result.stdout.splitlines() == [TRAP_LINE, TRAP_LINE, "reached the end"]
        assert kills == EACH_ONCE

    def test_a_failed_kill_does_not_abort_the_trap(self, tmp_path):
        """``set -e`` applies inside the trap; a kill against an exited child
        fails, and the remaining children must still be signalled."""
        result, kills = _run(
            tmp_path,
            f"CLEANUP_PID={CLEANUP} APP_PID={APP} LB_CHECK_PID={LB_CHECK}",
            STOP,
            kill_status=1,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "reached the end" in result.stdout
        assert kills == EACH_ONCE

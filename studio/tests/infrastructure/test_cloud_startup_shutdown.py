"""Stop-signal forwarding in cloud-startup.sh.

The script forwards a stop from two places: its trap, and the end of the
script once ``wait -n`` returns. Each child must get SIGTERM once: uvicorn
0.24's multi-worker parent can deadlock on a second one inside its signal
handler, and the cleanup worker logs a traceback for it.

Runs the script's own function definitions and trap in bash, with ``kill``
replaced by a function that records its arguments, so the PIDs need no real
processes. ``kill -0`` and the stop itself use the real ``kill`` builtin.
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


def _trap() -> str:
    """The script's trap line."""
    (trap,) = re.findall(
        r"^trap .+ TERM INT$", CLOUD_STARTUP_SH.read_text(), re.MULTILINE
    )
    return trap


def _run(tmp_path: Path, *steps: str, kill_status: int = 0):
    """Run the steps after the script's functions and its trap; return the
    result and the recorded kills."""
    kills = tmp_path / "kills"
    kills.touch()
    script = "\n".join(
        [
            "set -e",
            "kill() {",
            '    if [ "$1" = -0 ]; then builtin kill "$@"; return; fi',
            f'    echo "$*" >> "{kills}"; return {kill_status}',
            "}",
            _function_definitions(),
            _trap(),
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
        """The trap forwards, then ``wait -n`` returns and the end step runs.
        Only the trap logs the stop."""
        result, kills = _run(
            tmp_path,
            f"CLEANUP_PID={CLEANUP} APP_PID={APP} LB_CHECK_PID={LB_CHECK}",
            STOP,
            END_STEP,
        )

        assert result.stdout.splitlines() == [TRAP_LINE, "reached the end"]
        assert kills == EACH_ONCE

    def test_a_stop_in_the_startup_pause_is_forwarded_at_once(self, tmp_path):
        """bash runs a trap only once a foreground command returns. A stop held
        until the pause ends loses that time from the app's drain."""
        result, kills = _run(
            tmp_path,
            f"CLEANUP_PID={CLEANUP} APP_PID={APP}",
            f"( sleep 0.3; {STOP} ) &",
            "_pause 3",
            'echo "pause returned after $SECONDS s"',
        )

        assert kills == [f"-TERM {CLEANUP}", f"-TERM {APP}"]
        (seconds,) = re.findall(r"pause returned after (\d+) s", result.stdout)
        assert int(seconds) < 2, result.stdout

    def test_a_stop_in_the_startup_pause_reaches_the_load_balancer_check(
        self, tmp_path
    ):
        """The trap runs before the load balancer check starts; only the end
        step can signal that check."""
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
        """No trap ran, so the end step signals the rest, and logs no stop; a
        stop arriving during the drain that follows sends nothing more."""
        result, kills = _run(
            tmp_path,
            f"CLEANUP_PID={CLEANUP} APP_PID={APP} LB_CHECK_PID={LB_CHECK}",
            END_STEP,
            STOP,
        )

        assert result.stdout.splitlines() == [TRAP_LINE, "reached the end"]
        assert kills == EACH_ONCE

    def test_a_stop_during_the_drain_waits_for_the_app(self, tmp_path):
        """A trapped signal ends ``wait`` while the app still drains. The
        script exiting then would, as PID 1, kill the app mid-drain."""
        drained = tmp_path / "drained"
        result, kills = _run(
            tmp_path,
            f'( sleep 2; touch "{drained}" ) &',
            "APP_PID=$!",
            END_STEP,
            f"( sleep 0.5; {STOP} ) &",
            '_wait_exit "$APP_PID"',
            f'[ -e "{drained}" ] && echo "the app drained"',
        )

        assert result.stdout.splitlines() == [
            TRAP_LINE,
            "the app drained",
            "reached the end",
        ]
        assert len(kills) == 1

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

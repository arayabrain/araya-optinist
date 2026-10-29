"""The server entry points start uvicorn without importing the app.

Above one worker, the process that runs ``main()`` only supervises; each
worker imports ``studio.__main_unit__`` itself. An app import on the entry
path holds one more full copy of the app in every container. Instead, the app
is imported once in a child process first, so a broken app still stops the
server rather than leaving a supervisor with no worker.
"""

import subprocess
import sys

import pytest

from studio.app.dir_path import DIRPATH

# A fresh process: the suite has already imported the app. Runs the entry as
# its command line does, with uvicorn.run replaced; the import check runs for
# real, in its own child process.
_PROBE = """
import runpy
import sys
from unittest.mock import patch

import uvicorn

sys.argv = ["entry", "--workers", "3"]
with patch.object(uvicorn, "run") as run:
    {run_entry}
assert "studio.__main_unit__" not in sys.modules, "the entry imported the app"
for heavy in ("fastapi", "numpy", "pandas"):
    assert heavy not in sys.modules, f"the entry imported {{heavy}}"
assert run.call_count == 1
assert run.call_args.args == ("studio.__main_unit__:app",)
assert run.call_args.kwargs["workers"] == 3
"""

# The child's app import fails: the server must not start above one worker.
# With one worker uvicorn imports the app itself, so there is no check to fail.
_FAILED_IMPORT_PROBE = """
import runpy
import subprocess
import sys
from unittest.mock import patch

import uvicorn

sys.argv = ["entry", "--workers", "{workers}"]
failed = subprocess.CompletedProcess(args=[], returncode=1)
with patch.object(uvicorn, "run") as run, patch.object(
    subprocess, "run", return_value=failed
) as child:
    try:
        runpy.run_path("main.py", run_name="__main__")
        code = 0
    except SystemExit as stop:
        code = stop.code
assert (code, run.call_count, child.call_count) == {expected}, (
    code, run.call_count, child.call_count
)
"""


def _run(code):
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=DIRPATH.ROOT_DIR,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "run_entry",
    [
        # cloud-startup.sh and docker-compose
        'runpy.run_path("main.py", run_name="__main__")',
        # run_optinist
        'runpy.run_module("studio", run_name="__main__")',
    ],
    ids=["main.py", "python -m studio"],
)
def test_entry_runs_uvicorn_without_importing_the_app(run_entry):
    result = _run(_PROBE.format(run_entry=run_entry))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "workers,expected",
    [
        # exits 1 before uvicorn.run, after one import check
        (3, (1, 0, 1)),
        # starts as before, without an import check
        (1, (0, 1, 0)),
    ],
    ids=["3 workers", "1 worker"],
)
def test_failed_app_import_stops_the_server_above_one_worker(workers, expected):
    result = _run(_FAILED_IMPORT_PROBE.format(workers=workers, expected=expected))
    assert result.returncode == 0, result.stderr

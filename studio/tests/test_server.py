"""The server entry points start uvicorn without importing the app.

Above one worker, the process that runs ``main()`` only supervises; each
worker imports ``studio.__main_unit__`` itself. An app import on the entry
path holds one more full copy of the app in every container.
"""

import subprocess
import sys

import pytest

from studio.app.dir_path import DIRPATH

# A fresh process: the suite has already imported the app. Runs the entry as
# its command line does, with uvicorn.run replaced.
_PROBE = """
import runpy
import sys
from unittest.mock import patch

import uvicorn

sys.argv = ["entry", "--workers", "3"]
with patch.object(uvicorn, "run") as run:
    {run_entry}
assert "studio.__main_unit__" not in sys.modules, "the entry imported the app"
assert run.call_count == 1
assert run.call_args.args == ("studio.__main_unit__:app",)
assert run.call_args.kwargs["workers"] == 3
"""


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
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(run_entry=run_entry)],
        cwd=DIRPATH.ROOT_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

"""The dirty-worktree guard in ecr_build_push.sh names what blocks the build."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ECR_BUILD_PUSH = PROJECT_ROOT / "infrastructure" / "scripts" / "ecr_build_push.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required to build the fixture"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def dirty_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tester@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "tracked.txt").write_text("one")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c1")
    (repo / "tracked.txt").write_text("two")
    (repo / "scratch.md").write_text("notes")
    return repo


def test_lists_changed_and_untracked_files_and_how_to_fix(dirty_repo, tmp_path):
    # Stubs that fail, so a broken guard cannot reach a real build or push
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for tool in ("aws", "docker", "terraform", "yarn", "npm"):
        stub = stubs / tool
        stub.write_text("#!/bin/sh\nexit 97\n")
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run(
        ["bash", str(ECR_BUILD_PUSH), "--yes"],
        cwd=dirty_repo,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    out = result.stdout
    changed, untracked = out.index("Changed"), out.index("Untracked (not in git)")
    assert changed < out.index("  tracked.txt") < untracked
    assert untracked < out.index("  scratch.md")
    assert "??" not in out
    assert "git stash -u" in out
    assert "rerunning\nterraform apply retries the build" in out

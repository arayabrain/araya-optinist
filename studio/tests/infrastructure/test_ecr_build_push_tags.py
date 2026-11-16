"""Which tags ecr_build_push.sh pushes, and how it pushes them.

Runs a copy of the real script inside a throwaway git repository, with stub
terraform / aws / docker / yarn on PATH. git is real, so the version tag comes
from the tags the test puts on HEAD; the stub docker records every call.
"""

import os
import pty
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = PROJECT_ROOT / "infrastructure" / "scripts"
ECR_URI = "123.dkr.ecr.ap-northeast-1.amazonaws.com/repo"

TERRAFORM_STUB = f"""
case "$4" in
    environment) echo development ;;
    ecr_repository_url) echo {ECR_URI} ;;
    domain_port) echo 443 ;;
    *) echo x ;;
esac
"""

# No GTM ID in SSM: the build goes ahead with analytics off
AWS_STUB = """
case "$1 $2" in
    "ecr get-login-password") echo pw ;;
    "ssm get-parameter")
        echo "An error occurred (ParameterNotFound)" >&2
        exit 254 ;;
esac
"""

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required to build the fixture"
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def build(tmp_path):
    """A clean, committed checkout holding the script; returns a runner."""
    repo = tmp_path / "repo"
    scripts = repo / "infrastructure" / "scripts"
    scripts.mkdir(parents=True)
    (repo / "infrastructure" / "terraform").mkdir()
    (repo / "frontend").mkdir()
    for name in ("ecr_build_push.sh", "git_ref_info.sh"):
        shutil.copy(SCRIPTS / name, scripts / name)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tester@example.com")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c1")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    _stub(bin_dir / "terraform", TERRAFORM_STUB)
    _stub(bin_dir / "aws", AWS_STUB)
    _stub(bin_dir / "yarn", "exit 0")
    _stub(bin_dir / "docker", f'echo "docker $*" >> "{calls}"')
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    def run(*args: str, tty: bool = False):
        cmd = ["bash", str(scripts / "ecr_build_push.sh"), "--yes", *args]
        if tty:
            output, returncode = _run_on_a_terminal(cmd, repo, env)
        else:
            result = subprocess.run(
                cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=60
            )
            output, returncode = result.stdout + result.stderr, result.returncode
        assert returncode == 0, output
        pushes = [
            c for c in calls.read_text().splitlines() if c.startswith("docker push")
        ]
        return output, pushes

    run.repo = repo
    return run


def _run_on_a_terminal(cmd, cwd, env):
    """Run with stdout and stderr on a pseudo-terminal, as from a shell."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave
    )
    os.close(slave)
    chunks = []
    # Read while it runs, or a full terminal buffer would block the script.
    # The output ends with EOF on macOS and EIO on Linux.
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(master)
    return b"".join(chunks).decode(errors="replace"), proc.wait(timeout=60)


def _pushed_refs(pushes):
    return [push.split()[-1] for push in pushes]


class TestVersionTag:
    def test_a_release_commit_is_pushed_with_its_version(self, build):
        # terraform apply passes no --tag; a release still gets its version
        _git(build.repo, "tag", "v9.9.9")
        _, pushes = build()
        assert _pushed_refs(pushes) == [f"{ECR_URI}:latest", f"{ECR_URI}:v9.9.9"]

    def test_a_prerelease_commit_gets_a_timestamp(self, build):
        _git(build.repo, "tag", "v9.9.9-rc1")
        sha = _git(build.repo, "rev-parse", "--short", "HEAD")
        _, pushes = build()
        latest, version = _pushed_refs(pushes)
        assert latest == f"{ECR_URI}:latest"
        assert re.fullmatch(rf"{re.escape(ECR_URI)}:\d{{8}}-\d{{6}}-{sha}", version)

    def test_an_explicit_tag_wins_over_the_release_tag(self, build):
        _git(build.repo, "tag", "v9.9.9")
        _, pushes = build("--tag", "v9.9.9-hotfix")
        assert _pushed_refs(pushes) == [
            f"{ECR_URI}:latest",
            f"{ECR_URI}:v9.9.9-hotfix",
        ]


class TestPushOutput:
    def test_without_a_terminal_each_push_is_one_line(self, build):
        # terraform apply's local-exec gives the script no terminal
        _git(build.repo, "tag", "v9.9.9")
        output, pushes = build()
        assert pushes == [
            f"docker push --quiet {ECR_URI}:latest",
            f"docker push --quiet {ECR_URI}:v9.9.9",
        ]
        assert f"Pushing {ECR_URI}:latest..." in output
        assert f"Pushing {ECR_URI}:v9.9.9..." in output

    def test_on_a_terminal_the_progress_bars_stay(self, build):
        _git(build.repo, "tag", "v9.9.9")
        _, pushes = build(tty=True)
        assert pushes == [
            f"docker push {ECR_URI}:latest",
            f"docker push {ECR_URI}:v9.9.9",
        ]

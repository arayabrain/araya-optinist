"""Which tags ecr_build_push.sh pushes, and how it pushes them.

Runs a copy of the real script inside a throwaway git repository, with stub
terraform / aws / docker / yarn on PATH. git is real, so the version tag comes
from the tags the test puts on HEAD; the stub aws answers whether a tag is
already in the repository; the stub docker answers a push with the lines docker
prints without a terminal. Both record every call.
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

# The repository holds the tags in $ECR_TAGS; $ECR_CHECK_FAILS makes the tag
# check fail. No GTM ID in SSM: the build goes ahead with analytics off
AWS_STUB = """
echo "aws $*" >> "$CALLS"
case "$1 $2" in
    "ecr get-login-password") echo pw ;;
    "ecr describe-images")
        if [ -n "${ECR_CHECK_FAILS:-}" ]; then
            echo "An error occurred (AccessDeniedException)" >&2
            exit 254
        fi
        for arg in "$@"; do
            case "$arg" in imageTag=*) tag="${arg#imageTag=}" ;; esac
        done
        case " ${ECR_TAGS:-} " in
            *" $tag "*) echo '{"imageDetails": [{}]}' ;;
            *)
                echo "An error occurred (ImageNotFoundException)" >&2
                exit 254 ;;
        esac ;;
    "ssm get-parameter")
        echo "An error occurred (ParameterNotFound)" >&2
        exit 254 ;;
esac
"""

# A push of three layers: one already in the registry, two uploaded. With
# DOCKER_STORE=containerd, as Docker's containerd image store prints it: no
# Preparing or Waiting lines, both "already exists" forms, and an upload line
# for the config and manifest as well as the layers
DOCKER_STUB = """
echo "docker $*" >> "$CALLS"
[ "$1" = push ] || exit 0
if [ -n "${DOCKER_PUSH_FAILS:-}" ]; then
    echo "push failed: connection refused" >&2
    exit 1
fi
echo "The push refers to repository [${2%:*}]"
if [ "${DOCKER_STORE:-}" = containerd ]; then
    echo "aaaaaaaaaaaa: Layer already exists"
    echo "bbbbbbbbbbbb: Already exists"
    echo "cccccccccccc: Pushed"
    echo "dddddddddddd: Pushed"
else
    for layer in aaaaaaaaaaaa bbbbbbbbbbbb cccccccccccc; do
        echo "$layer: Preparing"
    done
    echo "cccccccccccc: Waiting"
    echo "bbbbbbbbbbbb: Layer already exists"
    echo "aaaaaaaaaaaa: Pushed"
    echo "cccccccccccc: Pushed"
fi
echo "${2##*:}: digest: sha256:0123abcd size: 1234"
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
    _stub(bin_dir / "docker", DOCKER_STUB)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
    }

    def run(
        *args: str,
        tty: bool = False,
        push_fails: bool = False,
        ecr_tags: str = "",
        ecr_check_fails: bool = False,
        store: str = "overlay2",
    ):
        """Run the script; with push_fails, docker push fails and so must the script.

        ecr_tags: the tags already in the repository, space-separated.
        """
        cmd = ["bash", str(scripts / "ecr_build_push.sh"), "--yes", *args]
        run_env = {**env, "ECR_TAGS": ecr_tags, "DOCKER_STORE": store}
        if push_fails:
            run_env["DOCKER_PUSH_FAILS"] = "1"
        if ecr_check_fails:
            run_env["ECR_CHECK_FAILS"] = "1"
        if tty:
            output, returncode = _run_on_a_terminal(cmd, repo, run_env)
        else:
            result = subprocess.run(
                cmd, cwd=repo, env=run_env, capture_output=True, text=True, timeout=60
            )
            output, returncode = result.stdout + result.stderr, result.returncode
        assert (returncode != 0) == push_fails, output
        pushes = [
            c for c in calls.read_text().splitlines() if c.startswith("docker push")
        ]
        return output, pushes

    run.repo = repo
    run.calls = calls
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


def _assert_latest_and_a_timestamp(build, pushes):
    sha = _git(build.repo, "rev-parse", "--short", "HEAD")
    latest, version = _pushed_refs(pushes)
    assert latest == f"{ECR_URI}:latest"
    assert re.fullmatch(rf"{re.escape(ECR_URI)}:\d{{8}}-\d{{6}}-{sha}", version)


def _tag_checks(build):
    return [c for c in build.calls.read_text().splitlines() if "describe-images" in c]


class TestVersionTag:
    def test_a_release_commit_is_pushed_with_its_version(self, build):
        # terraform apply passes no --tag; a release still gets its version
        _git(build.repo, "tag", "v9.9.9")
        _, pushes = build()
        assert _pushed_refs(pushes) == [f"{ECR_URI}:latest", f"{ECR_URI}:v9.9.9"]
        assert len(_tag_checks(build)) == 1

    def test_a_prerelease_commit_gets_a_timestamp(self, build):
        _git(build.repo, "tag", "v9.9.9-rc1")
        _, pushes = build()
        _assert_latest_and_a_timestamp(build, pushes)
        assert _tag_checks(build) == []

    def test_an_explicit_tag_wins_over_the_release_tag(self, build):
        _git(build.repo, "tag", "v9.9.9")
        _, pushes = build("--tag", "v9.9.9-hotfix")
        assert _pushed_refs(pushes) == [
            f"{ECR_URI}:latest",
            f"{ECR_URI}:v9.9.9-hotfix",
        ]


class TestReleaseTagAlreadyPushed:
    """A rebuild of a release commit must not move the tag off the released image."""

    def test_a_rebuild_gets_a_timestamp_and_the_tag_stays(self, build):
        # A rollback apply or a retry from the release commit builds a new image
        _git(build.repo, "tag", "v9.9.9")
        output, pushes = build(ecr_tags="latest v9.9.9")
        _assert_latest_and_a_timestamp(build, pushes)
        assert "WARNING: v9.9.9 is already in repo and stays on that image" in output
        assert "--tag v9.9.9" in output

    def test_an_explicit_tag_moves_it_on_purpose(self, build):
        _git(build.repo, "tag", "v9.9.9")
        output, pushes = build("--tag", "v9.9.9", ecr_tags="latest v9.9.9")
        assert _pushed_refs(pushes) == [f"{ECR_URI}:latest", f"{ECR_URI}:v9.9.9"]
        assert _tag_checks(build) == []
        assert "already in" not in output

    def test_a_failed_check_falls_back_to_a_timestamp(self, build):
        # Not knowing must not move a released tag either
        _git(build.repo, "tag", "v9.9.9")
        output, pushes = build(ecr_check_fails=True)
        _assert_latest_and_a_timestamp(build, pushes)
        assert "WARNING: could not check whether v9.9.9 is already in repo" in output
        assert "AccessDeniedException" in output


class TestPushOutput:
    def test_without_a_terminal_the_push_prints_a_layer_count(self, build):
        # terraform apply's local-exec gives the script no terminal
        _git(build.repo, "tag", "v9.9.9")
        output, pushes = build()
        assert pushes == [
            f"docker push {ECR_URI}:latest",
            f"docker push {ECR_URI}:v9.9.9",
        ]
        lines = output.splitlines()
        start = lines.index(f"Pushing {ECR_URI}:latest...")
        # A line per uploaded layer; the one already in the registry is only counted
        assert lines[start + 1 : start + 4] == [
            "  latest: 2/3 layers",
            "  latest: 3/3 layers",
            "latest: digest: sha256:0123abcd size: 1234",
        ]
        for noise in ("Preparing", "Waiting", "Layer already exists", "push refers"):
            assert noise not in output

    def test_with_the_containerd_image_store_the_count_has_no_total(self, build):
        # No layer list up front, so only the uploads can be counted
        _git(build.repo, "tag", "v9.9.9")
        output, _ = build(store="containerd")
        lines = output.splitlines()
        start = lines.index(f"Pushing {ECR_URI}:latest...")
        assert lines[start + 1 : start + 4] == [
            "  latest: 1 pushed",
            "  latest: 2 pushed",
            "latest: digest: sha256:0123abcd size: 1234",
        ]
        assert "already exists" not in output.lower()
        assert "/0 layers" not in output

    def test_on_a_terminal_the_progress_bars_stay(self, build):
        _git(build.repo, "tag", "v9.9.9")
        output, pushes = build(tty=True)
        assert pushes == [
            f"docker push {ECR_URI}:latest",
            f"docker push {ECR_URI}:v9.9.9",
        ]
        # docker's own output reaches the terminal untouched
        assert "aaaaaaaaaaaa: Preparing" in output
        assert "latest: 2/3 layers" not in output

    def test_a_failed_push_stops_the_build(self, build):
        # The pipe into the count must not hide the push's exit status
        output, pushes = build(push_fails=True)
        assert _pushed_refs(pushes) == [f"{ECR_URI}:latest"]
        assert "push failed: connection refused" in output
        assert "PUSH COMPLETE" not in output

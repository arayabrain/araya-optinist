"""Where ecr_build_push.sh takes the frontend GTM container ID from.

Runs a copy of the real script with stub git / terraform / aws / docker / yarn
on PATH and reads the frontend/.env.production it writes.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = PROJECT_ROOT / "infrastructure" / "scripts"

AWS_STUB = """
echo "aws $*" >> "$CALLS"
case "$1 $2" in
    "ecr get-login-password") echo pw ;;
    "ssm get-parameter")
        case "$SSM_MODE" in
            value) echo "$SSM_VALUE"; exit 0 ;;
            missing) code=ParameterNotFound ;;
            *) code=AccessDeniedException ;;
        esac
        echo "An error occurred ($code) when calling the GetParameter operation" >&2
        exit 254 ;;
esac
"""

TERRAFORM_STUB = """
case "$4" in
    environment) echo development ;;
    ecr_repository_url) echo 123.dkr.ecr.ap-northeast-1.amazonaws.com/repo ;;
    domain_port) echo 443 ;;
    *) echo x ;;
esac
"""


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def build(tmp_path):
    root = tmp_path / "repo"
    scripts = root / "infrastructure" / "scripts"
    scripts.mkdir(parents=True)
    (root / "infrastructure" / "terraform").mkdir()
    (root / "frontend").mkdir()
    for name in ("ecr_build_push.sh", "git_ref_info.sh"):
        shutil.copy(SCRIPTS / name, scripts / name)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    _stub(bin_dir / "aws", AWS_STUB)
    _stub(bin_dir / "terraform", TERRAFORM_STUB)
    for name in ("git", "yarn"):
        _stub(bin_dir / name, "exit 0")
    _stub(bin_dir / "docker", f'echo "docker $*" >> "{calls}"')

    def run(ssm_mode, ssm_value="", env_deploy=None, shell_env=None):
        if env_deploy is not None:
            (root / "infrastructure" / ".env.deploy").write_text(env_deploy)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "SSM_MODE": ssm_mode,
            "SSM_VALUE": ssm_value,
            **(shell_env or {}),
        }
        result = subprocess.run(
            ["bash", str(scripts / "ecr_build_push.sh"), "--yes"],
            capture_output=True,
            text=True,
            env=env,
        )
        dotenv = root / "frontend" / ".env.production"
        gtm = None
        if dotenv.exists():
            gtm = next(
                line.split("=", 1)[1]
                for line in dotenv.read_text().splitlines()
                if line.startswith("REACT_APP_GTM_ID=")
            )
        return result, gtm, calls.read_text()

    return run


def test_env_deploy_override_wins_and_ssm_is_not_called(build):
    result, gtm, calls = build(
        "value", "GTM-FROMSSM", env_deploy="REACT_APP_GTM_ID_DEVELOPMENT=GTM-LOCAL1\n"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert gtm == "GTM-LOCAL1"
    assert "from .env.deploy" in result.stdout
    assert "ssm get-parameter" not in calls


def test_ssm_supplies_the_id_when_no_override(build):
    result, gtm, calls = build("value", "GTM-575Z6HSR")
    assert result.returncode == 0, result.stdout + result.stderr
    assert gtm == "GTM-575Z6HSR"
    assert "from SSM /development/optinist/gtm-id" in result.stdout
    assert "--name /development/optinist/gtm-id" in calls


def test_shell_export_does_not_override_ssm(build):
    result, gtm, calls = build(
        "value",
        "GTM-575Z6HSR",
        shell_env={"REACT_APP_GTM_ID_DEVELOPMENT": "GTM-SHELL1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert gtm == "GTM-575Z6HSR"
    assert "from SSM /development/optinist/gtm-id" in result.stdout


def test_missing_parameter_builds_with_analytics_off(build):
    result, gtm, _ = build("missing")
    assert result.returncode == 0, result.stdout + result.stderr
    assert gtm == ""
    assert "analytics disabled" in result.stdout


def test_other_ssm_error_fails_before_building(build):
    result, gtm, calls = build("denied")
    assert result.returncode != 0
    assert "AccessDeniedException" in result.stdout
    assert gtm is None
    assert "docker build" not in calls


def test_malformed_ssm_value_fails(build):
    result, gtm, _ = build("value", "not-a-gtm-id")
    assert result.returncode != 0
    assert "malformed GTM ID from SSM" in result.stdout
    assert gtm is None

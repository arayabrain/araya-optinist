"""maintenance_mode.sh against a stub aws: it only ever deletes its own rule."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = PROJECT_ROOT / "infrastructure" / "scripts" / "maintenance_mode.sh"

STUB_AWS = """#!/usr/bin/env bash
echo "$*" >> "$AWS_LOG"
case "$2" in
  describe-load-balancers) echo lb-arn ;;
  describe-listeners) echo listener-arn ;;
  describe-rules)
    case "$*" in
      *RuleArn*) echo "$STUB_RULE_ARN" ;;
      *) echo "$STUB_RULE_BODY" ;;
    esac ;;
esac
"""

NO_RULE = ("None", "None")
OWN_RULE = ("rule-arn", "<html><!-- optinist-maintenance --></html>")
OTHER_FIXED_RULE = ("rule-arn", "<html>something else</html>")
FORWARD_RULE = ("rule-arn", "None")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("python3") is None,
    reason="bash and python3 are required to run the script",
)


def run(tmp_path: Path, rule: tuple[str, str], action: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    aws = bin_dir / "aws"
    aws.write_text(STUB_AWS)
    aws.chmod(0o755)
    log = tmp_path / "aws.log"
    log.write_text("")
    result = subprocess.run(
        ["bash", str(SCRIPT), "development", action],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "AWS_REGION": "ap-northeast-1",
            "AWS_LOG": str(log),
            "STUB_RULE_ARN": rule[0],
            "STUB_RULE_BODY": rule[1],
        },
    )
    calls = log.read_text().splitlines()
    return result, [c for c in calls if c.startswith(("elbv2 create", "elbv2 delete"))]


def test_on_creates_the_rule_when_none_exists(tmp_path):
    result, writes = run(tmp_path, NO_RULE, "on")
    assert result.returncode == 0, result.stderr
    assert len(writes) == 1
    assert writes[0].startswith("elbv2 create-rule")
    assert "--priority 10" in writes[0]
    assert "optinist-maintenance" in writes[0]


def test_off_deletes_its_own_rule(tmp_path):
    result, writes = run(tmp_path, OWN_RULE, "off")
    assert result.returncode == 0, result.stderr
    assert writes == ["elbv2 delete-rule --region ap-northeast-1 --rule-arn rule-arn"]


@pytest.mark.parametrize(
    "rule, action, stdout",
    [
        (NO_RULE, "off", "already off"),
        (NO_RULE, "status", "off"),
        (OWN_RULE, "on", "already on"),
        (OWN_RULE, "status", "on"),
    ],
)
def test_no_write_when_already_in_that_state(tmp_path, rule, action, stdout):
    result, writes = run(tmp_path, rule, action)
    assert result.returncode == 0, result.stderr
    assert stdout in result.stdout
    assert writes == []


@pytest.mark.parametrize("rule", [OTHER_FIXED_RULE, FORWARD_RULE])
@pytest.mark.parametrize("action", ["on", "off", "status"])
def test_refuses_a_rule_it_did_not_create(tmp_path, rule, action):
    result, writes = run(tmp_path, rule, action)
    assert result.returncode == 1
    assert "not the maintenance page" in result.stderr
    assert writes == []

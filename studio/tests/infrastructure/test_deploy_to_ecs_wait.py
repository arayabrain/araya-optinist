"""null_resource.deploy_to_ecs fails the apply when services never stabilize.

Runs the provisioner's real command with ``aws``, ``date`` and ``sleep``
stubbed, so a rollout that outlasts the deadline is simulated in milliseconds.
"""

import os
import re
import subprocess
import textwrap
from pathlib import Path

from terraform_source import TERRAFORM_DIR, read, resource_block

AWS_STUB = """#!/bin/bash
echo "$*" >> "$STUB_DIR/aws.log"
case "$1 $2" in
  "ecs list-services") echo "arn:svc/svc-a arn:svc/svc-b" ;;
  "ecs wait")
    n=$(( $(cat "$STUB_DIR/waits" 2>/dev/null || echo 0) + 1 ))
    echo $n > "$STUB_DIR/waits"
    [ "$n" -ge "$STABLE_ON_WAIT" ] || exit 255 ;;
  "ecs describe-services") printf 'svc-b\\tunable to place a task\\n' ;;
esac
"""

# Each `date +%s` reads 20 minutes later than the previous one
DATE_STUB = """#!/bin/bash
t=$(( $(cat "$STUB_DIR/now" 2>/dev/null || echo 0) + 1200 ))
echo $t > "$STUB_DIR/now"
echo $t
"""


def _command() -> str:
    block = resource_block(
        read(TERRAFORM_DIR / "deployment.tf"), "null_resource", "deploy_to_ecs"
    )
    heredoc = re.search(r"<<-EOT\n(.*?)\n\s*EOT", block, re.DOTALL).group(1)
    script = textwrap.dedent(heredoc).replace("$${", "\0")
    return re.sub(r"\$\{[^}]*\}", "tfvalue", script).replace("\0", "${")


def _run(tmp_path: Path, stable_on_wait: int) -> tuple:
    for name, body in (("aws", AWS_STUB), ("date", DATE_STUB), ("sleep", "")):
        stub = tmp_path / name
        stub.write_text(body or "#!/bin/sh\n")
        stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
        "STABLE_ON_WAIT": str(stable_on_wait),
    }
    result = subprocess.run(
        ["bash", "-c", _command()], env=env, capture_output=True, text=True
    )
    return result, (tmp_path / "aws.log").read_text()


def test_a_slow_rollout_is_waited_out(tmp_path):
    result, calls = _run(tmp_path, stable_on_wait=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.count("ecs wait services-stable") == 2
    assert "ssm start-associations-once" in calls


def test_a_rollout_past_the_deadline_fails_and_names_the_service(tmp_path):
    result, calls = _run(tmp_path, stable_on_wait=99)
    assert result.returncode == 1
    assert "not stable after 60 minutes" in result.stdout
    assert "svc-b\tunable to place a task" in result.stdout
    assert "DEPLOYMENT COMPLETE" not in result.stdout
    assert "ssm start-associations-once" not in calls

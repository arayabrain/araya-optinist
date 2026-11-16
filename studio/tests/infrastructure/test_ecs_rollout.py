"""The ECS rollout shared by terraform apply and ``ecr_build_push.sh --deploy``.

``ecs_rollout.sh`` runs with ``aws``, ``date`` and ``sleep`` stubbed, so a
rollout that outlasts its deadline is simulated in milliseconds. The two
callers are run with the script itself stubbed, to check what they pass it.
"""

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest
from terraform_source import TERRAFORM_DIR, read, resource_block

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = PROJECT_ROOT / "infrastructure" / "scripts"
ROLLOUT = SCRIPTS / "ecs_rollout.sh"
ECR_BUILD_PUSH = SCRIPTS / "ecr_build_push.sh"

GROUPS = ["--asg", "free-asg", "--asg", "public-asg"]
HYGIENE = ["--hygiene-association", "assoc-1"]
TARGET_GROUP_ARN = "arn:aws:elasticloadbalancing:r:1:targetgroup/{}/0123"
FREE_TG = TARGET_GROUP_ARN.format("free-tg")
PUBLIC_TG = TARGET_GROUP_ARN.format("public-tg")

# Every call is logged. The clock moves only on sleep and on `ecs wait`, which
# takes its full 10 minutes. describe-services answers the pre-force check
# with a rolling service for the first ROLLING_POLLS calls, and the target
# group lookup with two groups. describe-target-health fails its first
# FAIL_TARGET_CALLS calls and names a target not yet healthy up to call
# UNHEALTHY_TARGET_CALLS.
AWS_STUB = """#!/bin/bash
echo "$*" >> "$STUB_DIR/aws.log"
count() {
  n=$(( $(cat "$STUB_DIR/$1" 2>/dev/null || echo 0) + 1 ))
  echo $n > "$STUB_DIR/$1"
  echo $n
}
case "$1 $2" in
  "ecs list-services")
    [ -n "${NO_SERVICES:-}" ] || echo "arn:svc/svc-a arn:svc/svc-b" ;;
  "ecs describe-services")
    case "$*" in
      *runningCount*) printf 'svc-b\\tunable to place a task\\n' ;;
      *targetGroupArn*)
        [ -z "${FAIL_TG_LOOKUP:-}" ] || exit 255
        printf '%s\\t%s\\n' "$FREE_TG" "$PUBLIC_TG" ;;
      *) [ "$(count describes)" -gt "${ROLLING_POLLS:-0}" ] || echo "svc-a" ;;
    esac ;;
  "elbv2 describe-target-health")
    n=$(count targets)
    [ "$n" -gt "${FAIL_TARGET_CALLS:-0}" ] || exit 255
    [ "$n" -gt "${UNHEALTHY_TARGET_CALLS:-0}" ] || echo "i-1" ;;
  "ecs update-service") [ -z "${FAIL_UPDATE:-}" ] || exit 255 ;;
  "ecs wait")
    n=$(count waits)
    echo $(( $(cat "$STUB_DIR/now") + 600 )) > "$STUB_DIR/now"
    [ -z "${TERM_ON_WAIT:-}" ] || kill -TERM "$PPID"
    [ "$n" -ge "$STABLE_ON_WAIT" ] || exit 255 ;;
  "autoscaling describe-auto-scaling-groups")
    case "$*" in
      *"${UNREADABLE_ASG:-unset}"*) exit 255 ;;
      *"${ALREADY_SUSPENDED_ASG:-unset}"*) echo 1 ;;
      *) echo 0 ;;
    esac ;;
  "autoscaling resume-processes") [ -z "${FAIL_RESUME:-}" ] || exit 255 ;;
esac
"""
DATE_STUB = """#!/bin/bash
cat "$STUB_DIR/now"
"""
SLEEP_STUB = """#!/bin/bash
echo $(( $(cat "$STUB_DIR/now") + $1 )) > "$STUB_DIR/now"
"""


def _install_stubs(tmp_path: Path, stubs: dict) -> dict:
    for name, body in stubs.items():
        stub = tmp_path / name
        stub.write_text(body)
        stub.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
    }


def _rollout(tmp_path: Path, *args: str, stable_on_wait: int = 1, **extra_env):
    (tmp_path / "now").write_text("0\n")
    (tmp_path / "aws.log").write_text("")
    env = _install_stubs(
        tmp_path, {"aws": AWS_STUB, "date": DATE_STUB, "sleep": SLEEP_STUB}
    )
    env.update(
        {
            "STABLE_ON_WAIT": str(stable_on_wait),
            "FREE_TG": FREE_TG,
            "PUBLIC_TG": PUBLIC_TG,
            **extra_env,
        }
    )
    result = subprocess.run(
        ["bash", str(ROLLOUT), "--cluster", "c", "--region", "r", *args],
        env=env,
        capture_output=True,
        text=True,
    )
    calls = (tmp_path / "aws.log").read_text().splitlines()
    now = int((tmp_path / "now").read_text())
    return result, calls, now


def _indexes(calls: list, fragment: str) -> list:
    return [i for i, call in enumerate(calls) if fragment in call]


class TestStableWait:
    def test_a_slow_rollout_is_waited_out(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *HYGIENE, stable_on_wait=2)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(_indexes(calls, "ecs wait services-stable")) == 2
        hygiene = _indexes(
            calls, "ssm start-associations-once --association-ids assoc-1"
        )
        assert hygiene and hygiene[0] > _indexes(calls, "ecs wait")[-1]

    def test_a_rollout_past_the_deadline_fails_and_names_the_service(self, tmp_path):
        result, calls, now = _rollout(tmp_path, *HYGIENE, stable_on_wait=99)
        assert result.returncode == 1
        assert len(_indexes(calls, "ecs wait services-stable")) == 6
        assert 3600 <= now <= 3700
        assert "not stable within about an hour" in result.stdout
        assert "svc-b\tunable to place a task" in result.stdout
        assert not _indexes(calls, "ssm start-associations-once")

    def test_the_last_wait_that_fits_the_deadline_can_still_succeed(self, tmp_path):
        result, _, now = _rollout(tmp_path, stable_on_wait=6)
        assert result.returncode == 0, result.stdout + result.stderr
        assert now <= 3700

    def test_an_empty_cluster_fails_before_any_change(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *GROUPS, NO_SERVICES="1")
        assert result.returncode == 1
        assert "no ECS services found" in result.stdout
        for fragment in ("update-service", "ecs wait", "suspend-processes"):
            assert not _indexes(calls, fragment), fragment

    def test_a_failed_force_stops_before_waiting(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, FAIL_UPDATE="1")
        assert result.returncode == 1
        assert "force-new-deployment failed on svc-a" in result.stdout
        assert not _indexes(calls, "ecs wait")


class TestRolloutInProgress:
    def test_is_left_to_finish_before_the_force(self, tmp_path):
        result, calls, now = _rollout(tmp_path, ROLLING_POLLS="3")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "A rollout is in progress on: svc-a" in result.stdout
        checks = _indexes(calls, "ecs describe-services")
        assert len(checks) == 4
        assert _indexes(calls, "ecs update-service")[0] > checks[-1]
        # Three 30 s polls, then the one 10-minute wait
        assert now == 3 * 30 + 600

    def test_one_that_never_settles_is_forced_after_the_bound(self, tmp_path):
        result, calls, now = _rollout(tmp_path, ROLLING_POLLS="999")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "forcing the new deployment anyway" in result.stdout
        assert len(_indexes(calls, "ecs update-service")) == 2
        # 30 minutes of polling, then the one 10-minute wait
        assert now == 1800 + 600

    def test_no_rollout_in_progress_forces_at_once(self, tmp_path):
        result, calls, _ = _rollout(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(_indexes(calls, "ecs describe-services")) == 1
        assert "in progress" not in result.stdout


def _assert_off_then_on(calls: list, asg: str) -> int:
    """HealthCheck on ``asg`` is suspended once before the force and resumed
    once after every wait and target health read; returns the resume's index."""
    suspend = _indexes(
        calls,
        f"suspend-processes --auto-scaling-group-name {asg} "
        "--scaling-processes HealthCheck",
    )
    resume = _indexes(
        calls,
        f"resume-processes --auto-scaling-group-name {asg} "
        "--scaling-processes HealthCheck",
    )
    assert len(suspend) == 1 and len(resume) == 1, calls
    assert suspend[0] < _indexes(calls, "ecs update-service")[0]
    for fragment in ("ecs wait", "elbv2 describe-target-health"):
        before = _indexes(calls, fragment)
        assert not before or resume[0] > before[-1], fragment
    return resume[0]


class TestHostHealthChecks:
    """The groups' HealthCheck process is off from before the force until the
    restarted targets pass their checks, or until the script exits, whichever
    way it exits."""

    def _assert_off_then_on(self, calls: list, asg: str) -> None:
        _assert_off_then_on(calls, asg)

    def test_off_for_exactly_the_rollout(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *GROUPS)
        assert result.returncode == 0, result.stdout + result.stderr
        for asg in ("free-asg", "public-asg"):
            self._assert_off_then_on(calls, asg)

    def test_back_on_when_the_rollout_fails(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *GROUPS, stable_on_wait=99)
        assert result.returncode == 1
        for asg in ("free-asg", "public-asg"):
            self._assert_off_then_on(calls, asg)

    def test_back_on_when_the_rollout_is_interrupted(self, tmp_path):
        result, calls, _ = _rollout(
            tmp_path, *GROUPS, stable_on_wait=99, TERM_ON_WAIT="1"
        )
        assert result.returncode == 143
        assert len(_indexes(calls, "ecs wait")) == 1
        for asg in ("free-asg", "public-asg"):
            self._assert_off_then_on(calls, asg)

    def test_back_on_when_the_force_fails(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *GROUPS, FAIL_UPDATE="1")
        assert result.returncode == 1
        for asg in ("free-asg", "public-asg"):
            self._assert_off_then_on(calls, asg)

    def test_a_group_suspended_beforehand_is_left_as_found(self, tmp_path):
        result, calls, _ = _rollout(
            tmp_path, *GROUPS, ALREADY_SUSPENDED_ASG="public-asg"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "public-asg: HealthCheck was already suspended" in result.stdout
        self._assert_off_then_on(calls, "free-asg")
        assert not _indexes(calls, "processes --auto-scaling-group-name public-asg")

    def test_an_unreadable_group_is_skipped_with_a_warning(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *GROUPS, UNREADABLE_ASG="public-asg")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "could not read public-asg" in result.stdout
        self._assert_off_then_on(calls, "free-asg")
        assert not _indexes(calls, "processes --auto-scaling-group-name public-asg")

    def test_a_failed_resume_prints_the_command_to_run(self, tmp_path):
        result, _, _ = _rollout(tmp_path, *GROUPS, FAIL_RESUME="1")
        assert result.returncode == 0
        assert (
            "aws autoscaling resume-processes --auto-scaling-group-name free-asg "
            "--scaling-processes HealthCheck --region r"
        ) in result.stdout

    def test_no_groups_means_no_group_calls(self, tmp_path):
        result, calls, _ = _rollout(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not _indexes(calls, "autoscaling")
        assert not _indexes(calls, "elbv2")


class TestTargetHealthWait:
    """services-stable ignores target health, so HealthCheck stays off after it
    until the load-balanced services' targets are healthy, for at most the
    services' 900 s grace."""

    def test_health_checks_stay_off_until_the_targets_pass(self, tmp_path):
        result, calls, now = _rollout(
            tmp_path, *GROUPS, *HYGIENE, UNHEALTHY_TARGET_CALLS="5"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "All targets are healthy." in result.stdout
        reads = _indexes(calls, "elbv2 describe-target-health")
        # Two groups a round: rounds 1-3 still see an unready target
        assert len(reads) == 8
        for tg in (FREE_TG, PUBLIC_TG):
            assert _indexes(calls, f"--target-group-arn {tg}")
        resumes = [
            _assert_off_then_on(calls, asg) for asg in ("free-asg", "public-asg")
        ]
        hygiene = _indexes(calls, "ssm start-associations-once")
        assert hygiene and hygiene[0] > max(resumes)
        # The one 10-minute wait, then three 15 s polls
        assert now == 600 + 3 * 15

    def test_targets_that_never_pass_are_left_to_the_group_after_the_bound(
        self, tmp_path
    ):
        result, calls, now = _rollout(tmp_path, *GROUPS, UNHEALTHY_TARGET_CALLS="999")
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            "targets in free-tg public-tg still not healthy after 15 minutes"
            in result.stdout
        )
        for asg in ("free-asg", "public-asg"):
            _assert_off_then_on(calls, asg)
        assert now == 600 + 900

    def test_a_failed_read_counts_as_not_yet_healthy(self, tmp_path):
        result, calls, now = _rollout(tmp_path, *GROUPS, FAIL_TARGET_CALLS="2")
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(_indexes(calls, "elbv2 describe-target-health")) == 4
        assert now == 600 + 15

    def test_an_unreadable_target_group_list_resumes_at_once(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *GROUPS, FAIL_TG_LOOKUP="1")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "could not read the services' target groups" in result.stdout
        assert not _indexes(calls, "elbv2")
        for asg in ("free-asg", "public-asg"):
            _assert_off_then_on(calls, asg)

    def test_no_wait_when_no_group_was_suspended_here(self, tmp_path):
        result, calls, _ = _rollout(
            tmp_path, "--asg", "public-asg", ALREADY_SUSPENDED_ASG="public-asg"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not _indexes(calls, "elbv2")


# The callers run with ecs_rollout.sh replaced by a stub that logs its
# arguments and exits with $ROLLOUT_EXIT
ROLLOUT_STUB = """#!/bin/bash
echo "$*" > "$STUB_DIR/rollout.args"
exit "${ROLLOUT_EXIT:-0}"
"""


class TestTerraformDeploy:
    def _block(self) -> str:
        return resource_block(
            read(TERRAFORM_DIR / "deployment.tf"), "null_resource", "deploy_to_ecs"
        )

    def _run(self, tmp_path: Path, rollout_exit: int):
        heredoc = re.search(r"<<-EOT\n(.*?)\n\s*EOT", self._block(), re.DOTALL)
        script = textwrap.dedent(heredoc.group(1)).replace("$${", "\0")
        script = re.sub(r"\$\{[^}]*\}", "tfvalue", script).replace("\0", "${")
        # local-exec runs from the terraform directory
        (tmp_path / "scripts").mkdir()
        (tmp_path / "terraform").mkdir()
        (tmp_path / "scripts" / "ecs_rollout.sh").write_text(ROLLOUT_STUB)
        env = {
            **os.environ,
            "STUB_DIR": str(tmp_path),
            "ROLLOUT_EXIT": str(rollout_exit),
        }
        return subprocess.run(
            ["sh", "-c", script],
            cwd=tmp_path / "terraform",
            env=env,
            capture_output=True,
            text=True,
        )

    def test_passes_both_tier_groups_and_the_hygiene_association(self):
        block = self._block()
        assert "bash ../scripts/ecs_rollout.sh" in block
        assert "--asg ${aws_autoscaling_group.main.name}" in block
        assert "--asg ${aws_autoscaling_group.public.name}" in block
        assert (
            "--hygiene-association "
            "${aws_ssm_association.docker_hygiene.association_id}"
        ) in block

    def test_a_stable_rollout_completes_the_deploy(self, tmp_path):
        result = self._run(tmp_path, rollout_exit=0)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "rollout.args").read_text().split() == [
            "--cluster", "tfvalue", "--region", "tfvalue",
            "--asg", "tfvalue", "--asg", "tfvalue",
            "--hygiene-association", "tfvalue",
        ]  # fmt: skip
        assert "DEPLOYMENT COMPLETE" in result.stdout

    def test_a_failed_rollout_fails_the_apply(self, tmp_path):
        result = self._run(tmp_path, rollout_exit=1)
        assert result.returncode == 1
        assert "deploy_to_ecs is marked tainted" in result.stdout
        assert "DEPLOYMENT COMPLETE" not in result.stdout


# terraform -chdir=<dir> output -raw <name>: prints $TFOUT_<name>, or fails
# like a missing output
TERRAFORM_STUB = """#!/bin/bash
var="TFOUT_$4"
[ -n "${!var:-}" ] || { echo "Error: Output \\"$4\\" not found" >&2; exit 1; }
printf '%s' "${!var}"
"""


class TestScriptDeploy:
    """`ecr_build_push.sh --deploy` hands the same rollout what Terraform's
    outputs name, and still deploys from a state that lacks some of them."""

    def _run(self, tmp_path: Path, outputs: dict, rollout_exit: int = 0):
        source = ECR_BUILD_PUSH.read_text()
        block = re.search(
            r'^if \[ "\$DEPLOY" = true \]; then$.*?^fi$',
            source,
            re.DOTALL | re.MULTILINE,
        )
        assert block, "DEPLOY block not found"
        (tmp_path / "ecs_rollout.sh").write_text(ROLLOUT_STUB)
        env = _install_stubs(tmp_path, {"terraform": TERRAFORM_STUB})
        env.update({f"TFOUT_{k}": v for k, v in outputs.items()})
        env["ROLLOUT_EXIT"] = str(rollout_exit)
        prelude = (
            f'set -e\nDEPLOY=true\nSCRIPT_DIR="{tmp_path}"\n'
            "TERRAFORM_DIR=tf\nREGION=r\n"
        )
        result = subprocess.run(
            ["bash", "-c", prelude + block.group() + '\necho "SCRIPT END"\n'],
            env=env,
            capture_output=True,
            text=True,
        )
        args_file = tmp_path / "rollout.args"
        args = args_file.read_text().split() if args_file.exists() else None
        return result, args

    def test_passes_every_output_it_finds(self, tmp_path):
        result, args = self._run(
            tmp_path,
            {
                "ecs_cluster_name": "c",
                "asg_name": "free-asg",
                "public_asg_name": "public-asg",
                "docker_hygiene_association_id": "assoc-1",
            },
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert args == ["--cluster", "c", "--region", "r", *GROUPS, *HYGIENE]

    def test_a_missing_output_drops_only_its_argument(self, tmp_path):
        result, args = self._run(
            tmp_path, {"ecs_cluster_name": "c", "asg_name": "free-asg"}
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert args == ["--cluster", "c", "--region", "r", "--asg", "free-asg"]
        assert "no 'public_asg_name' output" in result.stdout

    def test_a_failed_rollout_fails_the_script(self, tmp_path):
        result, _ = self._run(tmp_path, {"ecs_cluster_name": "c"}, rollout_exit=1)
        assert result.returncode == 1
        assert "SCRIPT END" not in result.stdout

    @pytest.mark.parametrize(
        "output, resource",
        [
            ("asg_name", "aws_autoscaling_group.main.name"),
            ("public_asg_name", "aws_autoscaling_group.public.name"),
        ],
    )
    def test_the_groups_it_reads_are_terraform_outputs(self, output, resource):
        sources = "\n".join(read(path) for path in sorted(TERRAFORM_DIR.glob("*.tf")))
        assert re.search(
            r'^output "%s" \{[^}]*value\s*=\s*%s\s*$' % (output, re.escape(resource)),
            sources,
            re.MULTILINE,
        )

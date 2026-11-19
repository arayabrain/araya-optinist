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
# takes its full 10 minutes (no time at all with FAIL_WAIT_AT_ONCE; with
# INT_ON_WAIT it sends Ctrl-C to the whole group and exits 130, as the AWS CLI
# does). describe-services fails the pre-force check FAIL_DESCRIBE_CALLS times,
# then answers it with a rolling service for ROLLING_POLLS calls, and answers
# the target group lookup with two groups. describe-target-health fails its
# first FAIL_TARGET_CALLS calls and names a target not yet healthy up to call
# UNHEALTHY_TARGET_CALLS. Its healthy / not-ready count query answers one of
# each, no healthy target for the group named in NO_HEALTHY_TG, and no
# target at all for the one in NO_TARGET_TG. ALREADY_SUSPENDED_ASG reads as
# suspended for its first SUSPENDED_READS reads (default: always).
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
      *)
        n=$(count describes)
        failing=${FAIL_DESCRIBE_CALLS:-0}
        [ "$n" -gt "$failing" ] || exit 255
        [ "$n" -gt $(( failing + ${ROLLING_POLLS:-0} )) ] || echo "svc-a" ;;
    esac ;;
  "elbv2 describe-target-health")
    case "$*" in
      *"length("*)
        [ -z "${FAIL_COUNT_READ:-}" ] || exit 255
        case "$*" in
          *"${NO_HEALTHY_TG:-unset}"*) printf '0\\t1\\n' ;;
          *"${NO_TARGET_TG:-unset}"*) printf '0\\t0\\n' ;;
          *) printf '1\\t1\\n' ;;
        esac ;;
      *)
        n=$(count targets)
        [ "$n" -gt "${FAIL_TARGET_CALLS:-0}" ] || exit 255
        [ "$n" -gt "${UNHEALTHY_TARGET_CALLS:-0}" ] || echo "i-1" ;;
    esac ;;
  "ecs update-service") [ -z "${FAIL_UPDATE:-}" ] || exit 255 ;;
  "ecs wait")
    n=$(count waits)
    [ -z "${FAIL_WAIT_AT_ONCE:-}" ] || exit 255
    if [ -n "${INT_ON_WAIT:-}" ]; then
      trap 'exit 130' INT
      kill -INT 0
    fi
    echo $(( $(cat "$STUB_DIR/now") + 600 )) > "$STUB_DIR/now"
    [ -z "${TERM_ON_WAIT:-}" ] || kill -TERM "$PPID"
    [ "$n" -ge "$STABLE_ON_WAIT" ] || exit 255 ;;
  "autoscaling describe-auto-scaling-groups")
    case "$*" in
      *"${UNREADABLE_ASG:-unset}"*) exit 255 ;;
      *"${ALREADY_SUSPENDED_ASG:-unset}"*)
        [ "$(count asg_reads)" -gt "${SUSPENDED_READS:-999999}" ] && echo 0 || echo 1 ;;
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
    # Its own session, so a stub's Ctrl-C reaches the script's group only; the
    # timeout turns a hang into a failure
    result = subprocess.run(
        ["bash", str(ROLLOUT), "--cluster", "c", "--region", "r", *args],
        env=env,
        capture_output=True,
        text=True,
        start_new_session=True,
        timeout=60,
    )
    calls = (tmp_path / "aws.log").read_text().splitlines()
    now = int((tmp_path / "now").read_text())
    return result, calls, now


def _indexes(calls: list, fragment: str) -> list:
    return [i for i, call in enumerate(calls) if fragment in call]


# The pre-force check's query; the target group lookup is a describe-services too
ROLLING_CHECK = "rolloutState"


class TestArguments:
    @pytest.mark.parametrize(
        "args",
        [["--asg"], ["--region"], ["--hygiene-association"], ["--asg", ""]],
    )
    def test_an_option_without_a_value_fails_at_once(self, tmp_path, args):
        result, calls, _ = _rollout(tmp_path, *args)
        assert result.returncode == 1
        assert f"ERROR: {args[0]} needs a value" in result.stdout
        assert not calls


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

    def test_a_wait_that_fails_at_once_is_not_retried(self, tmp_path):
        # Access denied or expired credentials: no 10-minute timeout to wait out
        result, calls, now = _rollout(
            tmp_path, *GROUPS, *HYGIENE, FAIL_WAIT_AT_ONCE="1"
        )
        assert result.returncode == 1
        assert "the wait for the services failed at once" in result.stdout
        assert len(_indexes(calls, "ecs wait")) == 1
        assert now == 0
        for asg in ("free-asg", "public-asg"):
            _assert_off_then_on(calls, asg)
        assert not _indexes(calls, "ssm start-associations-once")


def _rolling_query() -> str:
    match = re.search(r"^ROLLING_QUERY='(.*)'$", ROLLOUT.read_text(), re.MULTILINE)
    assert match, "ROLLING_QUERY not found"
    return match.group(1)


class TestRolloutInProgress:
    @pytest.mark.parametrize(
        "deployments, rolling",
        [
            # Old tasks stopped, the new one still starting: only the PRIMARY left
            ([("PRIMARY", "IN_PROGRESS")], True),
            ([("PRIMARY", "IN_PROGRESS"), ("ACTIVE", "COMPLETED")], True),
            ([("PRIMARY", "COMPLETED"), ("ACTIVE", "COMPLETED")], True),
            ([("PRIMARY", "COMPLETED")], False),
        ],
    )
    # The backtick string form, which every query in the script uses and the
    # AWS CLI accepts
    @pytest.mark.filterwarnings("ignore:deprecated string literal syntax")
    def test_rolling_means_two_deployments_or_one_not_yet_steady(
        self, deployments, rolling
    ):
        jmespath = pytest.importorskip("jmespath")
        services = {
            "services": [
                {
                    "serviceName": "svc-a",
                    "deployments": [
                        {"status": status, "rolloutState": state}
                        for status, state in deployments
                    ],
                }
            ]
        }
        expected = ["svc-a"] if rolling else []
        assert jmespath.search(_rolling_query(), services) == expected

    def test_is_left_to_finish_before_the_force(self, tmp_path):
        result, calls, now = _rollout(tmp_path, ROLLING_POLLS="3")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "A rollout is in progress on: svc-a" in result.stdout
        checks = _indexes(calls, ROLLING_CHECK)
        assert len(checks) == 4
        assert _indexes(calls, "ecs update-service")[0] > checks[-1]
        # Three 30 s polls, then the one 10-minute wait
        assert now == 3 * 30 + 600

    def test_a_failed_read_is_retried(self, tmp_path):
        result, calls, now = _rollout(tmp_path, FAIL_DESCRIBE_CALLS="2")
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.count("Could not read the services' deployments") == 2
        checks = _indexes(calls, ROLLING_CHECK)
        assert len(checks) == 3
        assert _indexes(calls, "ecs update-service")[0] > checks[-1]
        assert now == 2 * 30 + 600

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
        assert len(_indexes(calls, ROLLING_CHECK)) == 1
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

    def test_back_on_when_ctrl_c_stops_the_wait(self, tmp_path):
        result, calls, _ = _rollout(
            tmp_path, *GROUPS, stable_on_wait=99, INT_ON_WAIT="1"
        )
        assert result.returncode == 130, result.stdout + result.stderr
        assert len(_indexes(calls, "ecs wait")) == 1
        for asg in ("free-asg", "public-asg"):
            self._assert_off_then_on(calls, asg)

    def test_back_on_when_the_force_fails(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *GROUPS, FAIL_UPDATE="1")
        assert result.returncode == 1
        for asg in ("free-asg", "public-asg"):
            self._assert_off_then_on(calls, asg)

    def test_a_group_another_rollout_holds_is_waited_for(self, tmp_path):
        # Suspended at the first read and the next two polls, lifted at the third
        result, calls, now = _rollout(
            tmp_path, *GROUPS, ALREADY_SUSPENDED_ASG="public-asg", SUSPENDED_READS="3"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "public-asg: HealthCheck already suspended" in result.stdout
        assert "public-asg: HealthCheck lifted by the other rollout" in result.stdout
        for asg in ("free-asg", "public-asg"):
            self._assert_off_then_on(calls, asg)
        # Two 30 s polls, then the one 10-minute wait
        assert now == 2 * 30 + 600

    def test_a_suspension_never_lifted_is_taken_over_after_the_bound(self, tmp_path):
        # Left by a killed run: the next rollout resumes it
        result, calls, now = _rollout(
            tmp_path, *GROUPS, ALREADY_SUSPENDED_ASG="public-asg"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            "public-asg: HealthCheck still suspended after 30 minutes; taking it over"
            in result.stdout
        )
        assert "forcing the new deployment anyway" not in result.stdout
        for asg in ("free-asg", "public-asg"):
            self._assert_off_then_on(calls, asg)
        assert now == 1800 + 600

    def test_an_unreadable_group_is_skipped_with_a_warning(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *GROUPS, UNREADABLE_ASG="public-asg")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "could not read public-asg" in result.stdout
        self._assert_off_then_on(calls, "free-asg")
        assert not _indexes(calls, "processes --auto-scaling-group-name public-asg")

    def test_a_failed_resume_fails_the_rollout(self, tmp_path):
        result, calls, _ = _rollout(tmp_path, *GROUPS, *HYGIENE, FAIL_RESUME="1")
        assert result.returncode == 1
        assert (
            "aws autoscaling resume-processes --auto-scaling-group-name free-asg "
            "--scaling-processes HealthCheck --region r"
        ) in result.stdout
        assert (
            "ERROR: HealthCheck is still suspended on free-asg public-asg;"
            in result.stdout
        )
        assert not _indexes(calls, "ssm start-associations-once")

    def test_no_groups_means_no_group_calls(self, tmp_path):
        result, calls, _ = _rollout(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not _indexes(calls, "autoscaling")


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
        # Each group still has a healthy target: a host to replace, not a tier down
        result, calls, now = _rollout(
            tmp_path, *GROUPS, *HYGIENE, UNHEALTHY_TARGET_CALLS="999"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            "targets in free-tg public-tg still not healthy after 15 minutes"
            in result.stdout
        )
        assert len(_indexes(calls, "length(TargetHealthDescriptions")) == 2
        assert "ERROR" not in result.stdout
        for asg in ("free-asg", "public-asg"):
            _assert_off_then_on(calls, asg)
        assert _indexes(calls, "ssm start-associations-once")
        assert now == 600 + 900

    def test_a_group_with_no_healthy_target_fails_the_rollout(self, tmp_path):
        result, calls, _ = _rollout(
            tmp_path,
            *GROUPS,
            *HYGIENE,
            UNHEALTHY_TARGET_CALLS="999",
            NO_HEALTHY_TG="free-tg",
        )
        assert result.returncode == 1
        assert (
            "ERROR: no healthy target in free-tg; that tier is not serving"
            in result.stdout
        )
        # Resumed before the exit, and no hygiene after a failed rollout
        for asg in ("free-asg", "public-asg"):
            _assert_off_then_on(calls, asg)
        assert not _indexes(calls, "ssm start-associations-once")

    def test_a_group_with_no_target_left_is_not_a_tier_down(self, tmp_path):
        # Reads failed until the bound, and the group has nothing to fail now
        result, _, _ = _rollout(
            tmp_path, *GROUPS, FAIL_TARGET_CALLS="999", NO_TARGET_TG="free-tg"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ERROR" not in result.stdout

    def test_an_unreadable_count_decides_nothing(self, tmp_path):
        result, _, _ = _rollout(
            tmp_path,
            *GROUPS,
            UNHEALTHY_TARGET_CALLS="999",
            NO_HEALTHY_TG="free-tg",
            FAIL_COUNT_READ="1",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ERROR" not in result.stdout

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

    def test_the_wait_and_the_tier_check_run_without_groups(self, tmp_path):
        result, calls, _ = _rollout(
            tmp_path, UNHEALTHY_TARGET_CALLS="999", NO_HEALTHY_TG="free-tg"
        )
        assert result.returncode == 1
        assert "ERROR: no healthy target in free-tg" in result.stdout
        assert not _indexes(calls, "autoscaling")


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

    def test_runs_after_every_ecs_service(self):
        # A task definition change's own rollout has started by then
        sources = "\n".join(read(path) for path in sorted(TERRAFORM_DIR.glob("*.tf")))
        services = re.findall(
            r'^resource "aws_ecs_service" "(\w+)"', sources, re.MULTILINE
        )
        depends_on = re.search(r"depends_on\s*=\s*\[([^\]]*)\]", self._block())
        assert services and depends_on
        for service in services:
            assert f"aws_ecs_service.{service}" in depends_on.group(1), service

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

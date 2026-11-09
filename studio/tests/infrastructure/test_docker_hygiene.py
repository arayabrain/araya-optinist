"""Regression tests for the docker hygiene that keeps ECS root volumes from
filling (infrastructure/scripts/docker_hygiene.sh and its wiring).

The script runs as root on every host, so it is checked as text and run with
stub docker / curl / df commands on PATH. The terraform association, the two
deploy hooks and the ECS agent knobs are checked as text, like the other
configuration tests.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from terraform_source import (
    TERRAFORM_DIR,
    attribute,
    read,
    resource_block,
    resource_blocks,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPTS = PROJECT_ROOT / "infrastructure" / "scripts"
HYGIENE = SCRIPTS / "docker_hygiene.sh"
USER_DATA = SCRIPTS / "ecs-user-data.sh"
ECR_BUILD_PUSH = SCRIPTS / "ecr_build_push.sh"
DEPLOYMENT_TF = TERRAFORM_DIR / "deployment.tf"

TEMPLATES = ["ecs", "premium", "background", "public", "public_dataview"]


def _script() -> str:
    assert HYGIENE.exists(), f"docker_hygiene.sh not found at {HYGIENE}"
    return HYGIENE.read_text()


def _statements(source: str) -> list:
    lines = (line.strip() for line in source.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


class TestScriptText:
    def test_keeps_going_after_a_failed_prune(self):
        assert not re.search(r"^\s*set\s+-[a-z]*e", _script(), re.MULTILINE)

    def test_has_no_ssm_parameter_syntax(self):
        assert "{{" not in _script()

    def test_container_prune_spares_the_last_ten_minutes(self):
        prunes = [s for s in _statements(_script()) if s.startswith("docker container")]
        assert prunes == [
            'docker container prune -f --filter "until=$CONTAINER_MIN_AGE"'
        ]
        assert "CONTAINER_MIN_AGE=10m" in _statements(_script())

    def test_image_prune_has_no_age_filter(self):
        """The post-deploy run must remove the image a deploy just replaced,
        however recently it was built; docker's in-use check protects the rest."""
        prunes = [s for s in _statements(_script()) if s.startswith("docker image")]
        assert prunes == ["docker image prune -af"]


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def host(tmp_path):
    """A fake host: stub docker, curl and df on PATH, each logging its calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    _stub(bin_dir / "docker", f'echo "docker $*" >> "{calls}"\nexit 0')
    _stub(
        bin_dir / "curl",
        f'echo "curl" >> "{calls}"\n'
        'if [ -z "${TASKS_JSON+x}" ]; then exit 7; fi\n'
        'if [ -n "${TASKS_JSON_SECOND+x}" ] '
        f'&& [ "$(grep -c "^curl$" "{calls}")" -ge 2 ]; then\n'
        '    printf "%s" "$TASKS_JSON_SECOND"; exit 0\nfi\n'
        'printf "%s" "$TASKS_JSON"',
    )
    _stub(
        bin_dir / "df",
        'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        'echo "/dev/root 100 ${DF_USED:-50} 50 ${DF_USED:-50}% /"',
    )

    def run(**env):
        result = subprocess.run(
            ["bash", str(HYGIENE)],
            env={
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "DOCKER_HYGIENE_LOG": str(tmp_path / "hygiene.log"),
                **env,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        docker_calls = [
            line[len("docker ") :]
            for line in (calls.read_text().splitlines() if calls.exists() else [])
            if line.startswith("docker ")
        ]
        curls = sum(
            1
            for line in (calls.read_text().splitlines() if calls.exists() else [])
            if line == "curl"
        )
        return result, docker_calls, curls

    return run


def _tasks(*statuses):
    return (
        "{"
        + ",".join(f'"t{i}":{{"KnownStatus":"{s}"}}' for i, s in enumerate(statuses))
        + "}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash runs the script")
class TestScriptRun:
    def test_agent_unreachable_prunes_containers_only(self, host):
        result, docker, curls = host()
        assert result.returncode == 0, result.stdout + result.stderr
        assert "container prune -f --filter until=10m" in docker
        assert not any("image prune" in call for call in docker)
        assert curls == 1
        assert "image prune skipped" in result.stdout

    def test_no_task_prunes_containers_only(self, host):
        result, docker, _ = host(TASKS_JSON="{}")
        assert result.returncode == 0
        assert not any("image prune" in call for call in docker)
        assert "running=0 pending=0" in result.stdout

    def test_pending_task_skips_image_prune(self, host):
        result, docker, _ = host(TASKS_JSON=_tasks("RUNNING", "PULLED"))
        assert result.returncode == 0
        assert not any("image prune" in call for call in docker)
        assert "running=1 pending=1" in result.stdout

    def test_steady_host_prunes_images_after_a_second_read(self, host):
        result, docker, curls = host(TASKS_JSON=_tasks("RUNNING", "STOPPED"))
        assert result.returncode == 0, result.stdout + result.stderr
        assert docker.index("container prune -f --filter until=10m") < docker.index(
            "image prune -af"
        )
        assert curls == 2

    def test_task_start_between_the_reads_skips_image_prune(self, host):
        result, docker, curls = host(
            TASKS_JSON=_tasks("RUNNING"), TASKS_JSON_SECOND=_tasks("RUNNING", "PENDING")
        )
        assert result.returncode == 0
        assert not any("image prune" in call for call in docker)
        assert curls == 2

    def test_root_still_over_threshold_exits_2(self, host):
        result, _, _ = host(TASKS_JSON=_tasks("RUNNING"), DF_USED="91")
        assert result.returncode == 2
        assert "WARNING" in result.stdout

    def test_log_is_appended_and_bounded(self, host, tmp_path):
        log = tmp_path / "hygiene.log"
        log.write_text("old\n" * 3000)
        result, _, _ = host(TASKS_JSON=_tasks("RUNNING"))
        assert result.returncode == 0
        lines = log.read_text().splitlines()
        assert len(lines) == 2000
        assert lines[-1].startswith("root volume")


class TestTerraform:
    def test_document_inlines_the_script(self):
        block = resource_block(
            read(DEPLOYMENT_TF), "aws_ssm_document", "docker_hygiene"
        )
        assert attribute(block, "name") == '"${local.env_prefix}-docker-hygiene"'
        assert 'file("${path.module}/../scripts/docker_hygiene.sh")' in block
        assert "<<'HYGIENE_EOF'" in block
        assert attribute(block, "timeoutSeconds") == '"600"'

    def test_association_targets_every_launch_template(self):
        block = resource_block(
            read(DEPLOYMENT_TF), "aws_ssm_association", "docker_hygiene"
        )
        assert attribute(block, "key") == '"tag:aws:ec2launchtemplate:id"'
        assert re.findall(r"aws_launch_template\.(\w+)\.id", block) == TEMPLATES
        assert attribute(block, "schedule_expression") == '"rate(6 hours)"'
        assert attribute(block, "max_concurrency") == '"25%"'
        assert attribute(block, "max_errors") == '"100%"'

    def test_every_declared_launch_template_is_listed(self):
        # A tier added with its own launch template must join the association
        declared = set()
        for path in sorted(TERRAFORM_DIR.glob("*.tf")):
            declared |= set(resource_blocks(read(path), "aws_launch_template"))
        assert declared == set(TEMPLATES)

    def test_association_id_is_an_output(self):
        tf = read(DEPLOYMENT_TF)
        assert re.search(
            r'^output "docker_hygiene_association_id" \{.*?'
            r"aws_ssm_association\.docker_hygiene\.association_id",
            tf,
            re.DOTALL | re.MULTILINE,
        )

    def test_deploy_triggers_hygiene_after_services_are_stable(self):
        block = resource_block(read(DEPLOYMENT_TF), "null_resource", "deploy_to_ecs")
        stable = block.index("wait services-stable")
        hook = block.index("start-associations-once")
        assert stable < hook
        assert "aws_ssm_association.docker_hygiene.association_id" in block
        assert re.search(
            r"start-associations-once.*?\|\| echo", block[hook - 40 :], re.DOTALL
        )


class TestUserData:
    def test_agent_cleanup_knobs(self):
        statements = _statements(USER_DATA.read_text())
        for line in [
            "echo ECS_ENGINE_TASK_CLEANUP_WAIT_DURATION=15m >> /etc/ecs/ecs.config",
            "echo ECS_IMAGE_CLEANUP_INTERVAL=10m >> /etc/ecs/ecs.config",
            "echo ECS_IMAGE_MINIMUM_CLEANUP_AGE=30m >> /etc/ecs/ecs.config",
            "echo ECS_NUM_IMAGES_DELETE_PER_CYCLE=10 >> /etc/ecs/ecs.config",
        ]:
            assert line in statements, line


class TestScriptDeploy:
    """`ecr_build_push.sh --deploy` cycles every service without Terraform, so
    it triggers the same hygiene run, and keeps working before the association
    exists."""

    def _deploy_block(self) -> str:
        source = ECR_BUILD_PUSH.read_text()
        match = re.search(
            r'^if \[ "\$DEPLOY" = true \]; then$.*?^fi$',
            source,
            re.DOTALL | re.MULTILINE,
        )
        assert match, "DEPLOY block not found"
        return match.group()

    def test_waits_then_triggers_hygiene_inside_the_deploy_block(self):
        block = self._deploy_block()
        loop = block.index("--force-new-deployment")
        stable = block.index("wait services-stable")
        hook = block.index("start-associations-once")
        assert loop < stable < hook

    def test_both_steps_only_warn_on_failure(self):
        block = self._deploy_block()
        assert re.search(r"wait services-stable[^\n]*\n\s*\|\| echo", block)
        assert re.search(r"start-associations-once[^\n]*\n\s*\|\| echo", block)

    def test_hygiene_call_is_guarded_by_the_output(self):
        block = self._deploy_block()
        assert (
            'output -raw docker_hygiene_association_id 2>/dev/null || echo ""' in block
        )
        assert 'if [ -n "$HYGIENE_ID" ]; then' in block
        assert block.index('if [ -n "$HYGIENE_ID" ]') < block.index(
            "start-associations-once"
        )

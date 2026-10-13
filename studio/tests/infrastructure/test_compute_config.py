"""Configuration regression tests for compute.tf.

- EFS: the snakemake volume containerPath is correct and consistent across
  all ECS task definitions.
- Free-tier ASG scale-in: ECS managed draining can complete its lifecycle
  hook, and the termination policy spares long-held sticky sessions.
"""

import re
from pathlib import Path

import pytest

# Locate compute.tf relative to the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
COMPUTE_TF = PROJECT_ROOT / "infrastructure" / "terraform" / "compute.tf"

EXPECTED_MOUNT_PATH = "/app/.snakemake"

# Matches mountPoints blocks that reference a snakemake volume
_MOUNT_BLOCK_RE = re.compile(
    r"mountPoints\s*=\s*\[\s*\{[^}]*?"
    r"sourceVolume\s*=\s*\"[^\"]*snmk-volume\"[^}]*?"
    r"containerPath\s*=\s*\"([^\"]+)\"",
    re.DOTALL,
)


class TestEFSMountPaths:
    """Verify EFS mount paths in compute.tf."""

    @pytest.fixture(autouse=True)
    def _load_tf(self):
        assert COMPUTE_TF.exists(), f"compute.tf not found at {COMPUTE_TF}"
        self.tf_content = COMPUTE_TF.read_text()

    def test_premium_mount_path(self):
        """Premium task definition must mount snakemake
        volume at /app/.snakemake (was /efs before fix)."""
        # Find premium task definition section
        premium_section = re.search(
            r'resource\s+"aws_ecs_task_definition"\s+'
            r'"premium".*?(?=resource\s+"aws_)',
            self.tf_content,
            re.DOTALL,
        )
        assert premium_section, "Premium task definition not found in compute.tf"
        match = _MOUNT_BLOCK_RE.search(premium_section.group())
        assert match, "snakemake mountPoints not found in premium " "task definition"
        assert match.group(1) == EXPECTED_MOUNT_PATH

    def test_asg_and_premium_mount_paths_match(self):
        """All snakemake volume containerPaths must be
        identical across task definitions."""
        paths = _MOUNT_BLOCK_RE.findall(self.tf_content)
        assert len(paths) >= 2, (
            f"Expected at least 2 snakemake mount blocks, " f"found {len(paths)}"
        )
        assert all(
            p == EXPECTED_MOUNT_PATH for p in paths
        ), f"Mount path mismatch: {paths}"


def _resource_block(tf: str, resource_type: str, name: str) -> str:
    """Return the source of one ``resource "<type>" "<name>"`` block."""
    match = re.search(
        r'resource\s+"%s"\s+"%s"\s*\{.*?(?=^(?:resource|data|output|locals)\s|\Z)'
        % (re.escape(resource_type), re.escape(name)),
        tf,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f'resource "{resource_type}" "{name}" not found'
    return match.group()


class TestFreeAsgScaleIn:
    """The free-tier ASG's scale-in configuration."""

    @pytest.fixture(autouse=True)
    def _load(self):
        tf = COMPUTE_TF.read_text()
        self.asg = _resource_block(tf, "aws_autoscaling_group", "main")
        self.capacity_provider = _resource_block(
            tf, "aws_ecs_capacity_provider", "main"
        )

    def test_asg_carries_the_amazon_ecs_managed_tag(self):
        """The ECS service-linked role may complete the managed-draining hook
        only on a group carrying this tag; without it every scale-in waits
        out the hook's 3600 s timeout."""
        tags = re.findall(r'tag\s*\{\s*key\s*=\s*"([^"]+)"', self.asg)
        assert "AmazonECSManaged" in tags

    def test_capacity_provider_keeps_managed_draining_on(self):
        """The tag exists for the draining hook; disabling draining removes
        the hook and leaves the tag without purpose."""
        assert not re.search(
            r'managed_draining\s*=\s*"DISABLED"', self.capacity_provider
        )

    def test_asg_terminates_the_newest_instance_first(self):
        """With sticky sessions the oldest instance holds the longest-held
        sessions, so ``OldestInstance`` removes the most established users."""
        match = re.search(r"^\s*termination_policies\s*=\s*(.+)$", self.asg, re.M)
        assert match, "termination_policies not found"
        assert match.group(1).strip() == '["NewestInstance"]'

"""Configuration regression tests for compute.tf.

- EFS: the snakemake volume containerPath is correct and consistent across
  all ECS task definitions.
- Free-tier ASG scale-in: ECS managed draining can complete its lifecycle
  hook, and the termination policy spares long-held sticky sessions.
- Base premium instance: termination protection, with Terraform still able
  to replace it.
"""

import re

import pytest
from terraform_source import TERRAFORM_DIR, attribute, read, resource_block, string_list

COMPUTE_TF = TERRAFORM_DIR / "compute.tf"

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


class TestFreeAsgScaleIn:
    """The free-tier ASG's scale-in configuration."""

    @pytest.fixture(autouse=True)
    def _load(self):
        tf = read(COMPUTE_TF)
        self.asg = resource_block(tf, "aws_autoscaling_group", "main")
        self.capacity_provider = resource_block(tf, "aws_ecs_capacity_provider", "main")
        self.service = resource_block(tf, "aws_ecs_service", "autoscaling")

    def test_asg_carries_the_amazon_ecs_managed_tag(self):
        """The ECS service-linked role may complete the managed-draining hook
        only on a group carrying this tag; without it every scale-in waits
        out the hook's 3600 s timeout. ECS adds the tag itself, and an apply
        that does not declare it removes it."""
        tags = re.findall(r'tag\s*\{\s*key\s*=\s*"([^"]+)"', self.asg)
        assert "AmazonECSManaged" in tags

    def test_capacity_provider_enables_managed_draining(self):
        """Declared rather than left to the API default, so the hook the tag
        exists for cannot disappear unnoticed."""
        assert attribute(self.capacity_provider, "managed_draining") == '"ENABLED"'

    def test_asg_terminates_the_newest_instance_first(self):
        """With sticky sessions the oldest instance holds the longest-held
        sessions, so ``OldestInstance`` removes the most established users."""
        policies = string_list(attribute(self.asg, "termination_policies"))
        assert policies == ["NewestInstance"]

    def test_free_service_lets_a_drain_stop_its_task_at_once(self):
        """Managed draining completes its hook only once the draining instance
        has no tasks. At 0 the task stops without waiting for a replacement;
        raised, the replacement has nowhere to go (one task per instance, and
        the free task fills one), so the hook waits out its 3600 s timeout
        again. Raising this needs that solved first."""
        assert attribute(self.service, "deployment_minimum_healthy_percent") == "0"

    def test_free_service_is_pinned_to_free_instances(self):
        """The other tiers' services pin themselves with ``memberOf`` on the
        ``tier`` attribute. Without the same pin a free replacement task
        placed during a drain could land on another tier's instance with
        spare capacity, leaving ECS to choose which of two free tasks to
        stop when the count comes down."""
        constraints = re.findall(
            r'placement_constraints\s*\{\s*type\s*=\s*"memberOf"\s*'
            r'expression\s*=\s*"([^"]+)"',
            self.service,
        )
        assert constraints == ["attribute:tier == free"]


class TestBasePremiumInstanceProtection:
    """The Terraform-managed base premium instance."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.instance = resource_block(read(COMPUTE_TF), "aws_instance", "premium")

    def test_termination_protection_is_on(self):
        """Premium manager leaves the instance alone only while
        PREMIUM_INSTANCE_IDS names it; an unset variable protects nothing.
        Termination protection refuses TerminateInstances from any caller."""
        assert attribute(self.instance, "disable_api_termination") == "true"

    def test_terraform_can_still_replace_it(self):
        """Without force_destroy, a Terraform replace or destroy of a
        protected instance fails."""
        assert attribute(self.instance, "force_destroy") == "true"

"""Configuration regression tests for compute.tf.

- EFS: the snakemake volume containerPath is correct and consistent across
  all ECS task definitions.
- Free-tier ASG scale-in: ECS managed draining can complete its lifecycle
  hook, the termination policy spares long-held sticky sessions, and no
  terminate hook of the group's own delays a termination.
- Free-tier ASG scale-out: the launch hook holds InService until the
  instance has booted.
- Base premium instance: termination protection, with Terraform still able
  to replace it.
- Workflow tier sizing: each tier's task definition and launch template take
  their sizes from that tier's variable in main.tf; its defaults, partial
  overrides and validation rules.
"""

import re

import pytest
from terraform_source import (
    TERRAFORM_DIR,
    attribute,
    read,
    resource_block,
    string_list,
    variable_block,
)

COMPUTE_TF = TERRAFORM_DIR / "compute.tf"
MAIN_TF = TERRAFORM_DIR / "main.tf"

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
        self.tf = tf
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

    def test_asg_declares_no_initial_lifecycle_hook(self):
        """Guards the ``ignore_changes`` carve-out: a block added here, plain
        or ``dynamic``, would be silently ignored on existing groups. A hook
        belongs in an ``aws_autoscaling_lifecycle_hook`` resource."""
        assert not re.search(
            r'^\s*(?:dynamic\s+")?initial_lifecycle_hook"?\s*\{',
            self.asg,
            re.MULTILINE,
        )

    def test_asg_ignores_changes_to_initial_lifecycle_hook(self):
        """``initial_lifecycle_hook`` is ForceNew and never read back from
        AWS, so the hooks a group was created with stay in state; without
        this the removed blocks replace the group."""
        match = re.search(r"ignore_changes\s*=\s*\[(.*?)\]", self.asg, re.DOTALL)
        assert match, "ignore_changes not found"
        assert "initial_lifecycle_hook" in re.findall(r"\w+", match.group(1))

    def test_launch_hook_holds_in_service_until_the_instance_has_booted(self):
        """InService starts the target-group registration, the ELB
        health-check grace, the refresh warmup and the scale_up cooldown.
        Without the wait the target is registered with nothing on port 8000,
        goes unhealthy and fires free-tg-unhealthy-hosts on every scale-out,
        and a slow start can outlast the grace."""
        hook = resource_block(self.tf, "aws_autoscaling_lifecycle_hook", "launch")
        assert attribute(hook, "autoscaling_group_name") == (
            "aws_autoscaling_group.main.name"
        )
        assert attribute(hook, "lifecycle_transition") == (
            '"autoscaling:EC2_INSTANCE_LAUNCHING"'
        )
        assert attribute(hook, "heartbeat_timeout") == "300"
        assert attribute(hook, "default_result") == '"CONTINUE"'
        # The name the group was created with, so the first apply on an
        # existing group overwrites that hook rather than adding a second
        assert attribute(hook, "name") == '"${local.env_prefix}-launch-hook"'

    def test_no_terminate_hook_of_the_groups_own(self):
        """A terminate hook nothing completes holds every scale-in in
        ``Terminating:Wait`` for its timeout after ECS's draining hook has
        completed. ECS installs and completes its own hook."""
        transitions = re.findall(
            r'resource\s+"aws_autoscaling_lifecycle_hook"\s+"[^"]+"\s*\{.*?'
            r'lifecycle_transition\s*=\s*"([^"]+)"',
            self.tf,
            re.DOTALL,
        )
        assert "autoscaling:EC2_INSTANCE_TERMINATING" not in transitions


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


# Workflow tier: (sizing variable, task definition, launch template)
WORKFLOW_TIERS = {
    "free": ("free_tier_sizing", "autoscaling", "ecs"),
    "premium": ("premium_tier_sizing", "premium", "premium"),
}

TIER_SIZING_DEFAULTS = {
    "free_tier_sizing": {
        "task_cpu": 2048,
        "task_memory_mib": 7168,
        "container_cpu": 1536,
        "container_memory_mib": 6656,
        "container_memory_reservation_mib": 4096,
        "container_max_swap_mib": 32768,
        "container_swappiness": 20,
        "root_volume_gib": 88,
        "swap_volume_gib": 32,
    },
    "premium_tier_sizing": {
        "task_cpu": 2048,
        "task_memory_mib": 7168,
        "container_cpu": 1536,
        "container_memory_mib": 6656,
        "container_memory_reservation_mib": 4096,
        "container_max_swap_mib": 32768,
        "container_swappiness": 20,
        "root_volume_gib": 48,
        "swap_volume_gib": 32,
    },
}

# Validation conditions, written once per tier variable ({v})
TIER_SIZING_RULES = [
    "var.{v}.container_memory_mib <= var.{v}.task_memory_mib",
    "var.{v}.container_memory_reservation_mib < var.{v}.container_memory_mib",
    "var.{v}.container_cpu <= var.{v}.task_cpu",
    "var.{v}.swap_volume_gib >= 1",
    "var.{v}.container_max_swap_mib <= var.{v}.swap_volume_gib * 1024",
    "var.{v}.container_swappiness >= 0 && var.{v}.container_swappiness <= 100",
]

# A sizing attribute of a task definition or launch template set to a number
_SIZING_LITERAL_RE = re.compile(
    r"^\s*(cpu|memory|memoryReservation|maxSwap|swappiness|volume_size"
    r"|swap_size_mb)\s*=\s*\d",
    re.MULTILINE,
)

_BLOCK_DEVICE_RE = re.compile(
    r"block_device_mappings\s*\{\s*device_name\s*=\s*(\S+)\s*ebs\s*\{(.*?)\}",
    re.DOTALL,
)


@pytest.mark.parametrize("tier", WORKFLOW_TIERS)
class TestWorkflowTierSizing:
    """Each workflow tier's task definition and launch template take their
    sizes from that tier's variable, so the two tiers can be sized apart."""

    @pytest.fixture(autouse=True)
    def _load(self, tier):
        variable, task_definition, launch_template = WORKFLOW_TIERS[tier]
        tf = read(COMPUTE_TF)
        self.var = f"var.{variable}"
        self.other_variable = next(
            other for other, _, _ in WORKFLOW_TIERS.values() if other != variable
        )
        self.task_definition = resource_block(
            tf, "aws_ecs_task_definition", task_definition
        )
        self.launch_template = resource_block(
            tf, "aws_launch_template", launch_template
        )

    def test_task_definition_reads_its_tier(self):
        # Task and container both set cpu / memory; the container's follow
        # container_definitions
        task, container = self.task_definition.split("container_definitions", 1)
        assert attribute(task, "cpu") == f"{self.var}.task_cpu"
        assert attribute(task, "memory") == f"{self.var}.task_memory_mib"
        assert attribute(container, "cpu") == f"{self.var}.container_cpu"
        assert attribute(container, "memory") == f"{self.var}.container_memory_mib"
        assert attribute(container, "memoryReservation") == (
            f"{self.var}.container_memory_reservation_mib"
        )
        assert attribute(container, "maxSwap") == f"{self.var}.container_max_swap_mib"
        assert attribute(container, "swappiness") == (
            f"{self.var}.container_swappiness"
        )

    def test_launch_template_reads_its_tier(self):
        devices = dict(_BLOCK_DEVICE_RE.findall(self.launch_template))
        assert attribute(devices['"/dev/xvda"'], "volume_size") == (
            f"{self.var}.root_volume_gib"
        )
        assert attribute(devices["local.swap_device_name"], "volume_size") == (
            f"{self.var}.swap_volume_gib"
        )

    def test_host_swap_is_the_whole_swap_volume(self):
        """ecs-user-data.sh runs mkswap on the device, so swap_size_mb only
        gates swap and sizes the fallback file. Derived from the volume, it
        cannot disagree with it. The script finds the device by the name the
        mapping attaches it under."""
        assert attribute(self.launch_template, "swap_size_mb") == (
            f"{self.var}.swap_volume_gib * 1024"
        )
        assert attribute(self.launch_template, "swap_device_name") == (
            "local.swap_device_name"
        )

    def test_no_size_is_literal_or_from_the_other_tier(self):
        for source in (self.task_definition, self.launch_template):
            assert not _SIZING_LITERAL_RE.findall(source)
            assert self.other_variable not in source


@pytest.mark.parametrize("variable", TIER_SIZING_DEFAULTS)
class TestWorkflowTierSizingVariables:
    """The per-tier sizing variables in main.tf."""

    @pytest.fixture(autouse=True)
    def _load(self, variable):
        self.block = variable_block(read(MAIN_TF), variable)

    def test_defaults(self, variable):
        """A default applies to every environment whose tfvars leaves the
        field out, so changing one resizes them all at the next apply."""
        defaults = re.findall(
            r"^\s*(\w+)\s*=\s*optional\(number,\s*(\d+)\)", self.block, re.MULTILINE
        )
        assert {name: int(value) for name, value in defaults} == (
            TIER_SIZING_DEFAULTS[variable]
        )

    def test_a_partial_override_keeps_the_other_defaults(self):
        """``premium_tier_sizing = { task_memory_mib = ... }`` must leave the
        other fields at their defaults: every field optional, and null
        treated as the empty default."""
        match = re.search(r"type\s*=\s*object\(\{(.*?)\}\)", self.block, re.DOTALL)
        assert match, "object type not found"
        fields = re.findall(r"^\s*\w+\s*=\s*(.+?)\s*$", match.group(1), re.MULTILINE)
        assert fields and all(field.startswith("optional(") for field in fields)
        assert attribute(self.block, "default") == "{}"
        assert attribute(self.block, "nullable") == "false"

    def test_validation_rules(self, variable):
        """Plan fails on what ECS would reject at apply (container above the
        task, reservation not below the hard limit), on a container swap
        ceiling above the host's swap, and on a swap volume a launch template
        cannot attach. Both tiers carry the same rules."""
        conditions = re.findall(
            r"^\s*condition\s*=\s*(.+?)\s*$", self.block, re.MULTILINE
        )
        assert conditions == [rule.format(v=variable) for rule in TIER_SIZING_RULES]

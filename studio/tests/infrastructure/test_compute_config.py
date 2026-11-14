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
- Workflow services: the free and premium services leave desired_count to
  their manager Lambdas.
- Workflow tier sizing: each tier's task definition and launch template take
  their sizes from that tier's sizing in main.tf; its defaults, optional
  fields, partial overrides and validation rules, unknown fields included.
  The plan also checks the task against the tier's instance type and the
  launch template against the AMI and gp3's limits.
- Uvicorn workers: every task definition that starts cloud-startup.sh sets
  its own count (the public and background ones live in their own files).
- Health check grace: every service that starts cloud-startup.sh outlasts
  its database wait.
"""

import re

import pytest
from terraform_source import (
    PROJECT_ROOT,
    TERRAFORM_DIR,
    attribute,
    data_block,
    identifier_list,
    read,
    resource_block,
    string_list,
    variable_block,
)

COMPUTE_TF = TERRAFORM_DIR / "compute.tf"
MAIN_TF = TERRAFORM_DIR / "main.tf"
PREMIUM_MANAGER_TF = TERRAFORM_DIR / "premium_manager.tf"
WAIT_FOR_DATABASE_SH = (
    PROJECT_ROOT / "infrastructure" / "scripts" / "wait_for_database.sh"
)

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

    def test_asg_replaces_hosts_ecs_cannot_fix(self):
        """ECS replaces an unhealthy task by itself; only the target group
        check replaces a host that never gets a task or that ECS keeps
        re-placing onto while it is wedged, the only free host. ``EC2`` would
        leave both until someone acts. The cost: past the ASG grace, a task
        restart that does not serve within about 300 s loses its instance."""
        assert attribute(self.asg, "health_check_type") == '"ELB"'

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


class TestWorkflowServiceDesiredCount:
    """The free and premium services' task counts belong to their managers."""

    @pytest.mark.parametrize("service", ["autoscaling", "premium"])
    def test_apply_leaves_desired_count_alone(self, service):
        """free-manager and premium-manager set one task per instance. Without
        this an apply pulls the count back to 1, and ECS stops the task on
        every other instance of the tier until the manager's next sync."""
        block = resource_block(read(COMPUTE_TF), "aws_ecs_service", service)
        assert "desired_count" in identifier_list(attribute(block, "ignore_changes"))


# Workflow tier: (sizing name, task definition, launch template). The resources
# read local.<name>, the tier's defaults merged with var.<name>
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
        "root_volume_gib": 64,
        "swap_volume_gib": 32,
    },
}

# Fields with no default: absent, the launch template leaves the attribute unset
TIER_SIZING_OPTIONAL_FIELDS = [
    "root_volume_iops",
    "root_volume_throughput_mibps",
    "swap_volume_iops",
    "swap_volume_throughput_mibps",
]

TIER_SIZING_MINIMUMS = {
    "task_cpu": 1,
    "task_memory_mib": 1,
    "container_cpu": 0,
    "container_memory_mib": 1,
    "container_memory_reservation_mib": 1,
    "container_max_swap_mib": 0,
    "container_swappiness": 0,
    "root_volume_gib": 30,
    "swap_volume_gib": 1,
    "root_volume_iops": 3000,
    "root_volume_throughput_mibps": 125,
    "swap_volume_iops": 3000,
    "swap_volume_throughput_mibps": 125,
}

TIER_SIZING_MAXIMUMS = {
    "container_swappiness": 100,
    "root_volume_iops": 80000,
    "root_volume_throughput_mibps": 2000,
    "swap_volume_iops": 80000,
    "swap_volume_throughput_mibps": 2000,
}

# Single-field rules, as validations of each tier variable ({v})
TIER_SIZING_RULES = [
    "length(setsubtract(keys(var.{v}), concat(keys(local.{v}_defaults),"
    " local.tier_sizing_optional_fields))) == 0",
    "alltrue([for value in values(var.{v}) : value != null])",
    "alltrue([for value in values(var.{v}) : value == null || value == floor(value)])",
    "alltrue([for field, value in var.{v} : value == null"
    " || !contains(keys(local.tier_sizing_minimums), field)"
    " || value >= local.tier_sizing_minimums[field]])",
    "alltrue([for field, value in var.{v} : value == null"
    " || !contains(keys(local.tier_sizing_maximums), field)"
    " || value <= local.tier_sizing_maximums[field]])",
]

# Rules across fields and against the tier's instance type ({t}), as
# preconditions of each tier's task definition, on the merged sizes ({s})
TIER_SIZING_PRECONDITIONS = [
    "{s}.container_memory_mib <= {s}.task_memory_mib",
    "{s}.container_memory_reservation_mib < {s}.container_memory_mib",
    "{s}.container_cpu <= {s}.task_cpu",
    "{s}.container_max_swap_mib <= {s}.swap_volume_gib * 1024",
    "{s}.task_memory_mib <= data.aws_ec2_instance_type.{t}.memory_size * 15 / 16",
    "{s}.task_cpu <= data.aws_ec2_instance_type.{t}.default_vcpus * 1024",
]

# Rules against the AMI and gp3, as preconditions of each tier's launch template
TIER_LAUNCH_TEMPLATE_PRECONDITIONS = [
    "{s}.root_volume_gib >= local.effective_ami_root_gib",
    "alltrue([for volume in values(local.tier_volumes.{t}) :"
    " volume.throughput_mibps * 4 <= volume.iops])",
    "alltrue([for volume in values(local.tier_volumes.{t}) :"
    " volume.iops <= max(3000, volume.gib * 500)])",
]

# A sizing attribute of a task definition or launch template set to a number
_SIZING_LITERAL_RE = re.compile(
    r"^\s*(cpu|memory|memoryReservation|maxSwap|swappiness|volume_size|iops"
    r"|throughput|swap_size_mb)\s*=\s*\d",
    re.MULTILINE,
)

_BLOCK_DEVICE_RE = re.compile(
    r"block_device_mappings\s*\{\s*device_name\s*=\s*(\S+)\s*ebs\s*\{(.*?)\}",
    re.DOTALL,
)


def _values(source: str, name: str) -> list:
    """Every ``<name> = <value>`` right-hand side in ``source``. A value runs
    to the end of the line its brackets close on; whitespace is collapsed, and
    dropped inside the brackets' ends."""
    values = []
    for match in re.finditer(r"^\s*%s\s*=[ \t]*" % re.escape(name), source, re.M):
        depth, end = 0, match.end()
        while end < len(source) and not (source[end] == "\n" and depth == 0):
            depth += (source[end] in "([{") - (source[end] in ")]}")
            end += 1
        value = " ".join(source[match.end() : end].split())
        value = re.sub(r"([(\[{]) ", r"\1", re.sub(r" ([)\]}])", r"\1", value))
        values.append(value)
    return values


def _local_numbers(tf: str, name: str) -> dict:
    """``{key: value}`` of the number map assigned to local ``name``."""
    match = re.search(
        r"^\s*%s\s*=\s*\{(.*?)^\s*\}" % re.escape(name),
        tf,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"local {name} not found"
    return {
        key: int(value)
        for key, value in re.findall(
            r"^\s*(\w+)\s*=\s*(\d+)\s*$", match.group(1), re.MULTILINE
        )
    }


def _local_strings(tf: str, name: str) -> list:
    """The items of the string list assigned to local ``name``."""
    match = re.search(
        r"^\s*%s\s*=\s*\[(.*?)^\s*\]" % re.escape(name),
        tf,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"local {name} not found"
    return re.findall(r'"([^"]*)"', match.group(1))


@pytest.mark.parametrize("tier", WORKFLOW_TIERS)
class TestWorkflowTierSizing:
    """Each workflow tier's task definition and launch template take their
    sizes from that tier's sizing, so the two tiers can be sized apart."""

    @pytest.fixture(autouse=True)
    def _load(self, tier):
        name, task_definition, launch_template = WORKFLOW_TIERS[tier]
        tf = read(COMPUTE_TF)
        self.tf = tf
        self.tier = tier
        self.sizing = f"local.{name}"
        self.other_tier = next(other for other in WORKFLOW_TIERS if other != tier)
        self.other_name = WORKFLOW_TIERS[self.other_tier][0]
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
        assert attribute(task, "cpu") == f"{self.sizing}.task_cpu"
        assert attribute(task, "memory") == f"{self.sizing}.task_memory_mib"
        assert attribute(container, "cpu") == f"{self.sizing}.container_cpu"
        assert attribute(container, "memory") == f"{self.sizing}.container_memory_mib"
        assert attribute(container, "memoryReservation") == (
            f"{self.sizing}.container_memory_reservation_mib"
        )
        assert attribute(container, "maxSwap") == (
            f"{self.sizing}.container_max_swap_mib"
        )
        assert attribute(container, "swappiness") == (
            f"{self.sizing}.container_swappiness"
        )

    def test_launch_template_reads_its_tier(self):
        """IOPS and throughput are null unless the tier sets them: an unset
        attribute keeps gp3's baseline and leaves today's templates as they
        are, where an explicit default would change every environment's
        template at the next apply."""
        devices = dict(_BLOCK_DEVICE_RE.findall(self.launch_template))
        for device, volume in (
            ('"/dev/xvda"', "root"),
            ("local.swap_device_name", "swap"),
        ):
            ebs = devices[device]
            assert attribute(ebs, "volume_size") == (
                f"{self.sizing}.{volume}_volume_gib"
            )
            assert attribute(ebs, "iops") == (
                f'lookup({self.sizing}, "{volume}_volume_iops", null)'
            )
            assert attribute(ebs, "throughput") == (
                f'lookup({self.sizing}, "{volume}_volume_throughput_mibps", null)'
            )

    def test_host_swap_is_the_whole_swap_volume(self):
        """ecs-user-data.sh runs mkswap on the device, so swap_size_mb only
        gates swap and sizes the fallback file. Derived from the volume, it
        cannot disagree with it. The script finds the device by the name the
        mapping attaches it under."""
        assert attribute(self.launch_template, "swap_size_mb") == (
            f"{self.sizing}.swap_volume_gib * 1024"
        )
        assert attribute(self.launch_template, "swap_device_name") == (
            "local.swap_device_name"
        )

    def test_no_size_is_literal_or_from_the_other_tier(self):
        for source in (self.task_definition, self.launch_template):
            assert not _SIZING_LITERAL_RE.findall(source)
            assert self.other_name not in source
            assert f"aws_ec2_instance_type.{self.other_tier}" not in source
            assert f"tier_volumes.{self.other_tier}" not in source

    def test_rules_across_fields_are_task_definition_preconditions(self):
        """Plan fails on what ECS would reject at apply (container above the
        task, reservation not below the hard limit), on a container swap
        ceiling above the host's swap, and on a task the tier's instance type
        cannot hold: ECS registers less memory than the nominal and never
        places it. A variable validation cannot read the merged sizes
        (Terraform reports a cycle); a precondition can, and also fails the
        plan. Both tiers carry the same rules."""
        assert _values(self.task_definition, "condition") == [
            rule.format(s=self.sizing, t=self.tier)
            for rule in TIER_SIZING_PRECONDITIONS
        ]

    def test_fit_checks_read_the_type_the_tier_launches(self):
        """The task is checked against the type its instances launch as. For
        premium the base and per-user instances' types are pinned in
        TestWorkflowTierGuardInputs."""
        data = data_block(self.tf, "aws_ec2_instance_type", self.tier)
        assert attribute(data, "instance_type") == f"var.{self.tier}_instance_type"
        assert attribute(self.launch_template, "instance_type") == (
            f"var.{self.tier}_instance_type"
        )

    def test_launch_template_preconditions(self):
        """Plan fails on values a template accepts and a launch then rejects:
        a root volume below the AMI's root snapshot, and gp3 throughput or
        IOPS above what the volume's IOPS or size allows."""
        assert _values(self.launch_template, "condition") == [
            rule.format(s=self.sizing, t=self.tier)
            for rule in TIER_LAUNCH_TEMPLATE_PRECONDITIONS
        ]


class TestWorkflowTierGuardInputs:
    """What the launch templates' preconditions compare against."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.tf = read(COMPUTE_TF)

    def test_the_ami_read_is_the_one_the_templates_boot(self):
        """Stock or custom (from SSM), the lookup is by the ID the templates
        use; the stock image moves with most_recent, the custom one with each
        pipeline build, so a fixed floor alone can go stale."""
        data = data_block(self.tf, "aws_ami", "effective")
        assert string_list(attribute(data, "owners")) == ["self", "amazon"]
        assert attribute(data, "name") == '"image-id"'
        assert attribute(data, "values") == "[local.effective_ami_id]"
        for template in WORKFLOW_TIERS.values():
            block = resource_block(self.tf, "aws_launch_template", template[2])
            assert attribute(block, "image_id") == "local.effective_ami_id"

    def test_every_premium_instance_launches_as_the_checked_type(self):
        """The premium task is checked against var.premium_instance_type, so
        the check holds only while the base instance and the premium manager's
        per-user launches (PREMIUM_INSTANCE_TYPE) take their type from it."""
        base = resource_block(self.tf, "aws_instance", "premium")
        assert attribute(base, "instance_type") == "var.premium_instance_type"
        manager = resource_block(
            read(PREMIUM_MANAGER_TF), "aws_lambda_function", "premium_manager"
        )
        assert attribute(manager, "PREMIUM_INSTANCE_TYPE") == (
            "var.premium_instance_type"
        )

    def test_each_tiers_volume_rules_read_its_own_sizing(self):
        """local.tier_volumes keys each tier to its own merged sizing, which
        the tier's launch-template rules then read."""
        block = _values(self.tf, "tier_volumes")[0]
        tiers = ", ".join(
            f"{tier} = local.{name}" for tier, (name, _, _) in WORKFLOW_TIERS.items()
        )
        assert block.startswith(f"{{for tier, sizing in {{{tiers}}} : tier => ")

    def test_the_root_snapshot_is_the_root_devices_mapping(self):
        assert _values(self.tf, "effective_ami_root_gib") == [
            "one([for mapping in data.aws_ami.effective.block_device_mappings :"
            " tonumber(mapping.ebs.volume_size)"
            " if mapping.device_name == data.aws_ami.effective.root_device_name])"
        ]

    def test_unset_volume_performance_is_the_gp3_baseline(self):
        """A rule on throughput against IOPS must use the IOPS the volume
        gets: 3,000 when the field is unset, as the minimums are."""
        block = _values(self.tf, "tier_volumes")[0]
        for volume_field, baseline in (
            ("iops", 3000),
            ("throughput_mibps", 125),
        ):
            assert (
                f'{volume_field} = lookup(sizing, "${{volume}}_volume_{volume_field}",'
                f" {baseline})"
            ) in block
            assert TIER_SIZING_MINIMUMS[f"root_volume_{volume_field}"] == baseline
            assert TIER_SIZING_MINIMUMS[f"swap_volume_{volume_field}"] == baseline


@pytest.mark.parametrize("variable", TIER_SIZING_DEFAULTS)
class TestWorkflowTierSizingVariables:
    """The per-tier sizing variables and their defaults in main.tf."""

    @pytest.fixture(autouse=True)
    def _load(self, variable):
        self.main = read(MAIN_TF)
        self.block = variable_block(self.main, variable)

    def test_defaults(self, variable):
        """A default applies to every environment whose tfvars leaves the
        field out, so changing one resizes them all at the next apply."""
        assert _local_numbers(self.main, f"{variable}_defaults") == (
            TIER_SIZING_DEFAULTS[variable]
        )

    def test_a_partial_override_keeps_the_other_defaults(self, variable):
        """``premium_tier_sizing = { task_memory_mib = ... }`` must leave the
        other fields at their defaults: the resources read the defaults merged
        with the override, and null is the empty override. A null field is
        dropped from the merge, so only the null rule reports it."""
        assert _values(self.main, variable) == [
            f"merge(local.{variable}_defaults,"
            f" {{for field, value in var.{variable} :"
            " field => value if value != null})"
        ]
        assert attribute(self.block, "default") == "{}"
        assert attribute(self.block, "nullable") == "false"

    def test_an_unknown_field_reaches_the_rules(self):
        """An object type drops an attribute it does not declare before
        validation runs, so a misspelled field (``_mb`` for ``_mib``) would
        leave its default in place without an error. A map keeps it for the
        unknown-field rule."""
        assert attribute(self.block, "type") == "map(number)"

    def test_optional_fields_have_no_default(self, variable):
        """Absent from the defaults, so the merged sizing lacks them and the
        launch template's lookup gives null."""
        optional = _local_strings(self.main, "tier_sizing_optional_fields")
        assert optional == TIER_SIZING_OPTIONAL_FIELDS
        assert not set(optional) & TIER_SIZING_DEFAULTS[variable].keys()

    def test_every_field_has_a_minimum_its_default_meets(self, variable):
        """Zero where ECS accepts none; the AMI snapshot size for the root
        volume, below which every launch fails after the template is
        accepted; gp3's included baseline for IOPS and throughput."""
        minimums = _local_numbers(self.main, "tier_sizing_minimums")
        assert minimums == TIER_SIZING_MINIMUMS
        defaults = TIER_SIZING_DEFAULTS[variable]
        assert minimums.keys() == defaults.keys() | set(TIER_SIZING_OPTIONAL_FIELDS)
        assert all(defaults[field] >= minimums[field] for field in defaults)

    def test_maximums_and_the_defaults_within_them(self, variable):
        """Docker's swappiness ceiling, gp3's IOPS and throughput ceilings."""
        maximums = _local_numbers(self.main, "tier_sizing_maximums")
        assert maximums == TIER_SIZING_MAXIMUMS
        defaults = TIER_SIZING_DEFAULTS[variable]
        assert all(
            defaults[field] <= maximum
            for field, maximum in maximums.items()
            if field in defaults
        )

    def test_validation_rules(self, variable):
        """Plan fails on an unknown, null, fractional, too-small or too-large
        field. Both tiers carry the same rules."""
        assert _values(self.block, "condition") == [
            rule.format(v=variable) for rule in TIER_SIZING_RULES
        ]


class TestUvicornWorkers:
    """Every task definition that starts cloud-startup.sh sets its worker count."""

    @pytest.mark.parametrize(
        "tf_file,task_definition,workers",
        [
            ("compute.tf", "autoscaling", "3"),
            ("compute.tf", "premium", "2"),
            ("public_service.tf", "public", "1"),
            ("background_service.tf", "background", "1"),
        ],
    )
    def test_worker_count_is_explicit(self, tf_file, task_definition, workers):
        """Unset, cloud-startup.sh's default would apply; each worker holds a
        full app process out of the memory the instance shares."""
        block = resource_block(
            read(TERRAFORM_DIR / tf_file), "aws_ecs_task_definition", task_definition
        )
        values = re.findall(
            r'name\s*=\s*"UVICORN_WORKERS"\s*,?\s*value\s*=\s*"([^"]*)"', block
        )
        assert values == [workers]


class TestServiceHealthCheckGrace:
    """Every service that starts cloud-startup.sh outlasts its database wait.

    ECS ignores failing load balancer and container health checks for the
    grace period only. A task still waiting for the database past it is
    marked unhealthy and replaced, and its replacement waits again.
    """

    # Migrations, app start and the first passing health checks
    STARTUP_MARGIN_SECONDS = 120

    @pytest.mark.parametrize(
        "tf_file,service",
        [
            ("compute.tf", "autoscaling"),
            ("compute.tf", "premium"),
            ("public_service.tf", "public"),
            ("background_service.tf", "background"),
        ],
    )
    def test_grace_period_covers_the_database_wait(self, tf_file, service):
        script = read(WAIT_FOR_DATABASE_SH)
        match = re.search(
            r"^wait_seconds=\$\{DB_WAIT_SECONDS:-(\d+)\}$", script, re.MULTILINE
        )
        assert match, "database wait bound not found in wait_for_database.sh"
        wait = int(match.group(1))

        tf = read(TERRAFORM_DIR / tf_file)
        # The override is for tests; a task definition setting it would
        # change the wait this test checks against
        assert "DB_WAIT_SECONDS" not in tf

        block = resource_block(tf, "aws_ecs_service", service)
        grace = int(attribute(block, "health_check_grace_period_seconds"))
        assert grace >= wait + self.STARTUP_MARGIN_SECONDS, (
            f"grace {grace} s < database wait {wait} s "
            f"+ margin {self.STARTUP_MARGIN_SECONDS} s"
        )

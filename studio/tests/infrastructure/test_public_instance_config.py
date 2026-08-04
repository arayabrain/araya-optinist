"""Configuration assertions for the public tier, read from the terraform source.

These are *configuration* assertions, not behavioral ones: terraform declaring
``AFTER_7_DAYS`` is not proof AWS applied it, and the deployed check stays a
a deployed check. What they do catch is a PR that changes the declaration -
which is where our own regressions live.

The ALB priority band check is the exception and is a real invariant test: the
premium band is
allocated at runtime by the premium-manager Lambda while the public and free
bands are static terraform, so nothing but this test connects the two halves.
"""

import re
from pathlib import Path

import pytest
from premium_manager import MAX_PREMIUM_PRIORITY, get_next_available_priority

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TERRAFORM_DIR = PROJECT_ROOT / "infrastructure" / "terraform"

PUBLIC_ALB_RULES_TF = TERRAFORM_DIR / "public_alb_rules.tf"
PUBLIC_SERVICE_TF = TERRAFORM_DIR / "public_service.tf"
PUBLIC_CLEANUP_TF = TERRAFORM_DIR / "public_cleanup.tf"
INFRASTRUCTURE_TF = TERRAFORM_DIR / "infrastructure.tf"
MAIN_TF = TERRAFORM_DIR / "main.tf"

PREMIUM_BAND_START = 100


def _read(path: Path) -> str:
    assert path.exists(), f"terraform source not found at {path}"
    return path.read_text()


def _resource_block(tf: str, resource_type: str, name: str) -> str:
    """Return the source of one ``resource "<type>" "<name>"`` block.

    Terminates at the next top-level ``resource`` / ``data`` / ``output``
    declaration, matching the technique in ``test_compute_config.py``.
    """
    match = re.search(
        r'resource\s+"%s"\s+"%s"\s*\{.*?(?=^(?:resource|data|output|locals)\s|\Z)'
        % (re.escape(resource_type), re.escape(name)),
        tf,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f'resource "{resource_type}" "{name}" not found'
    return match.group()


def _variable_default(tf: str, name: str) -> str:
    """Return the ``default`` of one ``variable "<name>"`` block."""
    block = re.search(
        r'variable\s+"%s"\s*\{.*?^\}' % re.escape(name), tf, re.DOTALL | re.MULTILINE
    )
    assert block, f'variable "{name}" not found'
    default = re.search(r"^\s*default\s*=\s*(.+)$", block.group(), re.MULTILINE)
    assert default, f'variable "{name}" has no default'
    return default.group(1).strip()


def _terraform_rule_priorities() -> dict:
    """Parse the ``local.alb_priority`` map from public_alb_rules.tf."""
    tf = _read(PUBLIC_ALB_RULES_TF)
    block = re.search(r"alb_priority\s*=\s*\{(.*?)\n\s*\}", tf, re.DOTALL)
    assert block, "local.alb_priority map not found in public_alb_rules.tf"
    priorities = {
        name: int(value)
        for name, value in re.findall(
            r"^\s*(\w+)\s*=\s*(\d+)\s*$", block.group(1), re.MULTILINE
        )
    }
    assert priorities, "no named priorities parsed from local.alb_priority"
    return priorities


class TestAlbPriorityBandsAreDisjoint:
    """Premium ALB rules cannot collide with the static terraform rules.

    The premium band is [100, MAX_PREMIUM_PRIORITY] and is allocated by the
    Lambda at assignment time. The public and free bands are terraform. A
    collision means a premium user's dedicated rule either fails to create or
    displaces the rule that routes ``/api/public/*`` to the public tier.
    """

    def test_every_terraform_priority_sits_above_the_premium_band(self):
        priorities = _terraform_rule_priorities()
        colliding = {
            name: value
            for name, value in priorities.items()
            if value <= MAX_PREMIUM_PRIORITY
        }
        assert not colliding, (
            f"terraform ALB rule priorities inside the premium band "
            f"[{PREMIUM_BAND_START}, {MAX_PREMIUM_PRIORITY}]: {colliding}"
        )

    def test_terraform_priorities_are_unique(self):
        priorities = _terraform_rule_priorities()
        seen = {}
        for name, value in priorities.items():
            seen.setdefault(value, []).append(name)
        duplicates = {v: names for v, names in seen.items() if len(names) > 1}
        assert not duplicates, f"duplicate ALB rule priorities: {duplicates}"

    def test_every_declared_priority_is_referenced_by_a_rule(self):
        """An orphaned entry in the map is a rule someone deleted without
        freeing its number, which silently shrinks the usable band."""
        tf = _read(PUBLIC_ALB_RULES_TF)
        referenced = set(re.findall(r"local\.alb_priority\.(\w+)", tf))
        declared = set(_terraform_rule_priorities())
        assert declared == referenced, (
            f"declared but unused: {declared - referenced}; "
            f"used but undeclared: {referenced - declared}"
        )

    def test_allocator_refuses_to_leave_the_premium_band(self):
        """The cap is enforced, not merely declared.

        With every priority in the band taken, the allocator must raise rather
        than return the next integer - which would be the lowest terraform
        priority and would steal a public-tier route.
        """
        band_is_full = {
            "Rules": [
                {"Priority": str(p)}
                for p in range(PREMIUM_BAND_START, MAX_PREMIUM_PRIORITY + 1)
            ]
        }

        with pytest.raises(Exception) as excinfo:
            with _fake_elbv2_rules(band_is_full):
                get_next_available_priority(
                    "arn:aws:elasticloadbalancing:region:account:listener/test"
                )

        assert "No available ALB rule priorities" in str(excinfo.value)

    def test_the_bands_are_adjacent_so_exhaustion_is_the_only_guard(self):
        """There is no numeric buffer between the premium cap and the first
        terraform rule, which is why the exhaustion case above must raise
        rather than fall through to the next integer."""
        assert min(_terraform_rule_priorities().values()) == MAX_PREMIUM_PRIORITY + 1

    def test_allocator_returns_a_priority_inside_the_band(self):
        """Sanity companion to the exhaustion case: the happy path must stay
        in-band, so the exhaustion test above is not passing for the wrong
        reason."""
        one_taken = {"Rules": [{"Priority": "100"}, {"Priority": "default"}]}

        with _fake_elbv2_rules(one_taken):
            priority = get_next_available_priority(
                "arn:aws:elasticloadbalancing:region:account:listener/test"
            )

        assert PREMIUM_BAND_START <= priority <= MAX_PREMIUM_PRIORITY
        assert priority == 101


class TestPublishedDataEfsLifecycle:
    """The published-data EFS transitions to IA after 7 days.

    Configuration assertion. The deployed check (that AWS actually moved the
    objects) stays manual.
    """

    def test_published_data_transitions_to_ia_after_7_days(self):
        block = _resource_block(
            _read(INFRASTRUCTURE_TF), "aws_efs_file_system", "published_data"
        )
        match = re.search(
            r"lifecycle_policy\s*\{[^}]*?transition_to_ia\s*=\s*\"([^\"]+)\"", block
        )
        assert match, "no lifecycle_policy/transition_to_ia on the published_data EFS"
        assert match.group(1) == "AFTER_7_DAYS"


class TestPublicCleanupSchedule:
    """The input-cache cleanup Lambda runs once a day, and is wired.

    The cache is unbounded between runs, so a rule that is disabled or
    retargeted stops being visible until EFS fills up.
    """

    @pytest.fixture(autouse=True)
    def _load(self):
        self.tf = _read(PUBLIC_CLEANUP_TF)
        self.rule = _resource_block(
            self.tf, "aws_cloudwatch_event_rule", "public_cleanup_schedule"
        )

    def test_schedule_fires_once_a_day(self):
        match = re.search(r"schedule_expression\s*=\s*\"([^\"]+)\"", self.rule)
        assert match, "public_cleanup_schedule has no schedule_expression"
        expression = match.group(1)

        cron = re.fullmatch(r"cron\((.+)\)", expression)
        assert cron, f"expected a cron() schedule, got {expression!r}"

        minute, hour, day_of_month, month, day_of_week, year = cron.group(1).split()
        assert minute.isdigit(), f"minute {minute!r} is not a single fixed minute"
        assert hour.isdigit(), f"hour {hour!r} is not a single fixed hour"
        assert (day_of_month, month, year) == ("*", "*", "*")
        assert day_of_week == "?", f"day_of_week {day_of_week!r} restricts the days"

    def test_schedule_is_enabled(self):
        assert re.search(r'state\s*=\s*"ENABLED"', self.rule), (
            "public_cleanup_schedule is not ENABLED, so the input cache is "
            "never wiped"
        )

    def test_schedule_targets_the_cleanup_lambda_and_may_invoke_it(self):
        target = _resource_block(
            self.tf, "aws_cloudwatch_event_target", "public_cleanup_target"
        )
        assert "aws_lambda_function.public_cleanup.arn" in target

        permission = _resource_block(
            self.tf, "aws_lambda_permission", "allow_cloudwatch_public_cleanup"
        )
        assert "aws_lambda_function.public_cleanup.function_name" in permission
        assert (
            "aws_cloudwatch_event_rule.public_cleanup_schedule.arn" in permission
        ), "the rule cannot invoke the Lambda without a matching source_arn"


class TestPublicLogGroup:
    """The public log group's name and retention, and that the public
    container actually logs to it."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.tf = _read(PUBLIC_SERVICE_TF)
        self.log_group = _resource_block(
            self.tf, "aws_cloudwatch_log_group", "public_optinist"
        )

    def test_log_group_name(self):
        match = re.search(r'name\s*=\s*"([^"]+)"', self.log_group)
        assert match, "public log group has no name"
        assert match.group(1) == "/ecs/${var.environment}-public-optinist-cloud-taskdef"

    def test_log_group_retention_is_30_days(self):
        match = re.search(r"retention_in_days\s*=\s*(\d+)", self.log_group)
        assert match, "public log group has no retention_in_days"
        assert int(match.group(1)) == 30

    def test_public_task_logs_into_that_group(self):
        """A correct log group nothing writes to is the same as no log group."""
        task = _resource_block(self.tf, "aws_ecs_task_definition", "public")
        match = re.search(r'"awslogs-group"\s*=\s*([^\n]+?)\s*$', task, re.MULTILINE)
        assert match, "public task definition has no awslogs-group"
        assert match.group(1) == "aws_cloudwatch_log_group.public_optinist.name"


class TestPublicAsgCapacity:
    """The public ASG's min_size and desired_capacity.

    The public tier serves the SPA shell for every tier, including while free
    is stopped, so a single instance makes SPA delivery a single point of
    failure across two AZs.
    """

    @pytest.fixture(autouse=True)
    def _load(self):
        self.asg = _resource_block(
            _read(PUBLIC_SERVICE_TF), "aws_autoscaling_group", "public"
        )
        main_tf = _read(MAIN_TF)
        self.min_size = int(_variable_default(main_tf, "public_asg_min_size"))
        self.max_size = int(_variable_default(main_tf, "public_asg_max_size"))
        self.desired = int(_variable_default(main_tf, "public_asg_desired_capacity"))

    def test_asg_reads_the_public_capacity_variables(self):
        """Not the free tier's ``asg_*`` variables, and not a literal."""
        assert re.search(r"min_size\s*=\s*var\.public_asg_min_size", self.asg)
        assert re.search(r"max_size\s*=\s*var\.public_asg_max_size", self.asg)
        assert re.search(
            r"desired_capacity\s*=\s*var\.public_asg_desired_capacity", self.asg
        )

    def test_capacity_defaults_keep_two_azs_covered(self):
        assert self.min_size >= 2, (
            f"public_asg_min_size is {self.min_size}; the public tier spans two "
            f"subnets and serves the SPA for every tier"
        )
        assert self.min_size <= self.desired <= self.max_size

    def test_ecs_desired_count_tracks_the_asg(self):
        """One task per instance. If these desync, instances run no container
        and the target group reports them unhealthy."""
        service = _resource_block(_read(PUBLIC_SERVICE_TF), "aws_ecs_service", "public")
        assert re.search(
            r"desired_count\s*=\s*var\.public_asg_desired_capacity", service
        )


def _fake_elbv2_rules(describe_rules_response):
    """Patch the boto3 client premium_manager builds, returning fixed rules."""
    from unittest.mock import MagicMock, patch

    client = MagicMock()
    client.describe_rules.return_value = describe_rules_response
    return patch("premium_manager.boto3.client", return_value=client)

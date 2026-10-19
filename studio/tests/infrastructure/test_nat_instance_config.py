"""Configuration regression tests for the NAT instances in infrastructure.tf.

- The AMI is pinned per environment in an SSM parameter and resolved through
  an x86_64, Amazon-owned image lookup, so a bad pin fails the plan; nothing
  picks the newest image by itself.
- A replacement creates before it destroys, and only user_data is ignored.
- user_data does not run a dnf update, which on AL2023 is locked to the
  image's release.
- The freshness check has no scoped data source, which every plan would print.
"""

import re

import pytest
from terraform_source import (
    TERRAFORM_DIR,
    attribute,
    check_block,
    data_block,
    read,
    resource_block,
    string_list,
)

INFRASTRUCTURE_TF = TERRAFORM_DIR / "infrastructure.tf"
NAT_INSTANCES = ("nat", "nat2")


@pytest.fixture(scope="module")
def tf():
    return read(INFRASTRUCTURE_TF)


def _filter_values(block: str, name: str) -> str:
    """Return the ``values`` of the filter block named ``name``."""
    match = re.search(
        r'filter\s*\{\s*name\s*=\s*"%s"\s*values\s*=\s*(\[[^\]]*\])' % re.escape(name),
        block,
    )
    assert match, f'filter "{name}" not found'
    return match.group(1)


def _lifecycle(instance: str) -> str:
    match = re.search(r"lifecycle\s*\{[^}]*\}", instance)
    assert match, "lifecycle block not found"
    return match.group()


class TestNatAmiPin:
    """Every apply reads one pin per environment, and a bad pin fails the plan."""

    @pytest.mark.parametrize("name", NAT_INSTANCES)
    def test_both_nat_instances_use_the_resolved_pin(self, tf, name):
        instance = resource_block(tf, "aws_instance", name)
        assert attribute(instance, "ami") == "data.aws_ami.nat_pinned.id"

    def test_the_pin_is_an_ssm_parameter_per_environment(self, tf):
        """Not a tfvars entry: each engineer's tfvars copy could carry a
        different value, and a stale one replaces the NAT."""
        parameter = data_block(tf, "aws_ssm_parameter", "nat_ami_id")
        assert (
            attribute(parameter, "name") == '"/${var.environment}/optinist/nat-ami-id"'
        )

    def test_the_pin_is_resolved_to_an_amazon_x86_64_image(self, tf):
        """A cross-region, mistyped or arm64 id finds nothing and fails the
        plan, before the old NAT is destroyed."""
        pinned = data_block(tf, "aws_ami", "nat_pinned")
        assert string_list(attribute(pinned, "owners")) == ["amazon"]
        assert _filter_values(pinned, "image-id") == (
            "[data.aws_ssm_parameter.nat_ami_id.insecure_value]"
        )
        assert string_list(_filter_values(pinned, "architecture")) == ["x86_64"]

    def test_a_deprecated_pin_still_resolves(self, tf):
        """Amazon deprecates AL2023 images about 90 days after creation."""
        pinned = data_block(tf, "aws_ami", "nat_pinned")
        assert attribute(pinned, "include_deprecated") == "true"

    def test_nothing_picks_the_newest_image_by_itself(self, tf):
        """most_recent was how the NAT drifted to every new upstream image."""
        assert "most_recent" not in data_block(tf, "aws_ami", "nat_pinned")
        assert not re.search(r'^data\s+"aws_ami"\s+"nat_instance"', tf, re.MULTILINE)
        assert "var.nat_ami_id" not in tf


class TestNatReplacement:
    """How a pin bump replaces the NAT instances."""

    @pytest.mark.parametrize("name", NAT_INSTANCES)
    def test_a_replacement_creates_before_it_destroys(self, tf, name):
        """A failed create leaves the old NAT forwarding."""
        lifecycle = _lifecycle(resource_block(tf, "aws_instance", name))
        assert attribute(lifecycle, "create_before_destroy") == "true"

    @pytest.mark.parametrize("name", NAT_INSTANCES)
    def test_only_user_data_is_ignored(self, tf, name):
        """ami must not be ignored: a bump has to plan the replacement."""
        lifecycle = _lifecycle(resource_block(tf, "aws_instance", name))
        assert attribute(lifecycle, "ignore_changes") == "[user_data]"


class TestNatUserData:
    @pytest.fixture(autouse=True)
    def _load(self, tf):
        locals_match = re.search(
            r"nat_user_data\s*=\s*<<-EOF(.*?)^\s*EOF$", tf, re.DOTALL | re.MULTILINE
        )
        assert locals_match, "nat_user_data heredoc not found"
        self.user_data = locals_match.group(1)

    def test_no_dnf_update_on_first_boot(self):
        """On AL2023 dnf is locked to the image's release: the image is the
        patch level, and an update only adds a network dependency to boot."""
        assert "yum install -y iptables-nft" in self.user_data
        assert not re.search(r"\b(yum|dnf)\s+(update|upgrade)\b", self.user_data)

    def test_the_install_has_swap_before_it(self):
        """On a t3.nano the install was killed by the OOM killer while loading
        the repository metadata, and the NAT never forwarded."""
        swapon = self.user_data.index("swapon /swapfile")
        install = self.user_data.index("yum install -y iptables-nft")
        assert swapon < install

    def test_the_install_is_retried(self):
        assert re.search(
            r"for attempt in [^;]+; do\s+yum install -y iptables-nft && break",
            self.user_data,
        )


class TestNatAmiFreshness:
    def test_the_check_compares_the_pin_with_the_latest_image(self, tf):
        check = check_block(tf, "nat_ami_freshness")
        assert "data.aws_ami.nat_pinned.creation_date" in check
        assert "data.aws_ami.nat_ami_latest.creation_date" in check

    def test_the_check_has_no_scoped_data_source(self, tf):
        """A check block's own data source is printed in every plan."""
        assert not re.search(
            r"^\s+data\s+\"", check_block(tf, "nat_ami_freshness"), re.MULTILINE
        )

    def test_resource_blocks_do_not_swallow_the_check(self, tf):
        """The block before the check must end where the check starts."""
        block = data_block(tf, "aws_ami", "nat_ami_latest")
        assert 'check "nat_ami_freshness"' not in block
        assert "assert {" not in block

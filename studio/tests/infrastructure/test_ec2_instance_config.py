"""Configuration regression tests for the standalone EC2 instances.

An ``aws_instance`` that boots from a launch template takes ``user_data`` from
the template and does not declare it. The provider reads the value back into
state, and the attribute is not Computed, so unless the resource ignores it
every plan proposes removing it; applying that clears the bootstrap script and
stops and starts the instance. Every ``.tf`` file is read, so an instance added
later is covered without a change here.
"""

import re

import pytest
from terraform_source import (
    TERRAFORM_DIR,
    attribute,
    identifier_list,
    read,
    resource_blocks,
)


def _launch_template_instances() -> dict:
    """Return ``{name: source}`` for every aws_instance with a launch_template block."""
    instances = {}
    for path in sorted(TERRAFORM_DIR.glob("*.tf")):
        for name, block in resource_blocks(read(path), "aws_instance").items():
            if re.search(r"^\s*launch_template\s*\{", block, re.MULTILINE):
                instances[name] = block
    return instances


def _declares_user_data(block: str) -> bool:
    return bool(re.search(r"^\s*user_data(_base64)?\s*=", block, re.MULTILINE))


class TestLaunchTemplateInstancesLeaveUserDataToTheTemplate:
    """Every launch-template instance without its own ``user_data`` ignores it."""

    def test_the_known_instances_are_found(self):
        """Guards the generic case below against matching nothing."""
        assert {"background", "premium"} <= set(_launch_template_instances())

    @pytest.mark.parametrize("name", sorted(_launch_template_instances()))
    def test_instance_ignores_user_data_unless_it_declares_it(self, name):
        block = _launch_template_instances()[name]
        if _declares_user_data(block):
            pytest.skip(f"aws_instance.{name} declares user_data itself")
        assert re.search(r"^\s*ignore_changes\s*=", block, re.MULTILINE), (
            f"aws_instance.{name} takes user_data from its launch template "
            f"but has no ignore_changes"
        )
        ignored = identifier_list(attribute(block, "ignore_changes"))
        assert "user_data" in ignored, f"aws_instance.{name} ignores {ignored}"

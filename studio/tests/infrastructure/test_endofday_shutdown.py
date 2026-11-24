"""Tests for the capacity provider step of endofday_shutdown.

The step runs `aws ecs update-capacity-provider`. The CLI checks the parameters
against the ECS API model before sending, so one field the update does not
accept fails the whole call. The model bundled with botocore is that check.
"""

import sys
import types
from unittest.mock import patch

import botocore.session
from botocore.validate import validate_parameters

try:
    import colorama  # noqa: F401
except ImportError:
    # The test image has no colorama; the script uses it only for colours.
    _colorama = types.ModuleType("colorama")
    _colorama.Fore = types.SimpleNamespace(GREEN="", YELLOW="", RED="", RESET="")
    _colorama.init = lambda: None
    sys.modules["colorama"] = _colorama

import endofday_shutdown as shutdown  # noqa: E402


def _parse_shorthand(text: str) -> dict:
    """Parse the `key=value,key={...}` subset of AWS CLI shorthand."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])

    parsed = {}
    for part in parts:
        key, _, value = part.partition("=")
        if value.startswith("{") and value.endswith("}"):
            parsed[key] = _parse_shorthand(value[1:-1])
        else:
            parsed[key] = int(value) if value.isdigit() else value
    return parsed


def _sent_update_call() -> list[str]:
    """Run the step with the AWS CLI stubbed; return the update command."""
    calls = []

    def fake_run_command(cmd, shell=False, check=True):
        calls.append(cmd)
        # Answers the existence check and reports the update as done.
        return "subscr-optinist-capacity-provider"

    with patch.object(shutdown, "run_command", side_effect=fake_run_command):
        assert shutdown.disable_capacity_provider_managed_scaling() is True

    updates = [c for c in calls if "update-capacity-provider" in c]
    assert len(updates) == 1
    return updates[0]


class TestDisableCapacityProviderManagedScaling:
    def test_update_parameters_pass_the_ecs_model(self):
        cmd = _sent_update_call()
        provider = _parse_shorthand(cmd[cmd.index("--auto-scaling-group-provider") + 1])
        op = (
            botocore.session.get_session()
            .get_service_model("ecs")
            .operation_model("UpdateCapacityProvider")
        )

        # Raises ParamValidationError on a field or type the update rejects.
        validate_parameters(
            {
                "name": cmd[cmd.index("--name") + 1],
                "autoScalingGroupProvider": provider,
            },
            op.input_shape,
        )
        # The parse reached the nested settings, so they were checked too.
        assert provider["managedScaling"]["status"] == "DISABLED"

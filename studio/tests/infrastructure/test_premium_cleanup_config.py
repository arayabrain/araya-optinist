"""Configuration regression tests for the Premium Cleanup Lambda.

premium_cleanup.py skips deleting the shared autoscaling target group by
comparing against AUTOSCALING_TARGET_GROUP_ARN; unset, that guard compares
against None and never matches.
"""

from terraform_source import TERRAFORM_DIR, attribute, read, resource_block

PREMIUM_MANAGER_TF = TERRAFORM_DIR / "premium_manager.tf"


def test_cleanup_lambda_gets_the_same_autoscaling_target_group_as_the_manager():
    tf = read(PREMIUM_MANAGER_TF)
    cleanup = resource_block(tf, "aws_lambda_function", "premium_cleanup")
    manager = resource_block(tf, "aws_lambda_function", "premium_manager")

    expected = "aws_lb_target_group.autoscaling.arn"
    assert attribute(manager, "AUTOSCALING_TARGET_GROUP_ARN") == expected
    assert attribute(cleanup, "AUTOSCALING_TARGET_GROUP_ARN") == expected

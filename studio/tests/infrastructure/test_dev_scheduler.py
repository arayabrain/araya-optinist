"""Tests for the dev_scheduler Lambda.

The Lambda's boto3 clients are module-level globals; the `ds` fixture imports
the module once and replaces each client with a MagicMock for the duration of
the test. No real AWS calls are ever made.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# Env vars dev_scheduler reads. Set as a module-level dict so every fixture
# patch starts from the same baseline; individual tests override as needed.
SCHEDULER_ENV = {
    "AWS_DEFAULT_REGION": "ap-northeast-1",
    "NAT_INSTANCE_ID": "i-nat",
    "BACKGROUND_INSTANCE_ID": "i-bg",
    "PREMIUM_INSTANCE_IDS": "i-prem1,i-prem2",
    "RDS_INSTANCE_ID": "test-rds",
    "RDS_SNAPSHOT_ID": "test-snap",
    "RDS_INSTANCE_CLASS": "db.t4g.small",
    "RDS_SUBNET_GROUP_NAME": "test-subnet-group",
    "RDS_SECURITY_GROUP_IDS": "sg-1,sg-2",
    "RDS_PARAMETER_GROUP_NAME": "test-pg",
    "RDS_PROXY_NAME": "test-proxy",
    "ASG_NAME": "test-asg",
    "ASG_MIN_SIZE": "1",
    "ASG_DESIRED_CAPACITY": "1",
    "ASG_MAX_SIZE": "2",
    "CLUSTER_NAME": "test-cluster",
    "ECS_SERVICE_NAMES": json.dumps(["svc-a", "svc-b"]),
    "PUBLIC_ASG_NAME": "test-public-asg",
    "PUBLIC_ASG_MIN_SIZE": "2",
    "PUBLIC_ASG_MAX_SIZE": "4",
    "PUBLIC_ASG_DESIRED_CAPACITY": "2",
    "PUBLIC_ECS_SERVICE_NAME": "svc-public",
    "SCHEDULE_RULE_NAMES": json.dumps(["rule-a"]),
    "DELAYED_RULE_NAMES": json.dumps(["delayed-a"]),
    "ALARM_PREFIX": "test-",
    "OVERRIDE_PARAM_NAME": "/test/override",
    "PREMIUM_MANAGER_FUNCTION_NAME": "test-premium-manager",
    "DEFAULT_STOP_MODE": "destroy",
}


# Stub exception classes — boto3 generates rds.exceptions.X dynamically, so
# replacing rds with a MagicMock loses them. Tests that need to simulate
# "not found" use these via clients["rds"].exceptions.<Class>.
class _DBInstanceNotFoundFault(Exception):
    pass


class _DBSnapshotNotFoundFault(Exception):
    pass


class _ParameterNotFound(Exception):
    pass


@pytest.fixture
def ds(monkeypatch):
    """Import dev_scheduler with all boto3 clients mocked.

    Returns a (module, clients) tuple where clients is a dict keyed by
    attribute name on the module ("rds", "ec2", "ecs", ...). Tests configure
    the mocks via clients["rds"].describe_db_instances.return_value = ...,
    then assert on the helper's return value or call history.
    """
    with patch.dict("os.environ", SCHEDULER_ENV, clear=False):
        import dev_scheduler  # noqa: PLC0415  — must be inside env patch

        clients = {
            "rds": MagicMock(name="rds"),
            "ec2": MagicMock(name="ec2"),
            "ecs": MagicMock(name="ecs"),
            "autoscaling": MagicMock(name="autoscaling"),
            "events": MagicMock(name="events"),
            "cloudwatch": MagicMock(name="cloudwatch"),
            "ssm": MagicMock(name="ssm"),
            "lambda_client": MagicMock(name="lambda_client"),
        }

        # Wire up exception classes so `except rds.exceptions.X:` works.
        clients["rds"].exceptions.DBInstanceNotFoundFault = _DBInstanceNotFoundFault
        clients["rds"].exceptions.DBSnapshotNotFoundFault = _DBSnapshotNotFoundFault
        clients["ssm"].exceptions.ParameterNotFound = _ParameterNotFound

        for name, mock in clients.items():
            monkeypatch.setattr(dev_scheduler, name, mock)

        # Make retries and polling instant.
        monkeypatch.setattr(dev_scheduler.time, "sleep", lambda *_: None)

        yield dev_scheduler, clients


# ---------------------------------------------------------------------------
# Helpers used by several tests.
# ---------------------------------------------------------------------------


def _ec2_response(state):
    """Build a describe_instances response with one instance in `state`."""
    return {"Reservations": [{"Instances": [{"State": {"Name": state}}]}]}


def _rds_response(status):
    """Build a describe_db_instances response with one instance in `status`."""
    return {"DBInstances": [{"DBInstanceStatus": status}]}


# ===========================================================================
# handler dispatch + input validation
# ===========================================================================


class TestHandler:
    def test_no_action_returns_400(self, ds):
        module, _ = ds
        result = module.handler({}, MagicMock())
        assert result["statusCode"] == 400

    def test_unknown_action_returns_400(self, ds):
        module, _ = ds
        result = module.handler({"action": "explode"}, MagicMock())
        assert result["statusCode"] == 400

    def test_start_dispatches_to_start_environment(self, ds):
        module, _ = ds
        with patch.object(module, "start_environment") as mock_start:
            mock_start.return_value = {"statusCode": 200}
            module.handler({"action": "start"}, MagicMock())
            mock_start.assert_called_once_with()

    def test_stop_default_mode_dispatches_with_destroy(self, ds):
        module, _ = ds
        with patch.object(module, "stop_environment") as mock_stop:
            mock_stop.return_value = {"statusCode": 200}
            module.handler({"action": "stop"}, MagicMock())
            mock_stop.assert_called_once_with(stop_mode="destroy")

    def test_stop_with_explicit_stop_mode(self, ds):
        module, _ = ds
        with patch.object(module, "stop_environment") as mock_stop:
            mock_stop.return_value = {"statusCode": 200}
            module.handler({"action": "stop", "stop_mode": "stop"}, MagicMock())
            mock_stop.assert_called_once_with(stop_mode="stop")

    def test_stop_unknown_mode_returns_400(self, ds):
        module, _ = ds
        result = module.handler({"action": "stop", "stop_mode": "nuke"}, MagicMock())
        assert result["statusCode"] == 400

    # ---- override input validation ----------------------------------------

    def test_override_default_hours(self, ds):
        module, _ = ds
        with patch.object(module, "set_override") as mock_set:
            module.handler({"action": "override"}, MagicMock())
            mock_set.assert_called_once_with(4)

    def test_override_explicit_hours_clamped_to_max(self, ds):
        module, _ = ds
        with patch.object(module, "set_override") as mock_set:
            module.handler({"action": "override", "hours": 999}, MagicMock())
            mock_set.assert_called_once_with(module.MAX_OVERRIDE_HOURS)

    def test_override_negative_hours_clamped_to_one(self, ds):
        module, _ = ds
        with patch.object(module, "set_override") as mock_set:
            module.handler({"action": "override", "hours": -5}, MagicMock())
            mock_set.assert_called_once_with(1)

    def test_override_null_hours_uses_default(self, ds):
        """`{"hours": null}` must not crash with TypeError."""
        module, _ = ds
        with patch.object(module, "set_override") as mock_set:
            module.handler({"action": "override", "hours": None}, MagicMock())
            mock_set.assert_called_once_with(4)

    def test_override_string_hours_returns_400(self, ds):
        """Non-integer string must return 400, not crash on str/int compare."""
        module, _ = ds
        result = module.handler({"action": "override", "hours": "abc"}, MagicMock())
        assert result["statusCode"] == 400

    def test_action_null_returns_400(self, ds):
        """`{"action": null}` must not crash on None.strip()."""
        module, _ = ds
        result = module.handler({"action": None}, MagicMock())
        assert result["statusCode"] == 400

    def test_whitespace_only_action_returns_400(self, ds):
        """`{"action": "   "}` strips to empty string → no action."""
        module, _ = ds
        result = module.handler({"action": "   "}, MagicMock())
        assert result["statusCode"] == 400

    def test_action_non_string_returns_400(self, ds):
        """`{"action": 123}` must be coerced and rejected, not crash on
        int.strip(). Pins the str() coercion in handler."""
        module, _ = ds
        result = module.handler({"action": 123}, MagicMock())
        assert result["statusCode"] == 400

    def test_stop_mode_non_string_returns_400(self, ds):
        """`{"action":"stop","stop_mode": 0}` — non-string truthy stop_mode
        must be coerced to a string and rejected by the tuple check."""
        module, _ = ds
        result = module.handler({"action": "stop", "stop_mode": 0}, MagicMock())
        assert result["statusCode"] == 400

    def test_override_zero_hours_clamped_to_one(self, ds):
        """`{"hours": 0}` must clamp to 1, NOT fall through to the default
        of 4. The previous `int(... or 4)` treated 0 as falsy."""
        module, _ = ds
        with patch.object(module, "set_override") as mock_set:
            module.handler({"action": "override", "hours": 0}, MagicMock())
            mock_set.assert_called_once_with(1)


# ===========================================================================
# with_retry plumbing
# ===========================================================================


class TestWithRetry:
    def test_returns_first_success(self, ds):
        module, _ = ds
        fn = MagicMock(return_value="ok")
        assert module.with_retry(fn, "arg") == "ok"
        assert fn.call_count == 1

    def test_retries_until_success(self, ds):
        module, _ = ds
        fn = MagicMock(side_effect=["error: nope", "ok"])
        fn.__name__ = "fn"
        assert module.with_retry(fn) == "ok"
        assert fn.call_count == 2

    def test_returns_last_error_after_max_attempts(self, ds):
        module, _ = ds
        fn = MagicMock(side_effect=["error: a", "error: b", "error: c"])
        fn.__name__ = "fn"
        assert module.with_retry(fn) == "error: c"
        assert fn.call_count == module.MAX_RETRY_ATTEMPTS

    def test_max_attempts_kwarg(self, ds):
        module, _ = ds
        fn = MagicMock(return_value="error: x")
        fn.__name__ = "fn"
        module.with_retry(fn, max_attempts=1)
        assert fn.call_count == 1

    def test_dict_result_treated_as_success(self, ds):
        """`str(dict)` starts with `{`, not "error" — must not retry."""
        module, _ = ds
        fn = MagicMock(return_value={"ok": True})
        assert module.with_retry(fn) == {"ok": True}
        assert fn.call_count == 1

    def test_propagates_exceptions_from_fn(self, ds):
        """Pins the contract that with_retry does NOT catch raised
        exceptions — helpers must return "error: ..." strings themselves."""
        module, _ = ds
        fn = MagicMock(side_effect=RuntimeError("uncaught"))
        fn.__name__ = "fn"
        with pytest.raises(RuntimeError, match="uncaught"):
            module.with_retry(fn)
        assert fn.call_count == 1  # no retry on raised exceptions


# ===========================================================================
# start_instance / stop_instance
# ===========================================================================


class TestStartInstance:
    def test_already_running(self, ds):
        module, clients = ds
        clients["ec2"].describe_instances.return_value = _ec2_response("running")
        assert module.start_instance("i-x", "Test") == "already_running"
        clients["ec2"].start_instances.assert_not_called()

    def test_starts_when_stopped(self, ds):
        module, clients = ds
        clients["ec2"].describe_instances.return_value = _ec2_response("stopped")
        assert module.start_instance("i-x", "Test") == "starting"
        clients["ec2"].start_instances.assert_called_once_with(InstanceIds=["i-x"])

    def test_terminated_returns_error(self, ds):
        module, clients = ds
        clients["ec2"].describe_instances.return_value = _ec2_response("terminated")
        result = module.start_instance("i-x", "Test")
        assert result.startswith("error:")
        clients["ec2"].start_instances.assert_not_called()

    def test_not_found_when_empty_reservations(self, ds):
        """Empty Reservations must return not_found, not raise IndexError."""
        module, clients = ds
        clients["ec2"].describe_instances.return_value = {"Reservations": []}
        assert module.start_instance("i-gone", "Premium") == "not_found"
        clients["ec2"].start_instances.assert_not_called()

    def test_not_found_when_reservation_has_no_instances(self, ds):
        module, clients = ds
        clients["ec2"].describe_instances.return_value = {
            "Reservations": [{"Instances": []}]
        }
        assert module.start_instance("i-gone", "Premium") == "not_found"

    def test_aws_exception_returns_error(self, ds):
        module, clients = ds
        clients["ec2"].describe_instances.side_effect = RuntimeError("boom")
        result = module.start_instance("i-x", "Test")
        assert result.startswith("error:")


class TestStopInstance:
    def test_stops_when_running(self, ds):
        module, clients = ds
        clients["ec2"].describe_instances.return_value = _ec2_response("running")
        assert module.stop_instance("i-x", "Test") == "stopping"
        clients["ec2"].stop_instances.assert_called_once_with(InstanceIds=["i-x"])

    def test_already_stopped(self, ds):
        module, clients = ds
        clients["ec2"].describe_instances.return_value = _ec2_response("stopped")
        assert module.stop_instance("i-x", "Test") == "already_stopped"
        clients["ec2"].stop_instances.assert_not_called()

    def test_already_terminated(self, ds):
        module, clients = ds
        clients["ec2"].describe_instances.return_value = _ec2_response("terminated")
        assert module.stop_instance("i-x", "Test") == "already_terminated"

    def test_not_found_when_empty_reservations(self, ds):
        """Empty Reservations must return not_found on stop_instance too."""
        module, clients = ds
        clients["ec2"].describe_instances.return_value = {"Reservations": []}
        assert module.stop_instance("i-gone", "Premium") == "not_found"
        clients["ec2"].stop_instances.assert_not_called()


# ===========================================================================
# RDS lifecycle helpers
# ===========================================================================


class TestRestoreRds:
    @pytest.fixture
    def rds_config(self):
        return {
            "instance_class": "db.t4g.small",
            "subnet_group": "sg",
            "security_group_ids": ["sg-1"],
            "parameter_group": "pg",
        }

    def test_already_exists_when_available(self, ds, rds_config):
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        assert module.restore_rds("rds-x", "snap-x", rds_config) == "already_exists"
        clients["rds"].restore_db_instance_from_db_snapshot.assert_not_called()

    def test_starts_when_stopped(self, ds, rds_config):
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("stopped")
        assert module.restore_rds("rds-x", "snap-x", rds_config) == "starting_existing"
        clients["rds"].start_db_instance.assert_called_once_with(
            DBInstanceIdentifier="rds-x"
        )

    def test_returns_error_when_status_deleting(self, ds, rds_config):
        """Cross-mode race — don't restore on top of an in-progress delete."""
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("deleting")
        result = module.restore_rds("rds-x", "snap-x", rds_config)
        assert result.startswith("error:")
        clients["rds"].restore_db_instance_from_db_snapshot.assert_not_called()
        clients["rds"].start_db_instance.assert_not_called()

    def test_restores_when_instance_not_found(self, ds, rds_config):
        module, clients = ds
        clients["rds"].describe_db_instances.side_effect = _DBInstanceNotFoundFault()
        clients["rds"].describe_db_snapshots.return_value = {
            "DBSnapshots": [{"Status": "available"}]
        }
        assert module.restore_rds("rds-x", "snap-x", rds_config) == "restoring"
        clients["rds"].restore_db_instance_from_db_snapshot.assert_called_once()

    def test_snapshot_not_found_returns_error(self, ds, rds_config):
        module, clients = ds
        clients["rds"].describe_db_instances.side_effect = _DBInstanceNotFoundFault()
        clients["rds"].describe_db_snapshots.side_effect = _DBSnapshotNotFoundFault()
        result = module.restore_rds("rds-x", "snap-x", rds_config)
        assert result.startswith("error:")

    def test_snapshot_waiter_timeout_returns_error(self, ds, rds_config):
        """Snapshot exists but is in 'creating'; waiter exceeds MaxAttempts."""
        module, clients = ds
        clients["rds"].describe_db_instances.side_effect = _DBInstanceNotFoundFault()
        clients["rds"].describe_db_snapshots.return_value = {
            "DBSnapshots": [{"Status": "creating"}]
        }
        waiter = MagicMock()
        waiter.wait.side_effect = RuntimeError("WaiterError: timed out")
        clients["rds"].get_waiter.return_value = waiter
        result = module.restore_rds("rds-x", "snap-x", rds_config)
        assert result.startswith("error:")
        clients["rds"].restore_db_instance_from_db_snapshot.assert_not_called()


class TestDestroyRds:
    def test_already_deleted_when_not_found(self, ds):
        module, clients = ds
        clients["rds"].describe_db_instances.side_effect = _DBInstanceNotFoundFault()
        assert module.destroy_rds("rds-x", "snap-x") == "already_deleted"
        clients["rds"].delete_db_instance.assert_not_called()

    def test_already_deleting_short_circuits(self, ds):
        """Re-running destroy mid-delete must short-circuit, not call AWS."""
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("deleting")
        assert module.destroy_rds("rds-x", "snap-x") == "already_deleting"
        clients["rds"].delete_db_instance.assert_not_called()
        clients["rds"].delete_db_snapshot.assert_not_called()

    def test_deletes_old_snapshot_then_instance(self, ds):
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        clients["rds"].describe_db_snapshots.return_value = {
            "DBSnapshots": [{"Status": "available"}]
        }
        clients["rds"].get_waiter.return_value = MagicMock()
        assert module.destroy_rds("rds-x", "snap-x") == "deleting"
        clients["rds"].delete_db_snapshot.assert_called_once_with(
            DBSnapshotIdentifier="snap-x"
        )
        clients["rds"].delete_db_instance.assert_called_once()

    def test_no_old_snapshot_skips_snapshot_delete(self, ds):
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        clients["rds"].describe_db_snapshots.side_effect = _DBSnapshotNotFoundFault()
        assert module.destroy_rds("rds-x", "snap-x") == "deleting"
        clients["rds"].delete_db_snapshot.assert_not_called()
        clients["rds"].delete_db_instance.assert_called_once()

    def test_delete_db_instance_called_with_final_snapshot(self, ds):
        """The final snapshot identifier must be passed and
        DeleteAutomatedBackups must be False — losing either would cause
        silent data destruction on every weeknight stop."""
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        clients["rds"].describe_db_snapshots.side_effect = _DBSnapshotNotFoundFault()
        module.destroy_rds("rds-x", "snap-x")
        clients["rds"].delete_db_instance.assert_called_once_with(
            DBInstanceIdentifier="rds-x",
            FinalDBSnapshotIdentifier="snap-x",
            DeleteAutomatedBackups=False,
        )

    def test_snapshot_delete_short_circuits_when_already_deleting(self, ds):
        """Symmetric to the instance `deleting` short-circuit. If a prior
        attempt's waiter timed out and with_retry re-entered destroy_rds,
        the snapshot may still be in `deleting` — re-issuing
        delete_db_snapshot would raise InvalidDBSnapshotState. The helper
        must skip the delete call and just wait."""
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        clients["rds"].describe_db_snapshots.return_value = {
            "DBSnapshots": [{"Status": "deleting"}]
        }
        clients["rds"].get_waiter.return_value = MagicMock()
        assert module.destroy_rds("rds-x", "snap-x") == "deleting"
        clients["rds"].delete_db_snapshot.assert_not_called()
        # Instance delete still proceeds.
        clients["rds"].delete_db_instance.assert_called_once()

    def test_retry_after_partial_failure_short_circuits(self, ds):
        """First attempt deletes the old snapshot then delete_db_instance
        fails (e.g. transient throttle). On retry, status
        is "deleting" and the helper must short-circuit rather than call
        delete_db_instance again."""
        module, clients = ds
        # attempt 1: available → proceeds to delete_db_instance (which fails)
        # attempt 2: deleting  → short-circuits to already_deleting
        clients["rds"].describe_db_instances.side_effect = [
            _rds_response("available"),
            _rds_response("deleting"),
        ]
        clients["rds"].describe_db_snapshots.side_effect = _DBSnapshotNotFoundFault()
        clients["rds"].delete_db_instance.side_effect = [
            RuntimeError("throttled"),
        ]
        result = module.with_retry(module.destroy_rds, "rds-x", "snap-x")
        assert result == "already_deleting"
        # delete_db_instance was only called once — second attempt
        # short-circuited before reaching it.
        assert clients["rds"].delete_db_instance.call_count == 1


class TestStopRds:
    def test_not_found(self, ds):
        module, clients = ds
        clients["rds"].describe_db_instances.side_effect = _DBInstanceNotFoundFault()
        assert module.stop_rds("rds-x") == "not_found"

    def test_already_stopped(self, ds):
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("stopped")
        assert module.stop_rds("rds-x") == "already_stopped"
        clients["rds"].stop_db_instance.assert_not_called()

    def test_stops_when_available(self, ds):
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        assert module.stop_rds("rds-x") == "stopping"
        clients["rds"].stop_db_instance.assert_called_once_with(
            DBInstanceIdentifier="rds-x"
        )

    def test_cannot_stop_when_creating(self, ds):
        module, clients = ds
        clients["rds"].describe_db_instances.return_value = _rds_response("creating")
        assert module.stop_rds("rds-x").startswith("error:")
        clients["rds"].stop_db_instance.assert_not_called()


# ===========================================================================
# ensure_rds_proxy_target
# ===========================================================================


class TestEnsureRdsProxyTarget:
    def test_skipped_when_no_proxy_env_var(self, ds, monkeypatch):
        module, clients = ds
        monkeypatch.delenv("RDS_PROXY_NAME", raising=False)
        assert module.ensure_rds_proxy_target("rds-x") == "skipped_no_proxy"
        clients["rds"].describe_db_proxy_targets.assert_not_called()

    def test_already_registered_fast_path(self, ds):
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.return_value = {
            "Targets": [{"RdsResourceId": "rds-x"}]
        }
        assert module.ensure_rds_proxy_target("rds-x") == "already_registered"
        clients["rds"].describe_db_instances.assert_not_called()
        clients["rds"].register_db_proxy_targets.assert_not_called()

    def test_registers_when_immediately_available(self, ds):
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.return_value = {"Targets": []}
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        assert module.ensure_rds_proxy_target("rds-x") == "registered"
        clients["rds"].register_db_proxy_targets.assert_called_once_with(
            DBProxyName="test-proxy", DBInstanceIdentifiers=["rds-x"]
        )

    def test_polls_through_creating_to_available(self, ds, monkeypatch):
        """Happy path: poll a transient state and register once available."""
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.return_value = {"Targets": []}
        clients["rds"].describe_db_instances.side_effect = [
            _rds_response("creating"),
            _rds_response("creating"),
            _rds_response("available"),
        ]
        # Time stays well within deadline.
        ticks = iter([0, 1, 2, 3, 4])
        monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
        assert module.ensure_rds_proxy_target("rds-x") == "registered"
        assert clients["rds"].describe_db_instances.call_count == 3
        clients["rds"].register_db_proxy_targets.assert_called_once()

    def test_returns_deferred_when_persistently_creating(self, ds, monkeypatch):
        """Instance still transient past deadline → soft skip."""
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.return_value = {"Targets": []}
        clients["rds"].describe_db_instances.return_value = _rds_response("creating")
        # First monotonic() sets the deadline; second jumps past it.
        ticks = iter([0, 9999])
        monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
        assert module.ensure_rds_proxy_target("rds-x") == "deferred_still_creating"
        clients["rds"].register_db_proxy_targets.assert_not_called()

    @pytest.mark.parametrize(
        "broken_status",
        [
            "failed",
            "incompatible-parameters",
            "incompatible-restore",
            "storage-full",
            "inaccessible-encryption-credentials",
        ],
    )
    def test_non_transient_states_return_error(self, ds, broken_status):
        """Permanent broken states must error (page), not soft-skip."""
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.return_value = {"Targets": []}
        clients["rds"].describe_db_instances.return_value = _rds_response(broken_status)
        result = module.ensure_rds_proxy_target("rds-x")
        assert result.startswith("error:")
        assert broken_status in result
        clients["rds"].register_db_proxy_targets.assert_not_called()

    def test_returns_error_when_instance_not_found(self, ds):
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.return_value = {"Targets": []}
        clients["rds"].describe_db_instances.side_effect = _DBInstanceNotFoundFault()
        result = module.ensure_rds_proxy_target("rds-x")
        assert result.startswith("error:")
        assert "not found" in result

    def test_register_call_failure_returns_error(self, ds):
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.return_value = {"Targets": []}
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        clients["rds"].register_db_proxy_targets.side_effect = RuntimeError("throttled")
        result = module.ensure_rds_proxy_target("rds-x")
        assert result.startswith("error:")

    def test_describe_proxy_targets_failure_returns_error(self, ds):
        """If the proxy itself doesn't exist (e.g. terraform drift between
        env vars and reality), describe_db_proxy_targets raises. The outer
        try/except must catch it and return an error string."""
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.side_effect = RuntimeError(
            "DBProxyNotFoundFault: test-proxy"
        )
        result = module.ensure_rds_proxy_target("rds-x")
        assert result.startswith("error:")
        clients["rds"].register_db_proxy_targets.assert_not_called()

    def test_fast_path_walks_marker_pagination(self, ds):
        """Target on a later page must be found by the fast path. A naïve
        single-call check would miss it and unnecessarily re-register
        (or, worse, fail to detect an already-registered target)."""
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.side_effect = [
            {"Targets": [{"RdsResourceId": "other-rds"}], "Marker": "page2"},
            {"Targets": [{"RdsResourceId": "rds-x"}]},
        ]
        assert module.ensure_rds_proxy_target("rds-x") == "already_registered"
        assert clients["rds"].describe_db_proxy_targets.call_count == 2
        clients["rds"].register_db_proxy_targets.assert_not_called()

    def test_transitions_from_creating_to_failed_mid_poll(self, ds, monkeypatch):
        """A transition into a permanent broken state during polling must
        error, not defer. Pins non-transient check above deadline check."""
        module, clients = ds
        clients["rds"].describe_db_proxy_targets.return_value = {"Targets": []}
        clients["rds"].describe_db_instances.side_effect = [
            _rds_response("creating"),
            _rds_response("creating"),
            _rds_response("failed"),  # transition to broken state
        ]
        ticks = iter([0, 1, 2, 3, 4, 5])
        monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
        result = module.ensure_rds_proxy_target("rds-x")
        assert result.startswith("error:")
        assert "failed" in result
        clients["rds"].register_db_proxy_targets.assert_not_called()


# ===========================================================================
# start_environment
# ===========================================================================


class TestStartEnvironment:
    @pytest.fixture
    def patched(self, ds, monkeypatch):
        """Patch every helper start_environment calls so the orchestration
        flow can be exercised in isolation. Tests override individual mocks
        as needed."""
        module, clients = ds

        # NAT pre-check (verify-start detection): default = NAT stopped.
        clients["ec2"].describe_instances.return_value = _ec2_response("stopped")

        helpers = {
            "start_instance": MagicMock(return_value="starting"),
            "restore_rds": MagicMock(return_value="restoring"),
            "ensure_rds_proxy_target": MagicMock(return_value="registered"),
            "scale_asg": MagicMock(return_value="min=1,desired=1,max=2"),
            "update_ecs_services": MagicMock(
                return_value={"ecs_svc-a": "desired_count=1"}
            ),
            "toggle_event_rules": MagicMock(return_value={"enable_rule-a": "ok"}),
            "toggle_alarm_actions": MagicMock(return_value="enabled_5_alarms"),
            "clear_override": MagicMock(),
        }
        for name, mock in helpers.items():
            mock.__name__ = name  # with_retry logs fn.__name__ on retries
            monkeypatch.setattr(module, name, mock)
        return module, clients, helpers

    def test_proxy_registration_runs_on_already_exists(self, patched):
        """When restore_rds returns "already_exists", proxy registration
        must still run — the proxy may have been deregistered by a prior
        destroy stop."""
        module, _, helpers = patched
        helpers["restore_rds"].return_value = "already_exists"
        result = module.start_environment()
        helpers["ensure_rds_proxy_target"].assert_called_once()
        assert result["statusCode"] == 200

    def test_proxy_registration_runs_on_restoring(self, patched):
        module, _, helpers = patched
        helpers["restore_rds"].return_value = "restoring"
        module.start_environment()
        helpers["ensure_rds_proxy_target"].assert_called_once()

    def test_proxy_registration_skipped_when_rds_errored(self, patched):
        module, _, helpers = patched
        helpers["restore_rds"].return_value = "error: snapshot_not_found"
        with pytest.raises(RuntimeError):
            module.start_environment()
        helpers["ensure_rds_proxy_target"].assert_not_called()

    def test_premium_not_found_does_not_raise(self, patched):
        """A stale premium ID returning 'not_found' must not fail the run."""
        module, _, helpers = patched

        def fake_start_instance(instance_id, label):
            if label == "Premium":
                return "not_found"
            return "starting"

        helpers["start_instance"].side_effect = fake_start_instance
        result = module.start_environment()
        assert result["statusCode"] == 200
        # And proxy registration still happened.
        helpers["ensure_rds_proxy_target"].assert_called_once()

    def test_raises_runtime_error_on_failed_step(self, patched):
        module, _, helpers = patched
        helpers["scale_asg"].return_value = "error: capacity exceeded"
        with pytest.raises(RuntimeError):
            module.start_environment()

    def test_is_verify_false_when_nat_stopped(self, patched):
        """Initial start: NAT was stopped before invocation. Delayed rules
        should not be enabled."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("stopped")
        module.start_environment()
        # toggle_event_rules called once (for non-delayed SCHEDULE_RULE_NAMES),
        # NOT a second time for DELAYED_RULE_NAMES.
        assert helpers["toggle_event_rules"].call_count == 1

    def test_is_verify_true_when_nat_running_and_rds_available(self, patched):
        """Verify-start: NAT running AND RDS already available.
        Delayed rules get enabled."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("running")
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        module.start_environment()
        assert helpers["toggle_event_rules"].call_count == 2

    def test_is_verify_false_when_nat_running_but_rds_not_available(self, patched):
        """Operator manually started NAT but RDS hasn't been restored yet.
        Must be treated as initial start, NOT verify-start — otherwise
        delayed rules fire prematurely and a deferred proxy gets paged."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("running")
        clients["rds"].describe_db_instances.side_effect = _DBInstanceNotFoundFault()
        module.start_environment()
        # Only the non-delayed rules call — delayed rules NOT enabled.
        assert helpers["toggle_event_rules"].call_count == 1

    def test_nat_pending_does_not_set_is_verify(self, patched):
        """KNOWN LIMITATION: if a verify-start invocation lands while NAT
        is still in `pending` (slow start path), is_verify stays False and
        a `deferred_still_creating` proxy result is NOT promoted to an
        error this cycle. The next +15 min verify gets another chance.
        Pinned so a future tightening of verify detection has to update
        this test deliberately."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("pending")
        helpers["ensure_rds_proxy_target"].return_value = "deferred_still_creating"
        # Should NOT raise (deferred not promoted) and delayed rules
        # should NOT be enabled.
        result = module.start_environment()
        assert result["statusCode"] == 200
        assert helpers["toggle_event_rules"].call_count == 1

    def test_is_verify_false_when_nat_running_but_rds_creating(self, patched):
        """Same theme: NAT running but RDS still in a transient state →
        not a verify-start."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("running")
        clients["rds"].describe_db_instances.return_value = _rds_response("creating")
        module.start_environment()
        assert helpers["toggle_event_rules"].call_count == 1

    def test_proxy_deferred_kept_on_initial_start(self, patched):
        """Initial start: deferred is acceptable, no RuntimeError."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("stopped")
        helpers["ensure_rds_proxy_target"].return_value = "deferred_still_creating"
        result = module.start_environment()
        assert result["statusCode"] == 200
        assert result["results"]["rds_proxy"] == "deferred_still_creating"

    def test_proxy_deferred_promoted_to_error_on_verify(self, patched):
        """A deferred result at verify-start must be promoted to an error."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("running")
        clients["rds"].describe_db_instances.return_value = _rds_response("available")
        helpers["ensure_rds_proxy_target"].return_value = "deferred_still_creating"
        with pytest.raises(RuntimeError) as exc_info:
            module.start_environment()
        assert "verify-start" in str(exc_info.value)

    def test_clear_override_called_even_on_failed_start(self, patched):
        """clear_override() must run even when a step returns an error —
        otherwise the override flag survives and skips the next scheduled
        stop indefinitely."""
        module, _, helpers = patched
        helpers["scale_asg"].return_value = "error: capacity"
        with pytest.raises(RuntimeError):
            module.start_environment()
        helpers["clear_override"].assert_called_once()

    def test_clear_override_called_when_helper_raises(self, patched):
        """Same guarantee as above for the raised-exception path: a helper
        raising mid-orchestration must not skip clear_override()."""
        module, _, helpers = patched
        helpers["scale_asg"].side_effect = RuntimeError("uncaught boom")
        with pytest.raises(RuntimeError, match="uncaught boom"):
            module.start_environment()
        helpers["clear_override"].assert_called_once()

    def test_is_verify_detection_failure_falls_through_to_false(self, patched):
        """A NAT pre-check failure must not escape; is_verify defaults to
        False so verify-start mode degrades safely to initial-start mode."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.side_effect = RuntimeError("throttled")
        # Should not raise; should fall through and complete normally.
        result = module.start_environment()
        assert result["statusCode"] == 200
        # Delayed rules NOT enabled because is_verify defaulted to False.
        assert helpers["toggle_event_rules"].call_count == 1

    def test_deferred_proxy_plus_other_error_raises_with_proxy_intact(self, patched):
        """If the proxy is deferred AND another step errors, the run must
        raise on the other error and preserve the deferred proxy result."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("stopped")
        helpers["ensure_rds_proxy_target"].return_value = "deferred_still_creating"
        helpers["scale_asg"].return_value = "error: capacity exceeded"
        with pytest.raises(RuntimeError) as exc_info:
            module.start_environment()
        # The error message should mention the ASG failure, not the proxy.
        assert "capacity" in str(exc_info.value)

    def test_start_scales_public_asg_and_service_to_two(self, patched):
        """Public scales up via its own ASG and its service restores to the
        HA desired count (2), not the desired=1 used for the other services."""
        module, _, helpers = patched
        module.start_environment()
        helpers["scale_asg"].assert_any_call(
            "test-public-asg", min_size=2, desired=2, max_size=4
        )
        helpers["update_ecs_services"].assert_any_call(
            "test-cluster", ["svc-public"], desired_count=2
        )

    def test_start_raises_when_public_gated_on_but_size_var_missing(
        self, patched, monkeypatch
    ):
        """Fail-fast: with PUBLIC_ASG_NAME set but a required size var absent,
        start must raise rather than silently scaling public to 0."""
        module, _, helpers = patched
        monkeypatch.delenv("PUBLIC_ASG_MAX_SIZE", raising=False)
        with pytest.raises(KeyError):
            module.start_environment()
        # clear_override still runs on the way out (try/finally).
        helpers["clear_override"].assert_called_once()

    def test_start_skips_public_when_unset(self, patched, monkeypatch):
        """Back-compat: with the public env vars absent, start touches neither
        a public ASG nor a public service — only the free ASG and 3-service
        restore run."""
        module, _, helpers = patched
        monkeypatch.delenv("PUBLIC_ASG_NAME", raising=False)
        monkeypatch.delenv("PUBLIC_ECS_SERVICE_NAME", raising=False)
        result = module.start_environment()
        assert result["statusCode"] == 200
        assert helpers["scale_asg"].call_count == 1
        assert helpers["update_ecs_services"].call_count == 1


# ===========================================================================
# stop_environment
# ===========================================================================


class TestStopEnvironment:
    @pytest.fixture
    def patched(self, ds, monkeypatch):
        module, clients = ds
        clients["ec2"].describe_instances.return_value = _ec2_response("running")

        helpers = {
            "is_override_active": MagicMock(return_value=False),
            "cleanup_dynamic_premium_instances": MagicMock(return_value="ok"),
            "toggle_event_rules": MagicMock(return_value={"disable_rule-a": "ok"}),
            "update_ecs_services": MagicMock(
                return_value={"ecs_svc-a": "desired_count=0"}
            ),
            "scale_asg": MagicMock(return_value="min=0,desired=0"),
            "stop_instance": MagicMock(return_value="stopping"),
            "destroy_rds": MagicMock(return_value="deleting"),
            "stop_rds": MagicMock(return_value="stopping"),
            "toggle_alarm_actions": MagicMock(return_value="disabled_5_alarms"),
        }
        for name, mock in helpers.items():
            mock.__name__ = name  # with_retry logs fn.__name__ on retries
            monkeypatch.setattr(module, name, mock)
        return module, clients, helpers

    def test_skipped_when_override_active(self, patched):
        module, _, helpers = patched
        helpers["is_override_active"].return_value = True
        result = module.stop_environment()
        assert result["status"] == "skipped_override"
        helpers["destroy_rds"].assert_not_called()
        helpers["stop_rds"].assert_not_called()

    def test_destroy_mode_calls_destroy_rds(self, patched):
        module, _, helpers = patched
        module.stop_environment(stop_mode="destroy")
        helpers["destroy_rds"].assert_called_once()
        helpers["stop_rds"].assert_not_called()

    def test_stop_mode_calls_stop_rds(self, patched):
        module, _, helpers = patched
        module.stop_environment(stop_mode="stop")
        helpers["stop_rds"].assert_called_once()
        helpers["destroy_rds"].assert_not_called()

    def test_cleanup_runs_when_nat_running(self, patched):
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("running")
        module.stop_environment()
        helpers["cleanup_dynamic_premium_instances"].assert_called_once()

    def test_cleanup_skipped_when_nat_stopped(self, patched):
        module, clients, helpers = patched
        clients["ec2"].describe_instances.return_value = _ec2_response("stopped")
        module.stop_environment()
        helpers["cleanup_dynamic_premium_instances"].assert_not_called()

    def test_raises_runtime_error_on_failed_step(self, patched):
        module, _, helpers = patched
        helpers["scale_asg"].return_value = "error: failed"
        with pytest.raises(RuntimeError):
            module.stop_environment()

    def test_nat_describe_raises_skips_cleanup(self, patched):
        """Trade-off pin: if the NAT pre-check itself raises (transient AWS
        API blip), nat_state defaults to "unknown" and cleanup is skipped.
        This is fail-CLOSED — safer for the cleanup-times-out failure mode
        (Lambda billing waste, 15 min stuck call) but worse for the
        AWS-API-blip mode (orphan dynamic premium instances burning money
        until the next stop cycle). Documented here so a future refactor
        toward fail-open is a deliberate choice with a test diff."""
        module, clients, helpers = patched
        clients["ec2"].describe_instances.side_effect = RuntimeError("throttled")
        module.stop_environment()
        helpers["cleanup_dynamic_premium_instances"].assert_not_called()

    def test_verify_stop_with_already_deleting_completes_cleanly(self, patched):
        """A verify-stop that finds RDS already in 'deleting' state must
        complete without raising."""
        module, _, helpers = patched
        helpers["destroy_rds"].return_value = "already_deleting"
        result = module.stop_environment(stop_mode="destroy")
        assert result["statusCode"] == 200
        assert result["results"]["rds"] == "already_deleting"

    def test_stop_scales_public_asg_and_service_to_zero(self, patched):
        """Public service drains to 0 and the public ASG terminates (desired=0
        and min=0) so no instances linger."""
        module, _, helpers = patched
        module.stop_environment(stop_mode="stop")
        helpers["update_ecs_services"].assert_any_call(
            "test-cluster", ["svc-public"], desired_count=0
        )
        helpers["scale_asg"].assert_any_call("test-public-asg", min_size=0, desired=0)

    def test_stop_skips_public_when_unset(self, patched, monkeypatch):
        """Back-compat: with the public env vars absent, stop scales only the
        free ASG and the 3-service set."""
        module, _, helpers = patched
        monkeypatch.delenv("PUBLIC_ASG_NAME", raising=False)
        monkeypatch.delenv("PUBLIC_ECS_SERVICE_NAME", raising=False)
        module.stop_environment(stop_mode="stop")
        assert helpers["scale_asg"].call_count == 1
        assert helpers["update_ecs_services"].call_count == 1


# ===========================================================================
# toggle_event_rules
# ===========================================================================


class TestToggleEventRules:
    def test_enable_success(self, ds):
        module, clients = ds
        result = module.toggle_event_rules(["a", "b"], enable=True)
        assert result == {"enable_a": "ok", "enable_b": "ok"}
        assert clients["events"].enable_rule.call_count == 2

    def test_disable_success(self, ds):
        module, clients = ds
        result = module.toggle_event_rules(["a"], enable=False)
        assert result == {"disable_a": "ok"}
        clients["events"].disable_rule.assert_called_once_with(Name="a")

    def test_failure_prefixed_with_error(self, ds):
        """Failed rule toggles must produce 'error: ...' values so the
        outer error sweep in start/stop_environment catches them."""
        module, clients = ds
        clients["events"].enable_rule.side_effect = RuntimeError("not found")
        result = module.toggle_event_rules(["missing"], enable=True)
        assert result["enable_missing"].startswith("error:")

    def test_partial_failure_recorded_per_rule(self, ds):
        module, clients = ds
        clients["events"].enable_rule.side_effect = [None, RuntimeError("x")]
        result = module.toggle_event_rules(["ok-rule", "bad-rule"], enable=True)
        assert result["enable_ok-rule"] == "ok"
        assert result["enable_bad-rule"].startswith("error:")


# ===========================================================================
# Misc helpers
# ===========================================================================


class TestToggleAlarmActions:
    def test_no_prefix(self, ds):
        module, _ = ds
        assert module.toggle_alarm_actions("", enable=True) == "no_prefix"

    def test_no_alarms(self, ds):
        module, clients = ds
        clients["cloudwatch"].get_paginator.return_value.paginate.return_value = [
            {"MetricAlarms": []}
        ]
        assert module.toggle_alarm_actions("test-", enable=True) == "no_alarms"

    def test_enables_alarms(self, ds):
        module, clients = ds
        clients["cloudwatch"].get_paginator.return_value.paginate.return_value = [
            {"MetricAlarms": [{"AlarmName": "a"}, {"AlarmName": "b"}]}
        ]
        result = module.toggle_alarm_actions("test-", enable=True)
        assert result == "enabled_2_alarms"
        clients["cloudwatch"].enable_alarm_actions.assert_called_once()

    def test_disables_alarms(self, ds):
        module, clients = ds
        clients["cloudwatch"].get_paginator.return_value.paginate.return_value = [
            {"MetricAlarms": [{"AlarmName": "a"}]}
        ]
        result = module.toggle_alarm_actions("test-", enable=False)
        assert result == "disabled_1_alarms"
        clients["cloudwatch"].disable_alarm_actions.assert_called_once()

    def test_batches_at_one_hundred_alarm_boundary(self, ds):
        """150 alarms must produce exactly 2 batches: 100 + 50."""
        module, clients = ds
        alarms = [{"AlarmName": f"a{i}"} for i in range(150)]
        clients["cloudwatch"].get_paginator.return_value.paginate.return_value = [
            {"MetricAlarms": alarms}
        ]
        result = module.toggle_alarm_actions("test-", enable=True)
        assert result == "enabled_150_alarms"
        assert clients["cloudwatch"].enable_alarm_actions.call_count == 2
        first_batch = (
            clients["cloudwatch"]
            .enable_alarm_actions.call_args_list[0]
            .kwargs["AlarmNames"]
        )
        second_batch = (
            clients["cloudwatch"]
            .enable_alarm_actions.call_args_list[1]
            .kwargs["AlarmNames"]
        )
        assert len(first_batch) == 100
        assert len(second_batch) == 50

    def test_consumes_multipage_paginator(self, ds):
        """Must iterate the paginator, not call describe_alarms once
        (which silently truncates at the default page size)."""
        module, clients = ds
        clients["cloudwatch"].get_paginator.return_value.paginate.return_value = [
            {"MetricAlarms": [{"AlarmName": "a"}, {"AlarmName": "b"}]},
            {"MetricAlarms": [{"AlarmName": "c"}]},
        ]
        result = module.toggle_alarm_actions("test-", enable=True)
        assert result == "enabled_3_alarms"


class TestUpdateEcsServices:
    def test_success(self, ds):
        module, clients = ds
        result = module.update_ecs_services(
            "cluster", ["svc-a", "svc-b"], desired_count=1
        )
        assert result == {
            "ecs_svc-a": "desired_count=1",
            "ecs_svc-b": "desired_count=1",
        }
        assert clients["ecs"].update_service.call_count == 2

    def test_per_service_error_captured(self, ds):
        module, clients = ds
        clients["ecs"].update_service.side_effect = [None, RuntimeError("nope")]
        result = module.update_ecs_services(
            "cluster", ["svc-a", "svc-b"], desired_count=0
        )
        assert result["ecs_svc-a"] == "desired_count=0"
        assert result["ecs_svc-b"].startswith("error:")


class TestScaleAsg:
    def test_with_max_size(self, ds):
        module, clients = ds
        result = module.scale_asg("asg", min_size=1, desired=1, max_size=3)
        assert result == "min=1,desired=1,max=3"
        clients["autoscaling"].update_auto_scaling_group.assert_called_once_with(
            AutoScalingGroupName="asg",
            MinSize=1,
            DesiredCapacity=1,
            MaxSize=3,
        )

    def test_without_max_size(self, ds):
        module, clients = ds
        result = module.scale_asg("asg", min_size=0, desired=0)
        assert result == "min=0,desired=0"
        kwargs = clients["autoscaling"].update_auto_scaling_group.call_args.kwargs
        assert "MaxSize" not in kwargs


class TestCleanupDynamicPremium:
    def test_skipped_when_no_function_name(self, ds, monkeypatch):
        module, _ = ds
        monkeypatch.delenv("PREMIUM_MANAGER_FUNCTION_NAME", raising=False)
        assert module.cleanup_dynamic_premium_instances() == "skipped"

    def test_success(self, ds):
        module, clients = ds
        payload_mock = MagicMock()
        payload_mock.read.return_value = json.dumps({"terminated": 2}).encode()
        clients["lambda_client"].invoke.return_value = {
            "Payload": payload_mock,
        }
        result = module.cleanup_dynamic_premium_instances()
        assert result == {"terminated": 2}

    def test_function_error_returns_error(self, ds):
        module, clients = ds
        payload_mock = MagicMock()
        payload_mock.read.return_value = json.dumps({"errorMessage": "boom"}).encode()
        clients["lambda_client"].invoke.return_value = {
            "Payload": payload_mock,
            "FunctionError": "Unhandled",
        }
        result = module.cleanup_dynamic_premium_instances()
        assert isinstance(result, str) and result.startswith("error:")

    def test_function_error_with_non_dict_payload(self, ds):
        """premium_manager could return a non-dict JSON value (e.g. a bare
        string) alongside a FunctionError. The current `f"error: {payload}"`
        formatting handles it; this pin guards against a future refactor
        that assumes `payload` is always a dict."""
        module, clients = ds
        payload_mock = MagicMock()
        # Bare JSON string — valid JSON, parses to a Python str.
        payload_mock.read.return_value = b'"boom"'
        clients["lambda_client"].invoke.return_value = {
            "Payload": payload_mock,
            "FunctionError": "Unhandled",
        }
        result = module.cleanup_dynamic_premium_instances()
        assert isinstance(result, str) and result.startswith("error:")
        assert "boom" in result

    def test_invoke_raises_returns_error(self, ds):
        """Read timeout / network error from boto3 invoke."""
        module, clients = ds
        clients["lambda_client"].invoke.side_effect = RuntimeError("ReadTimeoutError")
        result = module.cleanup_dynamic_premium_instances()
        assert isinstance(result, str) and result.startswith("error:")

    def test_payload_includes_base_instance_ids(self, ds):
        """premium_manager requires `base_instance_ids` in the payload to
        know which instances are Terraform-managed and must not be
        terminated. Pins the inter-Lambda contract."""
        module, clients = ds
        payload_mock = MagicMock()
        payload_mock.read.return_value = json.dumps({}).encode()
        clients["lambda_client"].invoke.return_value = {
            "Payload": payload_mock,
        }
        module.cleanup_dynamic_premium_instances()
        invoke_kwargs = clients["lambda_client"].invoke.call_args.kwargs
        sent_payload = json.loads(invoke_kwargs["Payload"])
        assert sent_payload["action"] == "cleanup_all_dynamic"
        assert sent_payload["base_instance_ids"] == ["i-prem1", "i-prem2"]


# ===========================================================================
# Override (SSM-backed)
# ===========================================================================


class TestOverride:
    def test_set_override_writes_ssm(self, ds):
        module, clients = ds
        result = module.set_override(2)
        assert result["statusCode"] == 200
        assert result["hours"] == 2
        clients["ssm"].put_parameter.assert_called_once()

    def test_set_override_missing_param_name(self, ds, monkeypatch):
        module, _ = ds
        monkeypatch.delenv("OVERRIDE_PARAM_NAME", raising=False)
        assert module.set_override(2)["statusCode"] == 400

    def test_set_override_ssm_failure_returns_500(self, ds):
        module, clients = ds
        clients["ssm"].put_parameter.side_effect = RuntimeError("AccessDenied")
        result = module.set_override(2)
        assert result["statusCode"] == 500

    def test_is_override_active_param_not_found(self, ds):
        module, clients = ds
        clients["ssm"].get_parameter.side_effect = _ParameterNotFound()
        assert module.is_override_active() is False

    def test_is_override_active_off(self, ds):
        module, clients = ds
        clients["ssm"].get_parameter.return_value = {"Parameter": {"Value": "off"}}
        assert module.is_override_active() is False

    def test_is_override_active_future_timestamp(self, ds):
        module, clients = ds
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        clients["ssm"].get_parameter.return_value = {"Parameter": {"Value": future}}
        assert module.is_override_active() is True

    def test_is_override_active_expired_clears(self, ds):
        module, clients = ds
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        clients["ssm"].get_parameter.return_value = {"Parameter": {"Value": past}}
        assert module.is_override_active() is False
        # clear_override writes "off"
        assert clients["ssm"].put_parameter.called

    def test_is_override_active_legacy_on_converts(self, ds):
        module, clients = ds
        clients["ssm"].get_parameter.return_value = {"Parameter": {"Value": "on"}}
        assert module.is_override_active() is True
        clients["ssm"].put_parameter.assert_called_once()  # legacy upgrade

    def test_is_override_active_unknown_value(self, ds):
        module, clients = ds
        clients["ssm"].get_parameter.return_value = {"Parameter": {"Value": "garbage"}}
        assert module.is_override_active() is False

    def test_is_override_active_malformed_timestamp(self, ds):
        """A value that looks like a timestamp but doesn't parse should
        fall through to legacy handling and ultimately return False
        without crashing."""
        module, clients = ds
        clients["ssm"].get_parameter.return_value = {
            "Parameter": {"Value": "2026-13-45T99:99:99Z"}
        }
        assert module.is_override_active() is False

    def test_clear_override_writes_off(self, ds):
        module, clients = ds
        module.clear_override()
        clients["ssm"].put_parameter.assert_called_once()
        kwargs = clients["ssm"].put_parameter.call_args.kwargs
        assert kwargs["Value"] == "off"

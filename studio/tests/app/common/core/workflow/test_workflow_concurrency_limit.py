"""The run-start gate against the plan's concurrent-run limit, and its lease.

- the gate is one conditional UPDATE: check and increment cannot be split by
  a second start (the two-connection race is in the opt-in
  ``test_workflow_count_integration.py``)
- a row whose lease has expired has no run alive, so its count never keeps a
  user out
- a count is released from the table it was taken on, and only once
- the limit comes from the user's plan (NULL = no limit), failing open
"""

from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import mysql

from studio.app.common.core.subscription.constants import SubscriptionPlanIds
from studio.app.common.core.workflow.workflow_tracking import (
    TIER_FREE,
    TIER_PREMIUM,
    WORKFLOW_CONCURRENCY_LIMIT_CODE,
    WORKFLOW_LEASE_TTL_SECONDS,
    ConcurrencyLimit,
    WorkflowConcurrencyLimitError,
    _get_concurrency_limit,
    decrement_workflow_count,
    increment_workflow_count,
    renew_workflow_lease,
)
from studio.app.common.models import FreeUserAssignment, PremiumUserAssignment

MODULE = "studio.app.common.core.workflow.workflow_tracking"

USER_ID = 4321
HOST = "i-0abc123def4567890"
FREE_LIMIT = ConcurrencyLimit("Free", 2, "Premium")


def _sql(statement):
    return " ".join(str(statement.compile(dialect=mysql.dialect())).split())


def _start(
    limit=FREE_LIMIT,
    tier=TIER_FREE,
    has_free_record=True,
    has_premium_record=False,
    rowcount=1,
    active=2,
    execute_error=None,
):
    """Run increment_workflow_count against a mock session.

    Returns (outcome, raised, session): outcome is the returned tier, raised
    the exception, if any.
    """
    with patch(f"{MODULE}._get_user_tier") as get_tier, patch(
        f"{MODULE}.session_scope"
    ) as session_scope, patch(f"{MODULE}.MODE") as mode, patch(
        f"{MODULE}._execution_host", return_value=HOST
    ), patch(
        f"{MODULE}._get_concurrency_limit", return_value=limit
    ):
        mode.IS_STANDALONE = False
        get_tier.return_value = (tier, has_free_record, has_premium_record)

        session = MagicMock()
        update_result = MagicMock(rowcount=rowcount)
        read_back = MagicMock()
        read_back.scalar_one_or_none.return_value = active
        session.execute.side_effect = execute_error or [update_result, read_back]
        session_scope.return_value.__enter__.return_value = session

        outcome, raised = None, None
        try:
            outcome = increment_workflow_count(USER_ID)
        except Exception as e:  # noqa: BLE001 - inspected by the caller
            raised = e

    return outcome, raised, session


def _update_sql(session):
    return _sql(session.execute.call_args_list[0].args[0])


def _where(sql):
    return sql[sql.upper().index(" WHERE ") :].strip()


def _set(sql):
    upper = sql.upper()
    return sql[upper.index(" SET ") : upper.index(" WHERE ")]


class TestTheGateStatement:
    def test_with_a_limit_the_update_matches_only_below_it_or_with_a_stale_lease(
        self,
    ):
        _, _, session = _start()

        where = _where(_update_sql(session))
        assert "free_user_assignments.active_workflow_count < " in where
        assert "free_user_assignments.last_workflow_heartbeat IS NULL" in where
        assert "free_user_assignments.last_workflow_heartbeat < date_sub(now()" in (
            where
        )
        compiled = (
            session.execute.call_args_list[0].args[0].compile(dialect=mysql.dialect())
        )
        assert FREE_LIMIT.limit in compiled.params.values()

    def test_the_lease_is_judged_on_the_database_clock_with_the_ttl(self):
        _, _, session = _start()

        assert f"INTERVAL {WORKFLOW_LEASE_TTL_SECONDS} SECOND" in _update_sql(session)

    def test_a_stale_lease_restarts_the_count_at_this_run(self):
        _, _, session = _start()

        values = _set(_update_sql(session))
        count = values.split("active_workflow_count=", 1)[1]
        assert count.startswith(
            "CASE WHEN (free_user_assignments.last_workflow_heartbeat IS NULL"
        )
        assert "THEN %s ELSE free_user_assignments.active_workflow_count + %s" in (
            count
        )

    def test_the_start_renews_the_lease(self):
        _, _, session = _start()

        assert "last_workflow_heartbeat=now()" in _set(_update_sql(session))

    def test_each_case_is_assigned_before_the_columns_it_reads(self):
        """MySQL assigns SET left to right; a CASE placed after the count or the
        heartbeat would read this run's own new values."""
        _, _, session = _start()

        values = _set(_update_sql(session))
        assert (
            values.index("instance_id=")
            < values.index("active_workflow_count=")
            < values.index("last_workflow_heartbeat=")
        )

    def test_the_pin_moves_to_this_host_when_the_lease_is_stale(self):
        _, _, session = _start()

        pin = _set(_update_sql(session)).split("instance_id=", 1)[1]
        assert pin.startswith(
            "CASE WHEN (free_user_assignments.active_workflow_count > %s AND NOT "
            "(free_user_assignments.last_workflow_heartbeat IS NULL"
        )

    def test_without_a_limit_nothing_is_refused(self):
        _, _, session = _start(limit=None)

        sql = _update_sql(session)
        assert _where(sql) == "WHERE free_user_assignments.user_id = %s"
        assert "active_workflow_count=(free_user_assignments" in sql
        assert "last_workflow_heartbeat=now()" in sql

    def test_the_premium_table_is_gated_the_same_way(self):
        _, _, session = _start(
            tier=TIER_PREMIUM, has_free_record=False, has_premium_record=True
        )

        sql = _update_sql(session)
        assert sql.startswith(f"UPDATE {PremiumUserAssignment.__tablename__} ")
        assert "premium_user_assignments.active_workflow_count < " in _where(sql)


class TestTheGateOutcome:
    def test_a_counted_start_returns_its_tier(self):
        outcome, raised, session = _start()

        assert raised is None
        assert outcome == TIER_FREE
        session.commit.assert_called_once()

    def test_a_premium_start_returns_the_premium_tier(self):
        outcome, _, _ = _start(
            tier=TIER_PREMIUM, has_free_record=False, has_premium_record=True
        )

        assert outcome == TIER_PREMIUM

    def test_at_the_limit_the_start_is_refused_with_the_running_count(self):
        _, raised, session = _start(rowcount=0, active=2)

        assert isinstance(raised, WorkflowConcurrencyLimitError)
        assert raised.active == 2
        assert raised.limit == FREE_LIMIT
        session.commit.assert_not_called()

    def test_a_refused_start_never_falls_back_to_the_other_table(self):
        """Counting on the other row would step around the limit."""
        _, raised, session = _start(
            rowcount=0, has_free_record=True, has_premium_record=True
        )

        assert isinstance(raised, WorkflowConcurrencyLimitError)
        tables = [
            call.args[0].table.name
            for call in session.execute.call_args_list
            if hasattr(call.args[0], "table")
        ]
        assert PremiumUserAssignment.__tablename__ not in tables

    def test_a_row_gone_without_a_limit_is_not_a_refusal(self):
        outcome, raised, _ = _start(limit=None, rowcount=0, active=None)

        assert raised is None
        assert outcome is None

    def test_a_database_error_lets_the_run_start_uncounted(self):
        outcome, raised, _ = _start(execute_error=RuntimeError("db unreachable"))

        assert raised is None
        assert outcome is None

    def test_a_user_without_an_assignment_row_is_not_gated(self):
        with patch(f"{MODULE}._get_concurrency_limit") as lookup:
            outcome, raised, session = _start(
                has_free_record=False, has_premium_record=False
            )

        assert (outcome, raised) == (None, None)
        session.execute.assert_not_called()
        lookup.assert_not_called()

    def test_standalone_is_not_gated(self):
        with patch(f"{MODULE}.MODE") as mode, patch(
            f"{MODULE}.session_scope"
        ) as session_scope:
            mode.IS_STANDALONE = True
            assert increment_workflow_count(USER_ID) is None

        session_scope.assert_not_called()


class TestTheRefusal:
    @staticmethod
    def _error(active=1, limit=1, upgrade="Premium"):
        return WorkflowConcurrencyLimitError(
            ConcurrencyLimit("Free", limit, upgrade), active
        )

    def test_the_detail_carries_what_the_frontend_reads(self):
        detail = self._error().to_detail()

        assert detail == {
            "code": WORKFLOW_CONCURRENCY_LIMIT_CODE,
            "message": self._error().message,
            "plan": "Free",
            "limit": 1,
            "active": 1,
            "upgrade_available": True,
        }

    def test_the_message_says_what_the_user_can_do(self):
        message = self._error().message

        assert "1 workflow running" in message
        assert "Free plan allows 1 at once" in message
        assert "Wait for one to finish or cancel one" in message
        assert f"within about {WORKFLOW_LEASE_TTL_SECONDS // 60} minutes" in message
        assert "The Premium plan allows more workflows at once." in message

    def test_the_count_is_pluralised(self):
        assert "3 workflows running" in self._error(active=3, limit=2).message

    def test_no_upgrade_is_offered_when_none_allows_more(self):
        error = self._error(upgrade=None)

        assert "allows more" not in error.message
        assert error.to_detail()["upgrade_available"] is False


def _plan(plan_id, name, limit, status=True):
    return SimpleNamespace(
        id=plan_id, name=name, max_concurrent_workflows=limit, status=status
    )


FREE = SubscriptionPlanIds.FREE
PREMIUM = SubscriptionPlanIds.PREMIUM


def _lookup(subscribed_plan=None, plans=(), error=None):
    """_get_concurrency_limit with the user's active plan (None = no
    subscription) and the plan rows ``session.get`` finds."""
    rows = {plan.id: plan for plan in plans}
    session = MagicMock()
    session.get.side_effect = lambda _model, plan_id: rows.get(plan_id)

    with patch(f"{MODULE}.session_scope") as session_scope, patch(
        "studio.app.common.core.subscription.subscription_service."
        "SubscriptionService.get_user_subscription",
        side_effect=error,
        return_value=(MagicMock(), subscribed_plan) if subscribed_plan else None,
    ):
        session_scope.return_value.__enter__.return_value = session
        return _get_concurrency_limit(USER_ID)


class TestThePlanLimit:
    def test_a_subscriber_gets_the_plan_limit(self):
        limit = _lookup(subscribed_plan=_plan(PREMIUM, "Premium", 4))

        assert limit == ConcurrencyLimit("Premium", 4, None)

    def test_a_user_without_a_subscription_gets_the_free_plan_limit(self):
        limit = _lookup(
            plans=[_plan(FREE, "Free", 1), _plan(PREMIUM, "Premium", 4)],
        )

        assert limit == ConcurrencyLimit("Free", 1, "Premium")

    def test_an_unlimited_premium_plan_is_an_upgrade(self):
        limit = _lookup(
            plans=[_plan(FREE, "Free", 1), _plan(PREMIUM, "Premium", None)],
        )

        assert limit.upgrade_plan_name == "Premium"

    def test_a_premium_plan_no_higher_is_not_an_upgrade(self):
        limit = _lookup(
            plans=[_plan(FREE, "Free", 2), _plan(PREMIUM, "Premium", 2)],
        )

        assert limit.upgrade_plan_name is None

    def test_an_inactive_premium_plan_is_not_an_upgrade(self):
        limit = _lookup(
            plans=[
                _plan(FREE, "Free", 1),
                _plan(PREMIUM, "Premium", 4, status=False),
            ],
        )

        assert limit.upgrade_plan_name is None

    def test_null_is_no_limit(self):
        assert _lookup(plans=[_plan(FREE, "Free", None)]) is None

    def test_no_free_plan_row_is_no_limit(self):
        assert _lookup(plans=[]) is None

    @pytest.mark.parametrize("value", [0, -1, "2"])
    def test_an_invalid_value_is_no_limit(self, value):
        """A bad row must not stop every run."""
        assert _lookup(plans=[_plan(FREE, "Free", value)]) is None

    def test_a_lookup_error_is_no_limit(self):
        assert _lookup(error=RuntimeError("db unreachable")) is None


class TestReleaseAndRenewTargetTheCountedTable:
    @staticmethod
    def _run(operation, *args, **kwargs):
        with patch(f"{MODULE}._get_user_tier") as get_tier, patch(
            f"{MODULE}.session_scope"
        ) as session_scope, patch(f"{MODULE}.MODE") as mode:
            mode.IS_STANDALONE = False
            # The tier as it reads now: the user upgraded during the run
            get_tier.return_value = (TIER_PREMIUM, True, True)
            session = MagicMock()
            session.execute.return_value.rowcount = 1
            session_scope.return_value.__enter__.return_value = session

            operation(*args, **kwargs)

        return get_tier, session

    def test_a_release_with_its_tier_ignores_a_plan_change_during_the_run(self):
        get_tier, session = self._run(decrement_workflow_count, USER_ID, tier=TIER_FREE)

        statement = session.execute.call_args.args[0]
        assert statement.table.name == FreeUserAssignment.__tablename__
        get_tier.assert_not_called()

    def test_a_release_without_a_tier_resolves_it_as_before(self):
        get_tier, session = self._run(decrement_workflow_count, USER_ID)

        statement = session.execute.call_args.args[0]
        assert statement.table.name == PremiumUserAssignment.__tablename__
        get_tier.assert_called_once()

    def test_a_renewal_writes_the_lease_of_the_counted_table(self):
        _, session = self._run(renew_workflow_lease, USER_ID, TIER_FREE)

        sql = _sql(session.execute.call_args.args[0])
        assert sql.startswith(f"UPDATE {FreeUserAssignment.__tablename__} ")
        assert "SET last_workflow_heartbeat=now()" in sql

    def test_an_uncounted_run_renews_nothing(self):
        _, session = self._run(renew_workflow_lease, USER_ID, None)

        session.execute.assert_not_called()

    def test_a_failed_renewal_does_not_raise(self):
        with patch(f"{MODULE}.session_scope") as session_scope, patch(
            f"{MODULE}.MODE"
        ) as mode:
            mode.IS_STANDALONE = False
            session_scope.return_value.__enter__.side_effect = RuntimeError("down")

            renew_workflow_lease(USER_ID, TIER_FREE)


class TestTheExecutorRenewsTheLeaseWhileTheRunLives:
    @staticmethod
    def _wait(results, done=False):
        from studio.app.common.core.snakemake.snakemake_executor import (
            _wait_renewing_lease,
        )

        future = MagicMock()
        future.result.side_effect = results
        future.done.return_value = done
        with patch(f"{MODULE}.renew_workflow_lease") as renew:
            outcome, raised = None, None
            try:
                outcome = _wait_renewing_lease(future, USER_ID, TIER_FREE)
            except Exception as e:  # noqa: BLE001 - inspected by the caller
                raised = e
        return outcome, raised, renew

    def test_each_wait_that_times_out_renews_the_lease(self):
        outcome, _, renew = self._wait(
            [FutureTimeoutError(), FutureTimeoutError(), "done"]
        )

        assert outcome == "done"
        assert renew.call_count == 2
        renew.assert_called_with(USER_ID, TIER_FREE)

    def test_a_timeout_raised_by_the_run_itself_is_its_result(self):
        """Not a wait timeout: looping on it would never end."""
        error = FutureTimeoutError("raised inside the run")
        _, raised, renew = self._wait([error, "unreachable"], done=True)

        assert raised is error
        renew.assert_not_called()


class TestTheRunnerReleasesWhatItCounted:
    @staticmethod
    def _construct(config_error=None, counted=TIER_FREE):
        from studio.app.common.core.workflow import workflow_runner as module

        run_item = MagicMock()
        run_item.nwbParam = None
        writer = MagicMock()
        if config_error:
            writer.return_value.write.side_effect = config_error

        with ExitStack() as stack:
            stack.enter_context(patch.object(module, "WorkflowConfigWriter", writer))
            stack.enter_context(patch.object(module, "ExptConfigWriter"))
            stack.enter_context(
                patch.object(module, "get_typecheck_params", return_value={})
            )
            stack.enter_context(patch.object(module, "Runner"))
            stack.enter_context(
                patch(f"{MODULE}.increment_workflow_count", return_value=counted)
            )
            decrement = stack.enter_context(patch(f"{MODULE}.decrement_workflow_count"))
            runner, raised = None, None
            try:
                runner = module.WorkflowRunner("bucket", "1", "exp1", run_item, USER_ID)
            except Exception as e:  # noqa: BLE001 - inspected by the caller
                raised = e
            if runner is not None:
                runner.release_workflow_slot()
                runner.release_workflow_slot()
        return raised, decrement, writer

    def test_a_refused_start_writes_nothing_of_the_run(self):
        """A refused re-run must leave the existing experiment untouched."""
        from studio.app.common.core.workflow import workflow_runner as module

        refusal = WorkflowConcurrencyLimitError(FREE_LIMIT, 2)
        with patch.object(module, "WorkflowConfigWriter") as writer, patch.object(
            module, "ExptConfigWriter"
        ) as expt_writer, patch.object(
            module, "get_typecheck_params", return_value={}
        ), patch(
            f"{MODULE}.increment_workflow_count", side_effect=refusal
        ):
            run_item = MagicMock()
            run_item.nwbParam = None
            with pytest.raises(WorkflowConcurrencyLimitError):
                module.WorkflowRunner("bucket", "1", "exp1", run_item, USER_ID)

        writer.assert_not_called()
        expt_writer.assert_not_called()

    def test_a_failure_after_counting_releases_the_count(self):
        raised, decrement, _ = self._construct(config_error=OSError("disk full"))

        assert isinstance(raised, OSError)
        decrement.assert_called_once_with(USER_ID, tier=TIER_FREE)

    def test_a_release_happens_once(self):
        _, decrement, _ = self._construct()

        decrement.assert_called_once_with(USER_ID, tier=TIER_FREE)

    def test_an_uncounted_start_releases_nothing(self):
        _, decrement, _ = self._construct(counted=None)

        decrement.assert_not_called()

    def test_scheduling_hands_the_count_to_the_executor(self):
        """After scheduling, the executor releases; the request must not."""
        from studio.app.common.core.workflow import workflow_runner as module

        runner = module.WorkflowRunner.__new__(module.WorkflowRunner)
        runner.user_id = USER_ID
        runner.counted_tier = TIER_PREMIUM
        runner.workspace_id, runner.unique_id, runner.generation = "1", "e", "g"
        runner.runItem = MagicMock(forceRunList=[])
        tasks = MagicMock()

        with patch.object(
            module.RemoteStorageController, "is_available", return_value=False
        ), patch.object(runner, "set_smk_config", create=True), patch.object(
            module, "get_typecheck_params", return_value={}
        ), patch.object(
            module, "SmkParamReader"
        ), patch.object(
            module, "delete_procs_dependencies"
        ), patch(
            f"{MODULE}.renew_workflow_lease"
        ) as renew, patch(
            f"{MODULE}.decrement_workflow_count"
        ) as decrement:
            runner.run_workflow(tasks)
            runner.release_workflow_slot()

        assert tasks.add_task.call_args.kwargs["counted_tier"] == TIER_PREMIUM
        renew.assert_called_once_with(USER_ID, TIER_PREMIUM)
        decrement.assert_not_called()

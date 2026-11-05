"""The plan's concurrent-run limit, and the release rules of the run count.

- a start takes a slot under its user's row lock; at the limit it is refused
  (the two-connection race is in the opt-in ``test_workflow_count_integration.py``)
- a slot not renewed within the TTL belongs to a dead run and is not counted
- a run renews and deletes only its own slot; ``active_workflow_count``, which
  cleanup and scale-in read, is never reset by the gate
- the count's release goes to the row the run was counted on, following the
  upgrade hand-over
- the limit comes from the user's plan (NULL = no limit), failing open
"""

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import mysql

from studio.app.common.core.subscription.constants import SubscriptionPlanIds
from studio.app.common.core.workflow.workflow_concurrency import (
    WORKFLOW_CONCURRENCY_LIMIT_CODE,
    WORKFLOW_SLOT_TTL_SECONDS,
    ConcurrencyLimit,
    WorkflowConcurrencyLimitError,
    _delete_stale_slots,
    _get_concurrency_limit,
    acquire_workflow_slot,
    release_workflow_slot,
    renew_workflow_slot,
)
from studio.app.common.core.workflow.workflow_tracking import (
    TIER_FREE,
    TIER_PREMIUM,
    decrement_workflow_count,
    increment_workflow_count,
)
from studio.app.common.models import FreeUserAssignment, PremiumUserAssignment

CONCURRENCY = "studio.app.common.core.workflow.workflow_concurrency"
TRACKING = "studio.app.common.core.workflow.workflow_tracking"

USER_ID = 4321
SLOT_ID = 77
FREE_LIMIT = ConcurrencyLimit("Free", 2, "Premium")


def _compiled(statement):
    return statement.compile(dialect=mysql.dialect())


def _sql(statement):
    return " ".join(str(_compiled(statement)).split())


def _statements(session):
    return [call.args[0] for call in session.execute.call_args_list]


def _acquire(limit=FREE_LIMIT, active=0, execute_error=None, user_id=USER_ID):
    """Run acquire_workflow_slot against a mock session.

    Returns (outcome, raised, session, delete_stale).
    """
    with patch(f"{CONCURRENCY}.session_scope") as session_scope, patch(
        f"{CONCURRENCY}.MODE"
    ) as mode, patch(
        f"{CONCURRENCY}._get_concurrency_limit", return_value=limit
    ), patch(
        f"{CONCURRENCY}._delete_stale_slots"
    ) as delete_stale:
        mode.IS_STANDALONE = False
        session = MagicMock()
        count = MagicMock()
        count.scalar_one.return_value = active
        inserted = MagicMock(inserted_primary_key=[SLOT_ID])
        session.execute.side_effect = execute_error or [MagicMock(), count, inserted]
        session_scope.return_value.__enter__.return_value = session

        outcome, raised = None, None
        try:
            outcome = acquire_workflow_slot(user_id, "1", "exp1")
        except Exception as e:  # noqa: BLE001 - inspected by the caller
            raised = e

    return outcome, raised, session, delete_stale


class TestTheGate:
    def test_the_start_locks_its_users_row_first(self):
        _, _, session, _ = _acquire()

        lock = _statements(session)[0]
        sql = _sql(lock)
        assert sql.startswith("SELECT users.id FROM users WHERE users.id = ")
        assert sql.endswith("FOR UPDATE")
        assert USER_ID in _compiled(lock).params.values()

    def test_the_count_is_the_users_live_slots_on_the_database_clock(self):
        _, _, session, _ = _acquire()

        sql = _sql(_statements(session)[1])
        assert "count(*)" in sql
        assert "FROM workflow_run_slots" in sql
        assert "workflow_run_slots.user_id = " in sql
        assert (
            "workflow_run_slots.heartbeat_at >= "
            f"date_sub(now(), INTERVAL {WORKFLOW_SLOT_TTL_SECONDS} SECOND)" in sql
        )

    def test_the_count_takes_no_lock(self):
        """A locking read takes gap locks that other users' starts could
        deadlock on; the users row lock already serialises this user."""
        _, _, session, _ = _acquire()

        sql = _sql(_statements(session)[1]).upper()
        assert "FOR UPDATE" not in sql
        assert "SHARE" not in sql

    def test_below_the_limit_a_slot_is_inserted_and_returned(self):
        outcome, raised, session, delete_stale = _acquire(active=1)

        assert raised is None
        assert outcome == SLOT_ID
        insert = _statements(session)[2]
        assert _sql(insert).startswith("INSERT INTO workflow_run_slots ")
        assert {USER_ID, "1", "exp1"} <= set(_compiled(insert).params.values())
        delete_stale.assert_called_once_with(USER_ID)

    def test_at_the_limit_the_start_is_refused_with_the_running_count(self):
        outcome, raised, session, delete_stale = _acquire(active=2)

        assert isinstance(raised, WorkflowConcurrencyLimitError)
        assert raised.active == 2
        assert raised.limit == FREE_LIMIT
        assert len(_statements(session)) == 2, "nothing inserted"
        delete_stale.assert_not_called()

    def test_without_a_limit_no_slot_is_taken(self):
        outcome, raised, session, _ = _acquire(limit=None)

        assert (outcome, raised) == (None, None)
        session.execute.assert_not_called()

    def test_a_database_error_lets_the_run_start_without_a_slot(self):
        outcome, raised, _, _ = _acquire(execute_error=RuntimeError("db down"))

        assert (outcome, raised) == (None, None)

    def test_a_start_without_a_user_takes_no_slot(self):
        outcome, raised, session, _ = _acquire(user_id=None)

        assert (outcome, raised) == (None, None)
        session.execute.assert_not_called()

    def test_standalone_takes_no_slot(self):
        with patch(f"{CONCURRENCY}.MODE") as mode, patch(
            f"{CONCURRENCY}._get_concurrency_limit"
        ) as lookup:
            mode.IS_STANDALONE = True
            assert acquire_workflow_slot(USER_ID, "1", "exp1") is None

        lookup.assert_not_called()


def _slot_session(operation, *args, execute_error=None):
    with patch(f"{CONCURRENCY}.session_scope") as session_scope:
        session = MagicMock()
        if execute_error:
            session.execute.side_effect = execute_error
        session_scope.return_value.__enter__.return_value = session
        operation(*args)
    return session


class TestASlotIsRenewedAndReleasedByItsRunOnly:
    def test_a_renewal_writes_its_own_slot(self):
        session = _slot_session(renew_workflow_slot, SLOT_ID)

        (statement,) = _statements(session)
        sql = _sql(statement)
        assert sql.startswith("UPDATE workflow_run_slots SET heartbeat_at=now()")
        assert sql.endswith("WHERE workflow_run_slots.id = %s")
        assert SLOT_ID in _compiled(statement).params.values()

    def test_a_release_deletes_its_own_slot(self):
        session = _slot_session(release_workflow_slot, SLOT_ID)

        (statement,) = _statements(session)
        assert _sql(statement) == (
            "DELETE FROM workflow_run_slots WHERE workflow_run_slots.id = %s"
        )
        assert SLOT_ID in _compiled(statement).params.values()

    @pytest.mark.parametrize("operation", [renew_workflow_slot, release_workflow_slot])
    def test_no_slot_writes_nothing(self, operation):
        with patch(f"{CONCURRENCY}.session_scope") as session_scope:
            operation(None)

        session_scope.assert_not_called()

    @pytest.mark.parametrize("operation", [renew_workflow_slot, release_workflow_slot])
    def test_a_database_error_does_not_raise(self, operation):
        _slot_session(operation, SLOT_ID, execute_error=RuntimeError("db down"))

    def test_stale_slots_of_the_user_are_deleted_by_primary_key(self):
        with patch(f"{CONCURRENCY}.session_scope") as session_scope:
            session = MagicMock()
            stale = MagicMock()
            stale.scalars.return_value.all.return_value = [5, 6]
            session.execute.side_effect = [stale, MagicMock()]
            session_scope.return_value.__enter__.return_value = session

            _delete_stale_slots(USER_ID)

        select_stale, delete = _statements(session)
        assert _sql(select_stale).endswith(
            "WHERE workflow_run_slots.user_id = %s AND workflow_run_slots.heartbeat_at"
            f" < date_sub(now(), INTERVAL {WORKFLOW_SLOT_TTL_SECONDS} SECOND)"
        )
        assert _sql(delete) == (
            "DELETE FROM workflow_run_slots WHERE workflow_run_slots.id IN "
            "(__[POSTCOMPILE_id_1])"
        )

    def test_no_stale_slot_deletes_nothing(self):
        with patch(f"{CONCURRENCY}.session_scope") as session_scope:
            session = MagicMock()
            session.execute.return_value.scalars.return_value.all.return_value = []
            session_scope.return_value.__enter__.return_value = session

            _delete_stale_slots(USER_ID)

        assert len(_statements(session)) == 1


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
        assert f"within about {WORKFLOW_SLOT_TTL_SECONDS // 60} minutes" in message
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

    with patch(f"{CONCURRENCY}.session_scope") as session_scope, patch(
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


class TestTheCountIsNeverGated:
    """The gate keeps its state in slots; the count stays plain +1 / -1."""

    @staticmethod
    def _increment(rowcount=1):
        with patch(f"{TRACKING}._get_user_tier") as get_tier, patch(
            f"{TRACKING}.session_scope"
        ) as session_scope, patch(f"{TRACKING}.MODE") as mode, patch(
            f"{TRACKING}._execution_host", return_value="i-0abc123def4567890"
        ):
            mode.IS_STANDALONE = False
            get_tier.return_value = (TIER_FREE, True, False)
            session = MagicMock()
            session.execute.return_value.rowcount = rowcount
            session_scope.return_value.__enter__.return_value = session
            outcome = increment_workflow_count(USER_ID)
        return outcome, session

    def test_the_increment_returns_its_tier(self):
        outcome, _ = self._increment()

        assert outcome == TIER_FREE

    def test_the_increment_has_no_condition_beyond_the_user(self):
        _, session = self._increment()

        sql = _sql(_statements(session)[0])
        assert sql.endswith("WHERE free_user_assignments.user_id = %s")
        assert "active_workflow_count=(free_user_assignments.active_workflow_count" in (
            sql
        )

    def test_the_pin_is_assigned_before_the_count_it_reads(self):
        """MySQL assigns SET left to right."""
        _, session = self._increment()

        sql = _sql(_statements(session)[0])
        assert sql.index("instance_id=CASE") < sql.index("active_workflow_count=")

    def test_a_row_gone_is_not_counted(self):
        outcome, _ = self._increment(rowcount=0)

        assert outcome is None


class TestTheCountIsReleasedOnTheRowItWasCountedOn:
    """The release follows the run's count.

    - a lapse during the run: the tier now reads free, but the count is on
      the premium row it was counted on
    - an upgrade during the run: the premium manager moved the free row's
      count to a new premium row and deleted the free row
    """

    @staticmethod
    def _release(*args, tier_now=TIER_FREE, rowcounts=(1,), **kwargs):
        """decrement_workflow_count; each UPDATE matches the next of
        ``rowcounts`` rows. Returns (get_tier, session, tables written)."""
        with patch(f"{TRACKING}._get_user_tier") as get_tier, patch(
            f"{TRACKING}.session_scope"
        ) as session_scope, patch(f"{TRACKING}.MODE") as mode:
            mode.IS_STANDALONE = False
            get_tier.return_value = (tier_now, True, True)
            session = MagicMock()
            session.execute.side_effect = [MagicMock(rowcount=n) for n in rowcounts]
            session_scope.return_value.__enter__.return_value = session

            decrement_workflow_count(*args, **kwargs)

        tables = [statement.table.name for statement in _statements(session)]
        return get_tier, session, tables

    def test_a_lapsed_plan_releases_the_row_the_run_was_counted_on(self):
        get_tier, _, tables = self._release(USER_ID, tier=TIER_PREMIUM)

        assert tables == [PremiumUserAssignment.__tablename__]
        get_tier.assert_not_called()

    def test_a_release_follows_the_count_handed_over_on_an_upgrade(self):
        _, session, tables = self._release(
            USER_ID, tier=TIER_FREE, tier_now=TIER_PREMIUM, rowcounts=(0, 1)
        )

        assert tables == [
            FreeUserAssignment.__tablename__,
            PremiumUserAssignment.__tablename__,
        ]
        session.commit.assert_called_once()

    def test_a_premium_count_never_moves_to_the_free_row(self):
        """Premium rows are deleted without a hand-over."""
        _, session, tables = self._release(USER_ID, tier=TIER_PREMIUM, rowcounts=(0,))

        assert tables == [PremiumUserAssignment.__tablename__]
        session.commit.assert_not_called()

    def test_a_release_with_no_row_left_writes_nothing(self):
        _, session, tables = self._release(USER_ID, tier=TIER_FREE, rowcounts=(0, 0))

        assert len(tables) == 2
        session.commit.assert_not_called()

    def test_a_release_without_a_tier_resolves_it_as_before(self):
        get_tier, _, tables = self._release(USER_ID, tier_now=TIER_PREMIUM)

        assert tables == [PremiumUserAssignment.__tablename__]
        get_tier.assert_called_once()


class TestTheExecutorRenewsTheSlotWhileTheRunLives:
    @staticmethod
    def _wait(results, done=False):
        from studio.app.common.core.snakemake.snakemake_executor import (
            _wait_renewing_slot,
        )

        future = MagicMock()
        future.result.side_effect = results
        future.done.return_value = done
        with patch(f"{CONCURRENCY}.renew_workflow_slot") as renew:
            outcome, raised = None, None
            try:
                outcome = _wait_renewing_slot(future, SLOT_ID)
            except Exception as e:  # noqa: BLE001 - inspected by the caller
                raised = e
        return outcome, raised, renew

    def test_each_wait_that_times_out_renews_the_slot(self):
        outcome, _, renew = self._wait(
            [FutureTimeoutError(), FutureTimeoutError(), "done"]
        )

        assert outcome == "done"
        assert renew.call_count == 2
        renew.assert_called_with(SLOT_ID)

    def test_a_run_finished_after_the_wait_timed_out_keeps_its_result(self):
        """The future can complete between the timeout and the done() check."""
        outcome, raised, renew = self._wait([FutureTimeoutError(), "done"], done=True)

        assert raised is None
        assert outcome == "done"
        renew.assert_not_called()

    def test_a_timeout_raised_by_the_run_itself_is_its_result(self):
        error = FutureTimeoutError("raised inside the run")
        _, raised, renew = self._wait([error, error], done=True)

        assert raised is error
        renew.assert_not_called()

    @staticmethod
    def _execute(raises=None):
        from studio.app.common.core.snakemake import snakemake_executor

        executor = MagicMock()
        future = executor.__enter__.return_value.submit.return_value
        if raises:
            future.result.side_effect = raises
        else:
            future.result.return_value = "done"

        with patch.object(
            snakemake_executor, "ProcessPoolExecutor", return_value=executor
        ), patch.object(
            snakemake_executor, "update_user_storage_after_workflow"
        ), patch.object(
            snakemake_executor, "get_client_id_for_subprocess", return_value="client"
        ), patch(
            f"{CONCURRENCY}.release_workflow_slot"
        ) as release, patch(
            f"{TRACKING}.decrement_workflow_count"
        ):
            try:
                snakemake_executor.snakemake_execute(
                    "1", "exp1", MagicMock(), user_id=USER_ID, slot_id=SLOT_ID
                )
            except RuntimeError:
                pass
        return release

    def test_the_slot_is_released_when_the_run_ends(self):
        self._execute().assert_called_once_with(SLOT_ID)

    def test_the_slot_is_released_when_the_run_raises(self):
        self._execute(raises=RuntimeError("snakemake died")).assert_called_once_with(
            SLOT_ID
        )


class TestTheRunnerReleasesWhatItTook:
    @staticmethod
    def _construct(config_error=None, slot=SLOT_ID, counted=TIER_FREE, refusal=None):
        """Construct a runner, then release it twice.

        Returns (raised, release_slot, decrement, writer, increment).
        """
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
                patch(
                    f"{CONCURRENCY}.acquire_workflow_slot",
                    return_value=slot,
                    side_effect=refusal,
                )
            )
            increment = stack.enter_context(
                patch(f"{TRACKING}.increment_workflow_count", return_value=counted)
            )
            release_slot = stack.enter_context(
                patch(f"{CONCURRENCY}.release_workflow_slot")
            )
            decrement = stack.enter_context(
                patch(f"{TRACKING}.decrement_workflow_count")
            )
            runner, raised = None, None
            try:
                runner = module.WorkflowRunner("bucket", "1", "exp1", run_item, USER_ID)
            except Exception as e:  # noqa: BLE001 - inspected by the caller
                raised = e
            if runner is not None:
                runner.release_unscheduled()
                runner.release_unscheduled()
        return raised, release_slot, decrement, writer, increment

    def test_a_refused_start_writes_nothing_and_counts_nothing(self):
        """A refused re-run must leave the existing experiment untouched."""
        refusal = WorkflowConcurrencyLimitError(FREE_LIMIT, 2)
        raised, _, _, writer, increment = self._construct(refusal=refusal)

        assert raised is refusal
        writer.assert_not_called()
        increment.assert_not_called()

    def test_a_failure_after_counting_releases_the_slot_and_the_count(self):
        raised, release_slot, decrement, _, _ = self._construct(
            config_error=OSError("disk full")
        )

        assert isinstance(raised, OSError)
        release_slot.assert_called_once_with(SLOT_ID)
        decrement.assert_called_once_with(USER_ID, tier=TIER_FREE)

    def test_a_release_happens_once(self):
        _, release_slot, decrement, _, _ = self._construct()

        release_slot.assert_called_once_with(SLOT_ID)
        decrement.assert_called_once_with(USER_ID, tier=TIER_FREE)

    def test_a_start_without_a_slot_or_a_count_releases_nothing(self):
        _, release_slot, decrement, _, _ = self._construct(slot=None, counted=None)

        release_slot.assert_not_called()
        decrement.assert_not_called()

    def test_scheduling_hands_the_slot_and_the_count_to_the_executor(self):
        """After scheduling, the executor releases; the request must not."""
        from studio.app.common.core.workflow import workflow_runner as module

        runner = module.WorkflowRunner.__new__(module.WorkflowRunner)
        runner.user_id = USER_ID
        runner.slot_id = SLOT_ID
        runner.counted_tier = TIER_PREMIUM
        runner.workspace_id, runner.unique_id, runner.generation = "1", "e", "g"
        runner.runItem = MagicMock(forceRunList=[])
        runner.forced_by_fill = []
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
            f"{CONCURRENCY}.renew_workflow_slot"
        ) as renew, patch(
            f"{CONCURRENCY}.release_workflow_slot"
        ) as release_slot, patch(
            f"{TRACKING}.decrement_workflow_count"
        ) as decrement:
            runner.run_workflow(tasks)
            runner.release_unscheduled()

        kwargs = tasks.add_task.call_args.kwargs
        assert (kwargs["slot_id"], kwargs["counted_tier"]) == (SLOT_ID, TIER_PREMIUM)
        renew.assert_called_once_with(SLOT_ID)
        release_slot.assert_not_called()
        decrement.assert_not_called()


class TestTheSlotStaysAliveWhileTheInputDownloads:
    @staticmethod
    async def _download(slot=SLOT_ID, duration=0.05, error=None):
        """ensure_input_data_local over a download of ``duration`` seconds, with
        a renewal interval of 10 ms. Returns (renewals during, after, raised)."""
        from studio.app.common.core.workflow import workflow_runner as module

        runner = module.WorkflowRunner.__new__(module.WorkflowRunner)
        runner.slot_id = slot

        async def download():
            await asyncio.sleep(duration)
            if error:
                raise error

        runner._ensure_input_data_local = AsyncMock(side_effect=download)
        raised = None
        with patch(f"{CONCURRENCY}.WORKFLOW_SLOT_RENEW_SECONDS", 0.01), patch(
            f"{CONCURRENCY}.renew_workflow_slot"
        ) as renew:
            try:
                await runner.ensure_input_data_local()
            except Exception as e:  # noqa: BLE001 - inspected by the caller
                raised = e
            during = renew.call_count
            await asyncio.sleep(0.05)
            after = renew.call_count
            if during:
                renew.assert_called_with(slot)
        return during, after, raised

    @pytest.mark.asyncio
    async def test_a_long_download_renews_the_slot(self):
        during, _, _ = await self._download()

        assert during >= 2

    @pytest.mark.asyncio
    async def test_the_renewal_stops_once_the_download_ends(self):
        during, after, _ = await self._download()

        assert after == during

    @pytest.mark.asyncio
    async def test_the_renewal_stops_when_the_download_fails(self):
        during, after, raised = await self._download(error=OSError("S3 down"))

        assert isinstance(raised, OSError)
        assert after == during

    @pytest.mark.asyncio
    async def test_no_slot_renews_nothing(self):
        during, after, _ = await self._download(slot=None)

        assert (during, after) == (0, 0)

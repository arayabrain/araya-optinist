"""Expiration deletion against a real database and a real S3 API.

Opt-in: the cases run only with ``RUN_EXPIRATION_IT=1`` in the expiration-it
lane (``make expiration_it``), where alembic has migrated a throwaway MySQL and
``AWS_ENDPOINT_URL_S3`` points the job's own aioboto3 client at a moto server.
The per-PR lane collects this module and skips it. Every import that needs the
database is inside a test or helper body for the same reason.

``ExpirationLifecycleJob.run()`` runs unpatched; only the SES send is recorded
instead of delivered. The mocked tests in ``test_expiration_lifecycle_job.py``
stubbed the bucket lookup and the S3 objects, which is how the job shipped
without ever finding a bucket and with ``ObjectSummary.size`` read unawaited.
"""

import asyncio
import os
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_EXPIRATION_IT") != "1",
    reason="opt-in: run via `make expiration_it` (database + S3 endpoint)",
)

EXCESS = 3_000
UNIT = 2_000


@pytest.fixture(autouse=True)
def _s3_storage_and_scratch_local_copies(tmp_path, monkeypatch):
    """The root conftest pins mock storage and the repo's test_data for every run.

    The job deletes local copies too, and studio/test_data/input/1 is a fixture.
    """
    from studio.app.dir_path import DIRPATH

    monkeypatch.setenv("REMOTE_STORAGE_TYPE", "2")
    monkeypatch.setattr(DIRPATH, "INPUT_DIR", str(tmp_path / "input"))
    monkeypatch.setattr(DIRPATH, "OUTPUT_DIR", str(tmp_path / "output"))


def _now():
    from studio.app.common.core.utils.datetime_utils import get_current_datetime

    return get_current_datetime().replace(tzinfo=None)


def _s3():
    import boto3

    return boto3.client("s3")


def _create_bucket(name):
    _s3().create_bucket(
        Bucket=name,
        CreateBucketConfiguration={
            "LocationConstraint": os.environ["AWS_DEFAULT_REGION"]
        },
    )


def _keys(bucket):
    listing = _s3().list_objects_v2(Bucket=bucket)
    return {obj["Key"] for obj in listing.get("Contents", [])}


def _seed_user(*, days_expired, usage_over_quota, bucket=None):
    """An expired premium user with one workspace holding one experiment."""
    from sqlalchemy import text

    from studio.app.common.core.subscription.constants import (
        ExpirationDeletion,
        SubscriptionPlanIds,
    )
    from studio.app.common.db.database import session_scope
    from studio.app.common.models import User
    from studio.app.common.models.experiment import ExperimentRecord
    from studio.app.common.models.subscription import UserStorageUsage, UserSubscription
    from studio.app.common.models.workspace import Workspace

    tag = uuid.uuid4().hex[:10]
    email = f"expiration-it-{tag}@example.invalid"
    free = ExpirationDeletion.FREE_QUOTA_BYTES
    with session_scope() as db:
        # The seed migration inserts only the Free plan
        db.execute(
            text(
                "INSERT IGNORE INTO subscription_plans "
                "(id, name, price, billing_cycle, features, currency, status) "
                f"VALUES ({SubscriptionPlanIds.PREMIUM}, 'Premium', 0, 1, '{{}}', 1, 1)"
            )
        )
        user = User(
            organization_id=1,
            uid=email,
            name="expiration IT",
            email=email,
            attributes={"remote_bucket_name": bucket} if bucket else {},
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(
            UserSubscription(
                user_id=user.id,
                plan_id=SubscriptionPlanIds.PREMIUM,
                expiration=_now() - timedelta(days=days_expired),
            )
        )
        db.add(
            UserStorageUsage(
                user_id=user.id,
                storage_usage_bytes=free + usage_over_quota,
                storage_quota_bytes=free,
            )
        )
        workspace = Workspace(name="expiration IT", user_id=user.id, deleted=False)
        db.add(workspace)
        db.flush()
        db.add(
            ExperimentRecord(
                workspace_id=workspace.id,
                uid=f"it{tag}",
                publish_status=0,
                analyzed_at=_now() - timedelta(days=days_expired + 10),
            )
        )
        return {
            "user_id": user.id,
            "email": email,
            "workspace_id": workspace.id,
            "uid": f"it{tag}",
        }


def _put_experiment_data(bucket, seeded, local=True):
    """Config, output, intermediate and input files in S3, plus local copies.

    Returns the S3 keys by tier.
    """
    from studio.app.common.core.storage.s3_storage_controller import S3StorageController

    ws = str(seeded["workspace_id"])
    out_prefix = S3StorageController.make_s3_output_prefix(ws, seeded["uid"])
    in_prefix = S3StorageController.make_s3_input_prefix(ws)
    files = {
        "config": (out_prefix, "experiment.yaml", 100),
        "output": (out_prefix, "result.npy", UNIT),
        "intermediate": (out_prefix, "suite2p_roi/fluorescence.npy", UNIT),
        "input": (in_prefix, "movie.tif", UNIT),
    }
    keys = {}
    for tier, (prefix, relative, size) in files.items():
        keys[tier] = prefix + relative
        _s3().put_object(Bucket=bucket, Key=keys[tier], Body=b"x" * size)
        if local:
            path = _local_path(keys[tier])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)
    return keys


def _local_path(key):
    """The local copy of an S3 key: keys mirror DATA_DIR below S3_BASE_PATH."""
    from studio.app.dir_path import DIRPATH

    for tier_dir, local_dir in (
        ("/input/", DIRPATH.INPUT_DIR),
        ("/output/", DIRPATH.OUTPUT_DIR),
    ):
        if tier_dir in key:
            return Path(local_dir, key.split(tier_dir, 1)[1])
    raise ValueError(key)


def _db_state(seeded):
    from studio.app.common.db.database import session_scope
    from studio.app.common.models.experiment import ExperimentRecord
    from studio.app.common.models.subscription import UserStorageUsage, UserSubscription

    with session_scope() as db:
        sub = (
            db.query(UserSubscription)
            .filter(UserSubscription.user_id == seeded["user_id"])
            .one()
        )
        usage = (
            db.query(UserStorageUsage)
            .filter(UserStorageUsage.user_id == seeded["user_id"])
            .one()
        )
        record = (
            db.query(ExperimentRecord)
            .filter(ExperimentRecord.uid == seeded["uid"])
            .one()
        )
        return {
            "processed_at": sub.deletion_processed_at,
            "start_notified_at": sub.grace_start_notified_at,
            "again_notified_at": sub.reprocess_notified_at,
            "usage": usage.storage_usage_bytes,
            "flags": (record.has_intermediates, record.has_outputs, record.has_inputs),
        }


def _set_subscription(seeded, **columns):
    from studio.app.common.db.database import session_scope
    from studio.app.common.models.subscription import UserSubscription

    with session_scope() as db:
        db.query(UserSubscription).filter(
            UserSubscription.user_id == seeded["user_id"]
        ).update(columns)


def _set_usage_over_quota(seeded, over):
    from studio.app.common.core.subscription.constants import ExpirationDeletion
    from studio.app.common.db.database import session_scope
    from studio.app.common.models.subscription import UserStorageUsage

    with session_scope() as db:
        db.query(UserStorageUsage).filter(
            UserStorageUsage.user_id == seeded["user_id"]
        ).update({"storage_usage_bytes": ExpirationDeletion.FREE_QUOTA_BYTES + over})


@contextmanager
def _emails_on():
    """Notifications enabled; yields the (to, subject) pairs instead of SES sends."""
    from studio.app.common.core.subscription.email_service import EmailService

    sent = []

    def record(to_email, subject, body):
        sent.append((to_email, subject))
        return True

    with patch.dict(
        os.environ, {"NOTIFICATION_FROM_EMAIL": "noreply@example.invalid"}
    ), patch.object(EmailService, "_send", side_effect=record):
        yield sent


def _subjects_to(sent, seeded):
    return [subject for to, subject in sent if to == seeded["email"]]


def _run_job():
    from studio.app.common.core.background.expiration_lifecycle_job import (
        ExpirationLifecycleJob,
    )

    asyncio.run(ExpirationLifecycleJob.run())


class TestExpirationDeletionOverRealStorage:
    def test_deletes_over_quota_data_from_the_users_own_bucket(self):
        bucket = f"expiration-it-own-{uuid.uuid4().hex[:8]}"
        _create_bucket(bucket)
        seeded = _seed_user(days_expired=31, usage_over_quota=EXCESS, bucket=bucket)
        keys = _put_experiment_data(bucket, seeded)
        # Same keys in the shared bucket: deleting from it would be the wrong data
        default_bucket = os.environ["S3_DEFAULT_BUCKET_NAME"]
        _create_bucket(default_bucket)
        _put_experiment_data(default_bucket, seeded, local=False)
        before = _db_state(seeded)

        _run_job()

        # preserve_outputs: intermediates (2000) then inputs (2000) cover 3000
        assert _keys(bucket) == {keys["config"], keys["output"]}
        assert set(keys.values()) <= _keys(default_bucket)
        after = _db_state(seeded)
        assert after["flags"] == (False, True, False)
        assert after["processed_at"] is not None
        assert after["usage"] == before["usage"] - 2 * UNIT
        assert not _local_path(keys["intermediate"]).exists()
        assert not _local_path(keys["input"]).exists()
        assert _local_path(keys["config"]).exists()
        assert _local_path(keys["output"]).exists()

    def test_finds_the_derived_bucket_when_none_is_recorded(self):
        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteStorageController,
        )

        seeded = _seed_user(days_expired=31, usage_over_quota=EXCESS)
        bucket = RemoteStorageController.create_user_bucket_name(
            id=seeded["user_id"], prefix=os.environ["S3_USER_BUCKET_PREFIX"]
        )
        _create_bucket(bucket)
        keys = _put_experiment_data(bucket, seeded)

        _run_job()

        assert _keys(bucket) == {keys["config"], keys["output"]}
        assert _db_state(seeded)["processed_at"] is not None

    def test_grace_and_under_quota_users_keep_their_data(self):
        bucket = f"expiration-it-keep-{uuid.uuid4().hex[:8]}"
        _create_bucket(bucket)
        in_grace = _seed_user(days_expired=10, usage_over_quota=EXCESS, bucket=bucket)
        under_quota = _seed_user(days_expired=40, usage_over_quota=-1, bucket=bucket)
        kept = set(_put_experiment_data(bucket, in_grace).values())
        kept |= set(_put_experiment_data(bucket, under_quota).values())

        _run_job()

        assert _keys(bucket) == kept
        for seeded in (in_grace, under_quota):
            state = _db_state(seeded)
            assert state["flags"] == (True, True, True)
            assert state["processed_at"] is None

    def test_with_emails_on_it_warns_first_and_deletes_after_the_notice(self):
        from studio.app.common.core.subscription.constants import ExpirationDeletion

        bucket = f"expiration-it-notice-{uuid.uuid4().hex[:8]}"
        _create_bucket(bucket)
        seeded = _seed_user(days_expired=31, usage_over_quota=EXCESS, bucket=bucket)
        keys = _put_experiment_data(bucket, seeded)

        with _emails_on() as sent:
            _run_job()

            mine = _subjects_to(sent, seeded)
            assert len(mine) == 1 and "deletion notice" in mine[0]
            assert _keys(bucket) == set(keys.values())
            state = _db_state(seeded)
            assert state["start_notified_at"] is not None
            assert state["processed_at"] is None

            _set_subscription(
                seeded,
                grace_start_notified_at=_now()
                - timedelta(days=ExpirationDeletion.MIN_NOTICE_DAYS + 1),
            )
            _run_job()

        assert _keys(bucket) == {keys["config"], keys["output"]}
        assert _db_state(seeded)["processed_at"] is not None
        mine = _subjects_to(sent, seeded)
        assert len(mine) == 2 and "deletion completed" in mine[1]

    def test_repeat_pass_waits_for_the_cooldown_and_restarts_it(self):
        bucket = f"expiration-it-repeat-{uuid.uuid4().hex[:8]}"
        _create_bucket(bucket)
        seeded = _seed_user(days_expired=60, usage_over_quota=EXCESS, bucket=bucket)
        keys = _put_experiment_data(bucket, seeded)

        _set_subscription(seeded, deletion_processed_at=_now() - timedelta(days=2))
        _run_job()
        assert _keys(bucket) == set(keys.values())

        _set_subscription(seeded, deletion_processed_at=_now() - timedelta(days=8))
        _run_job()
        assert _keys(bucket) == {keys["config"], keys["output"]}
        assert _db_state(seeded)["processed_at"] > _now() - timedelta(minutes=5)

        # Over again straight away: the pass just now restarted the cooldown
        _set_usage_over_quota(seeded, EXCESS)
        _run_job()
        assert _keys(bucket) == {keys["config"], keys["output"]}

    def test_with_emails_on_a_repeat_pass_needs_a_fresh_warning(self):
        from studio.app.common.core.subscription.constants import ExpirationDeletion

        bucket = f"expiration-it-again-{uuid.uuid4().hex[:8]}"
        _create_bucket(bucket)
        seeded = _seed_user(days_expired=60, usage_over_quota=EXCESS, bucket=bucket)
        keys = _put_experiment_data(bucket, seeded)
        _set_subscription(
            seeded,
            grace_start_notified_at=_now() - timedelta(days=50),
            grace_reminder_notified_at=_now() - timedelta(days=40),
            deletion_processed_at=_now() - timedelta(days=9),
        )

        with _emails_on() as sent:
            _run_job()

            mine = _subjects_to(sent, seeded)
            assert len(mine) == 1 and "over the free plan quota again" in mine[0]
            assert _keys(bucket) == set(keys.values())

            _set_subscription(
                seeded,
                reprocess_notified_at=_now()
                - timedelta(days=ExpirationDeletion.MIN_NOTICE_DAYS + 1),
            )
            _run_job()

        assert _keys(bucket) == {keys["config"], keys["output"]}
        mine = _subjects_to(sent, seeded)
        assert len(mine) == 2 and "deletion completed" in mine[1]

    def test_with_emails_on_a_pass_that_leaves_the_user_over_sends_no_again_email(
        self,
    ):
        bucket = f"expiration-it-still-over-{uuid.uuid4().hex[:8]}"
        _create_bucket(bucket)
        # Far more over quota than the deletable data: the pass cannot get under
        seeded = _seed_user(days_expired=60, usage_over_quota=100 * UNIT, bucket=bucket)
        keys = _put_experiment_data(bucket, seeded)
        _set_subscription(
            seeded,
            grace_start_notified_at=_now() - timedelta(days=50),
            grace_reminder_notified_at=_now() - timedelta(days=40),
        )

        with _emails_on() as sent:
            _run_job()
            assert _keys(bucket) == {keys["config"]}
            state = _db_state(seeded)
            assert state["again_notified_at"] == state["processed_at"]

            _run_job()

            # Still over after the cooldown: weekly retries, still no new email
            week_ago = _now() - timedelta(days=8)
            _set_subscription(
                seeded, deletion_processed_at=week_ago, reprocess_notified_at=week_ago
            )
            _run_job()

        mine = _subjects_to(sent, seeded)
        assert len(mine) == 1 and "deletion completed" in mine[0]
        assert _db_state(seeded)["processed_at"] > _now() - timedelta(minutes=5)

    def test_with_emails_on_a_stale_again_notice_is_sent_afresh(self):
        from studio.app.common.core.subscription.constants import ExpirationDeletion

        bucket = f"expiration-it-stale-{uuid.uuid4().hex[:8]}"
        _create_bucket(bucket)
        seeded = _seed_user(days_expired=120, usage_over_quota=EXCESS, bucket=bucket)
        keys = _put_experiment_data(bucket, seeded)
        valid_days = ExpirationDeletion.REPROCESS_NOTICE_VALID_DAYS
        # Warned after the last pass, but went under quota before the next one
        _set_subscription(
            seeded,
            grace_start_notified_at=_now() - timedelta(days=110),
            grace_reminder_notified_at=_now() - timedelta(days=100),
            deletion_processed_at=_now() - timedelta(days=valid_days + 20),
            reprocess_notified_at=_now() - timedelta(days=valid_days + 1),
        )

        with _emails_on() as sent:
            _run_job()

        assert _keys(bucket) == set(keys.values())
        mine = _subjects_to(sent, seeded)
        assert len(mine) == 1 and "over the free plan quota again" in mine[0]

    def test_a_crash_mid_unit_leaves_flags_and_usage_matching_s3(self):
        from studio.app.common.core.background.expiration_lifecycle_job import (
            ExpirationLifecycleJob,
        )

        class Crash(BaseException):
            pass

        bucket = f"expiration-it-crash-{uuid.uuid4().hex[:8]}"
        _create_bucket(bucket)
        seeded = _seed_user(days_expired=31, usage_over_quota=EXCESS, bucket=bucket)
        keys = _put_experiment_data(bucket, seeded)
        second = keys["intermediate"].replace("fluorescence", "f2")
        _s3().put_object(Bucket=bucket, Key=second, Body=b"x" * UNIT)
        first_deleted = min(keys["intermediate"], second)
        before = _db_state(seeded)

        # The job dies while handling the first file it deleted
        with patch.object(
            ExpirationLifecycleJob, "_delete_local_file", side_effect=Crash()
        ), pytest.raises(Crash):
            _run_job()

        assert first_deleted not in _keys(bucket)
        assert max(keys["intermediate"], second) in _keys(bucket)
        after = _db_state(seeded)
        assert after["flags"][0] is False
        assert after["usage"] == before["usage"] - UNIT


class TestQueuedDeletionOverRealStorage:
    def test_experiment_delete_removes_it_from_the_owners_bucket(self):
        from studio.app.common.core.background.background_task_worker import (
            BackgroundTaskWorker,
        )
        from studio.app.common.core.mode import MODE

        bucket = f"expiration-it-queued-{uuid.uuid4().hex[:8]}"
        _create_bucket(bucket)
        seeded = _seed_user(days_expired=-30, usage_over_quota=EXCESS, bucket=bucket)
        # No local copy: the placeholder experiment.yaml is not a parseable config
        keys = _put_experiment_data(bucket, seeded, local=False)

        with patch.object(MODE, "IS_STANDALONE", False):
            ok = asyncio.run(
                BackgroundTaskWorker._delete_experiment(
                    workspace_id=seeded["workspace_id"],
                    experiment_uid=seeded["uid"],
                )
            )

        assert ok is True
        assert _keys(bucket) == {keys["input"]}

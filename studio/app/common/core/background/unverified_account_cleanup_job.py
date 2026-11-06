"""
Background job to delete stale unverified Firebase accounts.

Self-registration creates a Firebase account with email_verified=False plus a
DB user row and an empty per-user bucket. An account that is never verified
can never log in, so abandoned registrations accumulate forever. This job
sweeps them via the Admin SDK.

Safety model (Firebase deletion is irreversible):
- Only accounts never used after registration are candidates. Registration
  itself signs the user in once to send the verification email, so a Firebase
  sign-in is allowed only within REGISTRATION_WINDOW_SECONDS of creation, and
  the DB row must not have been edited after that window. Firebase resets
  email_verified when an email address is changed through the Admin SDK, so
  "unverified + old" alone could match a dormant real user; these two checks
  exclude it.
- Mutations happen only when UNVERIFIED_ACCOUNT_CLEANUP_MODE=delete; any other
  value (including unset) is a dry run that runs the same DB checks and only
  logs the uids delete mode would delete.
- Never touched: uids in UNVERIFIED_CLEANUP_PROTECTED_UIDS, disabled accounts,
  non-password providers, admin-role users, and uids with no DB row in this
  environment (the Firebase project may be shared across environments).
"""

import asyncio
import os
import time
from typing import Optional, Set, Tuple

from firebase_admin import auth as firebase_auth

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageSimpleWriter,
)
from studio.app.common.core.subscription.constants import UnverifiedAccountCleanup
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.app.common.db.database import session_scope
from studio.app.common.models.subscription import (
    DeletionStatus,
    DeletionStep,
    UserDeletionRecord,
)
from studio.app.common.models.user import User as UserModel
from studio.app.common.models.user import UserRole as UserRoleModel
from studio.app.common.schemas.users import UserRole

logger = AppLogger.get_logger()


class UnverifiedAccountCleanupJob:
    """Delete Firebase accounts that never verified their email and never
    signed in, after a grace period.

    ``run`` is deliberately synchronous: AsyncIOScheduler executes sync jobs
    in a worker thread, keeping the blocking Firebase/DB/S3 calls off the
    event loop that serves /health.
    """

    @classmethod
    def run(cls):
        try:
            delete_mode = cls._is_delete_mode()
            protected_uids = cls._get_protected_uids()
            cutoff_ms = (
                time.time() - UnverifiedAccountCleanup.GRACE_PERIOD_DAYS * 86400
            ) * 1000

            candidates = [
                user.uid
                for user in firebase_auth.list_users().iterate_all()
                if cls._is_candidate(user, cutoff_ms, protected_uids)
            ]

            if not candidates:
                logger.info("Unverified account cleanup: no candidates")
                return

            logger.info(
                f"Unverified account cleanup: {len(candidates)} candidates "
                f"(mode={'delete' if delete_mode else 'dry_run'}), uids: "
                f"{candidates[:UnverifiedAccountCleanup.MAX_ACCOUNTS_PER_RUN]}"
            )
            if not delete_mode:
                would_delete = [uid for uid in candidates if cls._db_precheck(uid)]
                logger.info(
                    f"Unverified account cleanup dry run: {len(would_delete)} "
                    f"would be deleted, uids: "
                    f"{would_delete[:UnverifiedAccountCleanup.MAX_ACCOUNTS_PER_RUN]}"
                )
                return

            deleted = skipped = errors = 0
            for uid in candidates:
                if deleted >= UnverifiedAccountCleanup.MAX_ACCOUNTS_PER_RUN:
                    break
                try:
                    if cls._delete_account(uid, cutoff_ms, protected_uids):
                        deleted += 1
                    else:
                        skipped += 1
                except Exception as e:
                    logger.error(
                        f"Unverified account cleanup failed for uid {uid}: {e}",
                        exc_info=True,
                    )
                    errors += 1

            logger.info(
                f"Unverified account cleanup completed: {deleted} deleted, "
                f"{skipped} skipped, {errors} errors"
            )

        except Exception as e:
            logger.error(f"Unverified account cleanup sweep failed: {e}", exc_info=True)

    @staticmethod
    def _is_delete_mode() -> bool:
        """Fail closed: anything but an exact ``delete`` is a dry run."""
        mode = os.environ.get(
            UnverifiedAccountCleanup.MODE_ENV, UnverifiedAccountCleanup.MODE_DRY_RUN
        )
        if mode == UnverifiedAccountCleanup.MODE_DELETE:
            return True
        if mode != UnverifiedAccountCleanup.MODE_DRY_RUN:
            logger.warning(
                f"Unknown {UnverifiedAccountCleanup.MODE_ENV} value {mode!r}, "
                "falling back to dry_run"
            )
        return False

    @staticmethod
    def _get_protected_uids() -> Set[str]:
        raw = os.environ.get(UnverifiedAccountCleanup.PROTECTED_UIDS_ENV, "")
        return {uid.strip() for uid in raw.split(",") if uid.strip()}

    @staticmethod
    def _is_candidate(user, cutoff_ms: float, protected_uids: Set[str]) -> bool:
        """True only for a stale, password-only registration never used after
        its registration-time sign-in."""
        if user.email_verified or user.disabled:
            return False
        if user.uid in protected_uids:
            return False
        meta = user.user_metadata
        if not meta.creation_timestamp or meta.creation_timestamp >= cutoff_ms:
            return False
        window_end_ms = (
            meta.creation_timestamp
            + UnverifiedAccountCleanup.REGISTRATION_WINDOW_SECONDS * 1000
        )
        for ts in (meta.last_sign_in_timestamp, meta.last_refresh_timestamp):
            if ts and ts > window_end_ms:
                return False
        if not user.provider_data or any(
            p.provider_id != "password" for p in user.provider_data
        ):
            return False
        return True

    @classmethod
    def _delete_account(
        cls, uid: str, cutoff_ms: float, protected_uids: Set[str]
    ) -> bool:
        """Delete one account. Returns True if Firebase was deleted."""
        precheck = cls._db_precheck(uid)
        if precheck is None:
            return False
        user_id, bucket_name = precheck

        # Fresh record right before deletion: the account may have verified
        # (or signed in) since listing or during the DB precheck.
        try:
            fb_user = firebase_auth.get_user(uid)
        except firebase_auth.UserNotFoundError:
            if cls._deactivate_db_user(uid):
                logger.info(f"Reconciled DB user for already-deleted uid {uid}")
            return False

        if not cls._is_candidate(fb_user, cutoff_ms, protected_uids):
            logger.info(f"Skipping uid {uid}: no longer a cleanup candidate")
            return False

        # Firebase first: if this fails, the DB row is untouched and the
        # account is retried next run.
        firebase_auth.delete_user(uid)

        try:
            cls._deactivate_db_user(uid)
        except Exception as e:
            # The account is already unusable (login fails without Firebase),
            # but the row lingers active with no automatic repair path.
            logger.critical(
                f"Firebase account {uid} deleted but DB deactivation failed "
                f"for user {user_id}; manual cleanup required: {e}",
                exc_info=True,
            )
            return True

        cls._delete_user_bucket(user_id, bucket_name)
        logger.info(f"Deleted unverified account uid {uid} (user {user_id})")
        return True

    @classmethod
    def _db_precheck(cls, uid: str) -> Optional[Tuple[int, Optional[str]]]:
        """(user_id, bucket_name) if the DB row allows deleting uid, else None."""
        with session_scope() as db:
            user_db = db.query(UserModel).filter(UserModel.uid == uid).first()
            if user_db is None:
                logger.info(f"Skipping uid {uid}: no DB user in this environment")
                return None
            if cls._has_admin_role(db, user_db.id):
                logger.warning(f"Skipping uid {uid}: user {user_db.id} has admin role")
                return None
            if user_db.active and cls._edited_after_registration(user_db):
                logger.info(f"Skipping uid {uid}: DB row edited after registration")
                return None
            return user_db.id, user_db.remote_bucket_name

    @staticmethod
    def _edited_after_registration(user_db) -> bool:
        """An email change (which resets email_verified) touches updated_at."""
        if not user_db.created_at or not user_db.updated_at:
            return True
        return (
            user_db.updated_at - user_db.created_at
        ).total_seconds() > UnverifiedAccountCleanup.REGISTRATION_WINDOW_SECONDS

    @staticmethod
    def _has_admin_role(db, user_id: int) -> bool:
        return (
            db.query(UserRoleModel)
            .filter(
                UserRoleModel.user_id == user_id,
                UserRoleModel.role_id == UserRole.admin.value,
            )
            .first()
            is not None
        )

    @classmethod
    def _deactivate_db_user(cls, uid: str) -> bool:
        """Soft-delete the active, non-admin DB row for uid and write an
        audit record. Returns True if a row was deactivated."""
        with session_scope() as db:
            user_db = (
                db.query(UserModel)
                .filter(UserModel.uid == uid, UserModel.active.is_(True))
                .first()
            )
            if user_db is None:
                return False
            if cls._has_admin_role(db, user_db.id):
                logger.warning(
                    f"Not deactivating uid {uid}: user {user_db.id} has admin role"
                )
                return False
            user_db.active = False
            now = get_current_datetime()
            db.add(
                UserDeletionRecord(
                    user_id=user_db.id,
                    user_uid=uid,
                    step=DeletionStep.COMPLETED.value,
                    status=DeletionStatus.COMPLETED.value,
                    started_at=now,
                    completed_at=now,
                )
            )
        return True

    @staticmethod
    def _delete_user_bucket(user_id: int, bucket_name: Optional[str]):
        """Best-effort removal of the user's empty per-user bucket.

        Only a bucket whose name exactly matches the deterministic per-user
        formula is touched (never the shared bucket), and the delete is
        non-forced, so S3 itself refuses a non-empty bucket.
        """
        if not bucket_name or not RemoteStorageController.is_available():
            return

        expected = RemoteStorageController.create_user_bucket_name(
            id=user_id,
            prefix=os.environ.get("S3_USER_BUCKET_PREFIX", "optinist-user"),
        )
        if bucket_name != expected:
            logger.warning(
                f"Not deleting bucket {bucket_name!r} for user {user_id}: "
                "name does not match the per-user bucket formula"
            )
            return

        async def _delete():
            async with RemoteStorageSimpleWriter(bucket_name) as controller:
                await controller.delete_bucket(force_delete=False)

        try:
            # Safe in this worker thread: no event loop is running here.
            asyncio.run(_delete())
        except Exception as e:
            logger.warning(
                f"Could not delete bucket {bucket_name} for user {user_id}: {e}"
            )

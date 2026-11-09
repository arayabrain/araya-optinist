"""Tests for the unverified Firebase account cleanup job (issue #161)."""

import inspect
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from firebase_admin import auth as real_firebase_auth

from studio.app.common.core.background.unverified_account_cleanup_job import (
    UnverifiedAccountCleanupJob,
)
from studio.app.common.core.subscription.constants import UnverifiedAccountCleanup
from studio.app.common.models.subscription import UserDeletionRecord
from studio.app.common.models.user import User as UserModel

MODULE = "studio.app.common.core.background.unverified_account_cleanup_job"

NOW_MS = time.time() * 1000
OLD_MS = NOW_MS - 90 * 86400 * 1000  # 90 days old, well past the grace period
SECRET_EMAIL = "candidate-secret@example.com"


def _fb_user(
    uid="uid-1",
    email_verified=False,
    disabled=False,
    creation_timestamp=OLD_MS,
    last_sign_in_timestamp=None,
    last_refresh_timestamp=None,
    providers=("password",),
):
    user = MagicMock()
    user.uid = uid
    user.email = SECRET_EMAIL
    user.email_verified = email_verified
    user.disabled = disabled
    user.user_metadata.creation_timestamp = creation_timestamp
    user.user_metadata.last_sign_in_timestamp = last_sign_in_timestamp
    user.user_metadata.last_refresh_timestamp = last_refresh_timestamp
    user.provider_data = [MagicMock(provider_id=p) for p in providers]
    return user


REGISTERED_AT = datetime(2026, 1, 1)


def _db_user(user_id=5, active=True, bucket_name=None, edited_after_seconds=1):
    row = MagicMock()
    row.id = user_id
    row.active = active
    row.remote_bucket_name = bucket_name
    row.created_at = REGISTERED_AT
    row.updated_at = REGISTERED_AT + timedelta(seconds=edited_after_seconds)
    return row


def _mock_db(user_results, admin_row=None):
    """DB session mock: successive UserModel queries pop from user_results;
    any other model's query returns admin_row."""
    db = MagicMock()
    remaining = list(user_results)

    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        if model is UserModel:
            q.first.return_value = remaining.pop(0) if remaining else None
        else:
            q.first.return_value = admin_row
        return q

    db.query.side_effect = query
    return db


def _patch_session(db):
    scope = MagicMock()
    scope.return_value.__enter__ = MagicMock(return_value=db)
    scope.return_value.__exit__ = MagicMock(return_value=False)
    return patch(f"{MODULE}.session_scope", scope)


@pytest.fixture
def mock_fb():
    with patch(f"{MODULE}.firebase_auth") as fb:
        fb.UserNotFoundError = real_firebase_auth.UserNotFoundError
        yield fb


def _set_candidates(mock_fb, users):
    mock_fb.list_users.return_value.iterate_all.return_value = iter(users)


def test_run_is_sync():
    """The event-loop-safety design rests on run() being a plain function:
    AsyncIOScheduler executes sync jobs in a worker thread."""
    assert not inspect.iscoroutinefunction(UnverifiedAccountCleanupJob.run)


class TestIsCandidate:
    def _check(self, user, cutoff_ms=None, protected=frozenset()):
        if cutoff_ms is None:
            cutoff_ms = (
                time.time() - UnverifiedAccountCleanup.GRACE_PERIOD_DAYS * 86400
            ) * 1000
        return UnverifiedAccountCleanupJob._is_candidate(user, cutoff_ms, protected)

    def test_stale_never_used_password_account_is_candidate(self):
        assert self._check(_fb_user()) is True

    def test_verified_skipped(self):
        assert self._check(_fb_user(email_verified=True)) is False

    def test_disabled_skipped(self):
        assert self._check(_fb_user(disabled=True)) is False

    def test_protected_uid_skipped(self):
        assert self._check(_fb_user(uid="keep"), protected={"keep"}) is False

    def test_missing_creation_timestamp_skipped(self):
        assert self._check(_fb_user(creation_timestamp=None)) is False

    def test_grace_period_boundary(self):
        # Exactly at the cutoff is NOT older than the cutoff; 1ms older is.
        cutoff = 1_000_000.0
        assert self._check(_fb_user(creation_timestamp=1_000_000), cutoff) is False
        assert self._check(_fb_user(creation_timestamp=999_999), cutoff) is True

    def test_recent_account_skipped(self):
        assert self._check(_fb_user(creation_timestamp=NOW_MS)) is False

    def test_registration_time_sign_in_is_candidate(self):
        """Registration signs in once to send the verification email."""
        signed_in = OLD_MS + 2000
        user = _fb_user(
            last_sign_in_timestamp=signed_in, last_refresh_timestamp=signed_in
        )
        assert self._check(user) is True

    def test_sign_in_after_registration_window_skipped(self):
        window_ms = UnverifiedAccountCleanup.REGISTRATION_WINDOW_SECONDS * 1000
        at_edge = OLD_MS + window_ms
        assert self._check(_fb_user(last_sign_in_timestamp=at_edge)) is True
        late = at_edge + 1
        assert self._check(_fb_user(last_sign_in_timestamp=late)) is False

    def test_refresh_after_registration_window_skipped(self):
        late = OLD_MS + 86400 * 1000
        assert self._check(_fb_user(last_refresh_timestamp=late)) is False

    def test_empty_provider_data_skipped(self):
        assert self._check(_fb_user(providers=())) is False

    def test_non_password_provider_skipped(self):
        assert self._check(_fb_user(providers=("google.com",))) is False
        assert self._check(_fb_user(providers=("password", "google.com"))) is False


class TestMode:
    def test_defaults_to_dry_run(self, monkeypatch):
        monkeypatch.delenv(UnverifiedAccountCleanup.MODE_ENV, raising=False)
        assert UnverifiedAccountCleanupJob._is_delete_mode() is False

    def test_delete_enables(self, monkeypatch):
        monkeypatch.setenv(UnverifiedAccountCleanup.MODE_ENV, "delete")
        assert UnverifiedAccountCleanupJob._is_delete_mode() is True

    @pytest.mark.parametrize("value", [" delete", "DELETE", " DELETE "])
    def test_only_exact_delete_enables(self, monkeypatch, value):
        monkeypatch.setenv(UnverifiedAccountCleanup.MODE_ENV, value)
        assert UnverifiedAccountCleanupJob._is_delete_mode() is False

    def test_unknown_value_fails_closed(self, monkeypatch, caplog):
        monkeypatch.setenv(UnverifiedAccountCleanup.MODE_ENV, "enabled")
        with caplog.at_level("WARNING"):
            assert UnverifiedAccountCleanupJob._is_delete_mode() is False
        assert "falling back to dry_run" in caplog.text

    def test_protected_uids_parsing(self, monkeypatch):
        monkeypatch.setenv(UnverifiedAccountCleanup.PROTECTED_UIDS_ENV, "a, b,,c , ")
        assert UnverifiedAccountCleanupJob._get_protected_uids() == {"a", "b", "c"}


class TestRunDryRun:
    def test_dry_run_reports_what_delete_would_do_and_mutates_nothing(
        self, mock_fb, monkeypatch, caplog
    ):
        monkeypatch.delenv(UnverifiedAccountCleanup.MODE_ENV, raising=False)
        monkeypatch.delenv(UnverifiedAccountCleanup.PROTECTED_UIDS_ENV, raising=False)
        _set_candidates(mock_fb, [_fb_user(uid="stale-1"), _fb_user(uid="other-env")])
        row = _db_user()
        db = _mock_db([row, None])

        with _patch_session(db), caplog.at_level("INFO"):
            UnverifiedAccountCleanupJob.run()

        mock_fb.get_user.assert_not_called()
        mock_fb.delete_user.assert_not_called()
        db.add.assert_not_called()
        assert row.active is True
        assert "2 candidates" in caplog.text
        assert "dry_run" in caplog.text
        assert "1 would be deleted, uids: ['stale-1']" in caplog.text
        assert "Skipping uid other-env: no DB user" in caplog.text

    def test_no_email_ever_logged(self, mock_fb, monkeypatch, caplog):
        monkeypatch.delenv(UnverifiedAccountCleanup.MODE_ENV, raising=False)
        _set_candidates(mock_fb, [_fb_user()])

        with _patch_session(_mock_db([_db_user()])), caplog.at_level("DEBUG"):
            UnverifiedAccountCleanupJob.run()

        assert SECRET_EMAIL not in caplog.text

    def test_no_candidates(self, mock_fb, monkeypatch, caplog):
        monkeypatch.delenv(UnverifiedAccountCleanup.MODE_ENV, raising=False)
        _set_candidates(mock_fb, [_fb_user(email_verified=True)])

        with caplog.at_level("INFO"):
            UnverifiedAccountCleanupJob.run()

        assert "no candidates" in caplog.text

    def test_sweep_failure_is_caught_and_logged(self, mock_fb, monkeypatch, caplog):
        monkeypatch.delenv(UnverifiedAccountCleanup.MODE_ENV, raising=False)
        mock_fb.list_users.side_effect = ValueError("firebase unreachable")

        with caplog.at_level("ERROR"):
            UnverifiedAccountCleanupJob.run()  # must not raise

        assert "sweep failed" in caplog.text


class TestRunDelete:
    @pytest.fixture(autouse=True)
    def delete_mode(self, monkeypatch):
        monkeypatch.setenv(UnverifiedAccountCleanup.MODE_ENV, "delete")
        monkeypatch.delenv(UnverifiedAccountCleanup.PROTECTED_UIDS_ENV, raising=False)

    def test_deletes_firebase_then_deactivates_db_row(self, mock_fb):
        user = _fb_user(uid="stale-1")
        _set_candidates(mock_fb, [user])
        mock_fb.get_user.return_value = user
        row = _db_user(user_id=5)
        db = _mock_db([row, row])

        with _patch_session(db):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_called_once_with("stale-1")
        assert row.active is False
        added = db.add.call_args[0][0]
        assert isinstance(added, UserDeletionRecord)
        assert added.user_uid == "stale-1"
        assert added.user_id == 5

    def test_admin_role_user_skipped(self, mock_fb):
        user = _fb_user(uid="stale-1")
        _set_candidates(mock_fb, [user])
        mock_fb.get_user.return_value = user
        db = _mock_db([_db_user()], admin_row=MagicMock())

        with _patch_session(db):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_not_called()

    def test_no_db_row_skipped(self, mock_fb):
        user = _fb_user(uid="other-env")
        _set_candidates(mock_fb, [user])
        mock_fb.get_user.return_value = user
        db = _mock_db([None])

        with _patch_session(db):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_not_called()

    def test_inactive_db_row_still_deletes_firebase(self, mock_fb):
        """Leftover Firebase account from an earlier partial deletion."""
        user = _fb_user(uid="stale-1")
        _set_candidates(mock_fb, [user])
        mock_fb.get_user.return_value = user
        # Pre-check sees the inactive row; _deactivate_db_user (active filter)
        # then finds nothing to update.
        db = _mock_db([_db_user(active=False, edited_after_seconds=86400), None])

        with _patch_session(db):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_called_once_with("stale-1")
        db.add.assert_not_called()

    def test_verified_in_window_skipped(self, mock_fb):
        """User verified between listing and deletion: fresh re-check wins."""
        _set_candidates(mock_fb, [_fb_user(uid="stale-1")])
        mock_fb.get_user.return_value = _fb_user(uid="stale-1", email_verified=True)

        with _patch_session(_mock_db([_db_user()])):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_not_called()

    def test_verified_during_db_precheck_skipped(self, mock_fb):
        """The fresh Firebase read happens after the DB precheck closes."""
        _set_candidates(mock_fb, [_fb_user(uid="stale-1")])
        verified = {"now": False}

        def get_user(uid):
            return _fb_user(uid=uid, email_verified=verified["now"])

        mock_fb.get_user.side_effect = get_user
        db = _mock_db([_db_user()])
        scope = MagicMock()
        scope.return_value.__enter__ = MagicMock(return_value=db)
        scope.return_value.__exit__ = MagicMock(
            side_effect=lambda *a: verified.update(now=True) or False
        )

        with patch(f"{MODULE}.session_scope", scope):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_not_called()

    def test_db_row_edited_after_registration_skipped(self, mock_fb):
        """An email change resets email_verified and touches updated_at."""
        user = _fb_user(uid="stale-1")
        _set_candidates(mock_fb, [user])
        mock_fb.get_user.return_value = user
        db = _mock_db([_db_user(edited_after_seconds=86400)])

        with _patch_session(db):
            UnverifiedAccountCleanupJob.run()

        mock_fb.get_user.assert_not_called()
        mock_fb.delete_user.assert_not_called()

    def test_db_failure_after_firebase_delete_still_counts(self, mock_fb, caplog):
        """A Firebase delete consumes the cap even if DB cleanup fails."""
        first, second = _fb_user(uid="first"), _fb_user(uid="second")
        _set_candidates(mock_fb, [first, second])
        mock_fb.get_user.side_effect = [first, second]

        with patch.object(
            UnverifiedAccountCleanup, "MAX_ACCOUNTS_PER_RUN", 1
        ), _patch_session(_mock_db([_db_user(), _db_user()])), patch.object(
            UnverifiedAccountCleanupJob,
            "_deactivate_db_user",
            side_effect=RuntimeError("db down"),
        ), caplog.at_level(
            "INFO"
        ):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_called_once_with("first")
        assert "manual cleanup required" in caplog.text
        assert "1 deleted" in caplog.text

    def test_already_deleted_reconciles_db_row(self, mock_fb):
        _set_candidates(mock_fb, [_fb_user(uid="gone")])
        mock_fb.get_user.side_effect = real_firebase_auth.UserNotFoundError("gone")
        row = _db_user()
        db = _mock_db([row, row])

        with _patch_session(db):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_not_called()
        assert row.active is False
        db.add.assert_called_once()

    def test_one_failure_does_not_abort_sweep(self, mock_fb, caplog):
        good = _fb_user(uid="good")
        _set_candidates(mock_fb, [_fb_user(uid="bad"), good])
        mock_fb.get_user.side_effect = [ValueError("boom"), good]
        row = _db_user()
        db = _mock_db([row, row, row])

        with _patch_session(db), caplog.at_level("INFO"):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_called_once_with("good")
        assert "1 deleted" in caplog.text
        assert "1 errors" in caplog.text

    def test_cap_counts_successes_only(self, mock_fb):
        """A failing candidate must not consume the per-run budget."""
        good = _fb_user(uid="good")
        never = _fb_user(uid="never-reached")
        _set_candidates(mock_fb, [_fb_user(uid="bad"), good, never])
        mock_fb.get_user.side_effect = [ValueError("boom"), good, never]
        row = _db_user()
        db = _mock_db([row] * 5)

        with patch.object(
            UnverifiedAccountCleanup, "MAX_ACCOUNTS_PER_RUN", 1
        ), _patch_session(db):
            UnverifiedAccountCleanupJob.run()

        mock_fb.delete_user.assert_called_once_with("good")
        assert mock_fb.get_user.call_count == 2  # third candidate never reached


class TestBucketDeletion:
    def test_skipped_when_storage_unavailable(self):
        with patch(f"{MODULE}.RemoteStorageController") as rsc, patch(
            f"{MODULE}.RemoteStorageSimpleWriter"
        ) as writer:
            rsc.is_available.return_value = False
            UnverifiedAccountCleanupJob._delete_user_bucket(5, "some-bucket")
        writer.assert_not_called()

    def test_skipped_when_no_bucket_name(self):
        with patch(f"{MODULE}.RemoteStorageSimpleWriter") as writer:
            UnverifiedAccountCleanupJob._delete_user_bucket(5, None)
        writer.assert_not_called()

    def test_refuses_name_not_matching_per_user_formula(self, caplog):
        """The shared app bucket (or any foreign name) must never be deleted."""
        with patch(f"{MODULE}.RemoteStorageController") as rsc, patch(
            f"{MODULE}.RemoteStorageSimpleWriter"
        ) as writer, caplog.at_level("WARNING"):
            rsc.is_available.return_value = True
            rsc.create_user_bucket_name.return_value = "optinist-user-5-abc123"
            UnverifiedAccountCleanupJob._delete_user_bucket(5, "shared-app-bucket")
        writer.assert_not_called()
        assert "does not match" in caplog.text

    def test_deletes_matching_bucket_without_force(self):
        controller = MagicMock()
        controller.delete_bucket = AsyncMock()
        writer = MagicMock()
        writer.return_value.__aenter__ = AsyncMock(return_value=controller)
        writer.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch(f"{MODULE}.RemoteStorageController") as rsc, patch(
            f"{MODULE}.RemoteStorageSimpleWriter", writer
        ):
            rsc.is_available.return_value = True
            rsc.create_user_bucket_name.return_value = "optinist-user-5-abc123"
            UnverifiedAccountCleanupJob._delete_user_bucket(5, "optinist-user-5-abc123")

        controller.delete_bucket.assert_awaited_once_with(force_delete=False)

    def test_delete_failure_is_swallowed(self, caplog):
        controller = MagicMock()
        controller.delete_bucket = AsyncMock(side_effect=Exception("BucketNotEmpty"))
        writer = MagicMock()
        writer.return_value.__aenter__ = AsyncMock(return_value=controller)
        writer.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch(f"{MODULE}.RemoteStorageController") as rsc, patch(
            f"{MODULE}.RemoteStorageSimpleWriter", writer
        ), caplog.at_level("WARNING"):
            rsc.is_available.return_value = True
            rsc.create_user_bucket_name.return_value = "optinist-user-5-abc123"
            UnverifiedAccountCleanupJob._delete_user_bucket(
                5, "optinist-user-5-abc123"
            )  # must not raise

        assert "Could not delete bucket" in caplog.text

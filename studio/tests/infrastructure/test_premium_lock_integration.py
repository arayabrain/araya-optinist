"""Real-MySQL integration tests for distributed_lock and the assign claim.

Opt-in. The DB tests are skipped unless RUN_PREMIUM_LOCK_IT=1 with RDS_* env
pointing at a reachable MySQL (see docker-compose.premium-lock-it.yml). The
per-PR lambda lane collects this module but skips them (no DB there); the
premium-lock-it workflow runs them on PRs that touch the premium Lambdas.

Scope:
- distributed_lock (MySQL GET_LOCK, still used by the scaling / standby /
  migration locks) serializes concurrent sessions: mutual exclusion,
  serialization under contention, per-name isolation.
- assign_claim (a premium_assign_claims row) gives the same exclusion across
  sessions with the real INSERT / takeover UPDATE / renewal / DELETE
  statements, lets an expired claim be reclaimed, keeps a renewed one, and
  premium_cleanup reads only unexpired claims.
  The mocked per-PR tests run the same helpers against an in-memory table;
  this pins the SQL semantics on MySQL.
What this does NOT do is run the real assign_premium_user against a real DB:
that the critical section executes inside the claim is guarded by
TestConcurrentAssignClaim::test_assign_impl_runs_inside_the_claim and the
differential test (deterministic, per-PR).
"""

import os
import threading
import time
from unittest.mock import patch

import pytest

_opt_in = pytest.mark.skipif(
    os.environ.get("RUN_PREMIUM_LOCK_IT") != "1",
    reason="opt-in L3: set RUN_PREMIUM_LOCK_IT=1 with a real MySQL (RDS_* env)",
)


@pytest.fixture
def pm():
    # Imported lazily so collection (per-PR lambda lane) never needs a DB.
    # distributed_lock reads RDS_* / port at call time, not import time.
    import premium_manager

    return premium_manager


def _lock_name(suffix):
    # Suffix so the three tests in this process use distinct names.
    return f"ws5_{os.getpid()}_{suffix}"


def _lock_owner(pm, name):
    """Return the connection id currently holding GET_LOCK(name), or None if
    free, via an independent probe session (mirrors distributed_lock's connect).
    Positively distinguishes 'held by another session' from a NULL/error."""
    import pymysql

    host = os.environ["RDS_HOST"].split(":")[0]
    conn = pymysql.connect(
        host=host,
        port=pm.DatabaseConfig.DEFAULT_PORT,
        user=os.environ["RDS_USER"],
        password=os.environ["RDS_PASSWORD"],
        database=os.environ["RDS_DATABASE"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        ssl={"check_hostname": False},
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT IS_USED_LOCK(%s) AS owner", (name,))
            return cursor.fetchone()["owner"]
    finally:
        conn.close()


def test_opt_in_env_is_consistent():
    """Not skipped: if a DB is wired (RDS_HOST set) the opt-in flag must be set
    too, so a mis-propagated flag ERRORS here instead of the whole suite
    silently skipping and the harness reporting a hollow green."""
    if os.environ.get("RDS_HOST"):
        assert os.environ.get("RUN_PREMIUM_LOCK_IT") == "1", (
            "RDS_HOST is set but RUN_PREMIUM_LOCK_IT is not; the lock "
            "integration tests would silently skip"
        )


@_opt_in
def test_second_session_cannot_acquire_held_lock(pm):
    """A held GET_LOCK blocks a second (different-connection) session, and the
    name becomes acquirable again after release."""
    name = _lock_name("mutex")
    with pm.distributed_lock(name, timeout=5) as first:
        assert first is True
        # Positively confirm the lock is held by a session (rules out a NULL /
        # error result masquerading as contention below).
        assert _lock_owner(pm, name) is not None
        # distributed_lock opens a fresh connection per call, so this is a
        # distinct session; timeout=0 => immediate 0 while the first holds.
        with pm.distributed_lock(name, timeout=0) as second:
            assert second is False
    # First released on block exit; the name is free and acquirable again.
    assert _lock_owner(pm, name) is None
    with pm.distributed_lock(name, timeout=5) as third:
        assert third is True


@_opt_in
def test_concurrent_holders_are_serialized(pm):
    """Four real threads contending for one lock never hold it simultaneously,
    and all four complete their critical section (so the proof is not vacuous)."""
    name = _lock_name("serial")
    guard = threading.Lock()
    live = {"n": 0}
    max_live = {"n": 0}
    completed = {"n": 0}
    errors = []

    def worker():
        try:
            with pm.distributed_lock(name, timeout=30) as acquired:
                assert acquired is True
                with guard:
                    live["n"] += 1
                    max_live["n"] = max(max_live["n"], live["n"])
                time.sleep(0.2)  # hold the critical section so overlap would show
                with guard:
                    live["n"] -= 1
                    completed["n"] += 1
        except Exception as e:  # noqa: BLE001 - surface any thread failure
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    assert not any(t.is_alive() for t in threads)
    assert completed["n"] == 4  # every thread actually entered the lock
    assert max_live["n"] == 1  # but never two at once


@_opt_in
def test_distinct_lock_names_do_not_block(pm):
    """Different lock names are independent, so the scaling, standby and
    migration locks do not serialize each other. Meaningful together with
    the mutual-exclusion test above (which rejects an always-grant lock)."""
    held = _lock_name("iso_a")
    other = _lock_name("iso_b")
    with pm.distributed_lock(held, timeout=5) as a:
        assert a is True
        with pm.distributed_lock(other, timeout=0) as b:
            assert b is True


# Mirrors studio/alembic/versions/p901p1020029_add_premium_assign_claims.py
# minus the users foreign key: this throwaway schema has no users table.
_CLAIMS_DDL = """
CREATE TABLE IF NOT EXISTS premium_assign_claims (
    user_id BIGINT UNSIGNED NOT NULL,
    claim_token VARCHAR(32) NOT NULL,
    claimed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    PRIMARY KEY (user_id)
)
"""


def _execute(pm, sql, params=None, fetch=False):
    with pm.get_db_connection(auto_commit=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall() if fetch else None


@pytest.fixture
def claim_user_ids(pm):
    """Distinct user ids for this process, with their claim rows removed
    before and after the test."""
    base = 9_000_000 + (os.getpid() % 100_000) * 10
    ids = [base + i for i in range(4)]
    _execute(pm, _CLAIMS_DDL)
    placeholders = ", ".join(["%s"] * len(ids))
    delete_sql = f"DELETE FROM premium_assign_claims WHERE user_id IN ({placeholders})"
    _execute(pm, delete_sql, ids)
    yield ids
    _execute(pm, delete_sql, ids)


def _claim_token(pm, user_id):
    rows = _execute(
        pm,
        "SELECT claim_token FROM premium_assign_claims WHERE user_id = %s",
        (user_id,),
        fetch=True,
    )
    return rows[0]["claim_token"] if rows else None


@_opt_in
def test_assign_claim_excludes_a_second_session(pm, claim_user_ids):
    """A held claim refuses a second claimant until it is released; another
    user's claim is unaffected."""
    user, other = claim_user_ids[0], claim_user_ids[1]
    with pm.assign_claim(user, wait_seconds=0) as first:
        assert first is True
        assert _claim_token(pm, user) is not None
        with pm.assign_claim(user, wait_seconds=0) as second:
            assert second is False
        with pm.assign_claim(other, wait_seconds=0) as unrelated:
            assert unrelated is True
    assert _claim_token(pm, user) is None
    with pm.assign_claim(user, wait_seconds=0) as third:
        assert third is True


@_opt_in
def test_concurrent_claimants_are_serialized(pm, claim_user_ids):
    """Four real threads contending for one user's claim never hold it at
    once, and all four get it in turn within their bounded wait."""
    user = claim_user_ids[0]
    guard = threading.Lock()
    live = {"n": 0}
    max_live = {"n": 0}
    completed = {"n": 0}
    errors = []

    def worker():
        try:
            with pm.assign_claim(user, wait_seconds=30) as acquired:
                assert acquired is True
                with guard:
                    live["n"] += 1
                    max_live["n"] = max(max_live["n"], live["n"])
                time.sleep(0.2)  # hold the claim so overlap would show
                with guard:
                    live["n"] -= 1
                    completed["n"] += 1
        except Exception as e:  # noqa: BLE001 - surface any thread failure
            errors.append(e)

    with patch.object(pm, "ASSIGN_CLAIM_POLL_INTERVAL_SECONDS", 0.05):
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

    assert not errors, errors
    assert not any(t.is_alive() for t in threads)
    assert completed["n"] == 4
    assert max_live["n"] == 1


@_opt_in
def test_expired_claim_is_reclaimed_and_a_live_one_is_not(pm, claim_user_ids):
    """A row left by a crashed holder is taken over once expired; an
    unexpired one is not."""
    expired_user, live_user = claim_user_ids[0], claim_user_ids[1]
    _execute(
        pm,
        """INSERT INTO premium_assign_claims (user_id, claim_token, expires_at)
           VALUES (%s, 'crashed', NOW() - INTERVAL 1 SECOND),
                  (%s, 'running', NOW() + INTERVAL 600 SECOND)""",
        (expired_user, live_user),
    )

    with pm.assign_claim(live_user, wait_seconds=0) as taken:
        assert taken is False
    assert _claim_token(pm, live_user) == "running"

    with pm.assign_claim(expired_user, wait_seconds=0) as reclaimed:
        assert reclaimed is True
        token = _claim_token(pm, expired_user)
        assert token not in (None, "crashed")
        # The crashed holder's late release cannot delete the new claim.
        pm._release_assign_claim(expired_user, "crashed")
        assert _claim_token(pm, expired_user) == token
    assert _claim_token(pm, expired_user) is None


@_opt_in
def test_a_held_claim_is_renewed_past_its_ttl(pm, claim_user_ids):
    """With a 2 s TTL renewed every 0.5 s, a claim held for 4 s is never
    reclaimable by another session; after release it is free at once."""
    user = claim_user_ids[0]
    with patch.object(pm, "ASSIGN_CLAIM_TTL_SECONDS", 2), patch.object(
        pm, "ASSIGN_CLAIM_RENEW_INTERVAL_SECONDS", 0.5
    ):
        with pm.assign_claim(user, wait_seconds=0) as held:
            assert held is True
            for _ in range(8):
                time.sleep(0.5)
                with pm.assign_claim(user, wait_seconds=0) as contender:
                    assert contender is False
        with pm.assign_claim(user, wait_seconds=0) as after_release:
            assert after_release is True


@_opt_in
def test_claim_is_not_taken_while_a_previous_release_holds_the_session_lock(
    pm, claim_user_ids
):
    """Old holder, new contender: an invocation of the previous release
    holds GET_LOCK('assign_user_<id>') on its own session for its whole run.
    The new assign must not run beside it, and must leave no claim behind."""
    user = claim_user_ids[0]
    legacy_name = f"{pm.LEGACY_ASSIGN_LOCK_PREFIX}{user}"
    with pm.distributed_lock(legacy_name, timeout=5) as old_holder:
        assert old_holder is True
        with pm.assign_claim(user, wait_seconds=0) as new_contender:
            assert new_contender is False
        assert _claim_token(pm, user) is None
    with pm.assign_claim(user, wait_seconds=0) as after_old_run:
        assert after_old_run is True


@_opt_in
def test_without_the_claims_table_assign_serializes_on_the_session_lock(
    pm, claim_user_ids
):
    """Before the migration (the Lambdas are applied first), a real 1146
    switches assign to GET_LOCK('assign_user_<id>'): held by another session
    it is refused; free, it is taken and held for the block."""
    user = claim_user_ids[0]
    legacy_name = f"{pm.LEGACY_ASSIGN_LOCK_PREFIX}{user}"
    _execute(pm, "RENAME TABLE premium_assign_claims TO premium_assign_claims_hidden")
    try:
        with pm.distributed_lock(legacy_name, timeout=5) as other_session:
            assert other_session is True
            with pm.assign_claim(user, wait_seconds=0) as contender:
                assert contender is False
        with pm.assign_claim(user, wait_seconds=0) as holder:
            assert holder is True
            assert _lock_owner(pm, legacy_name) is not None
        assert _lock_owner(pm, legacy_name) is None
    finally:
        _execute(
            pm, "RENAME TABLE premium_assign_claims_hidden TO premium_assign_claims"
        )


@_opt_in
def test_cleanup_reads_only_unexpired_claims(pm, claim_user_ids):
    import premium_cleanup

    live_user, expired_user = claim_user_ids[2], claim_user_ids[3]
    _execute(
        pm,
        """INSERT INTO premium_assign_claims (user_id, claim_token, expires_at)
           VALUES (%s, 'live', NOW() + INTERVAL 600 SECOND),
                  (%s, 'old', NOW() - INTERVAL 1 SECOND)""",
        (live_user, expired_user),
    )

    claimed = premium_cleanup.get_assign_claimed_user_ids()

    assert live_user in claimed
    assert expired_user not in claimed

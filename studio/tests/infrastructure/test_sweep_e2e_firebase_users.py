"""Tests for the e2e Firebase sweep script.

The sweep deletes accounts, so the things worth asserting are which addresses it
selects, which it leaves alone, and that a partial delete is never reported as a
clean one.
"""

import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / ".github" / "scripts" / "sweep_e2e_firebase_users.py"

_spec = importlib.util.spec_from_file_location("sweep_e2e_firebase_users", MODULE_PATH)
sweeper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweeper)

# Every fixture address below is stamped well before this, so the grace period
# only spares accounts a test stamps deliberately close to it.
NOW_MS = 1787000000000


def user(email):
    return SimpleNamespace(uid=f"uid-{email}", email=email)


def stale_uids(users):
    return [u.uid for u in sweeper.stale_users(users, NOW_MS)]


SWEPT = [
    "e2e_unverified_1786520283944@test.com",
    "e2e_admin_created_operator_1786930000000@test.com",
    "e2e_admin_mutable_1786930000000@test.com",
]

SPARED = [
    # Fixed accounts the suite logs in as: no Date.now() suffix
    "e2e_ci_free@test.com",
    "e2e_ci_admin@test.com",
    "e2e_ci_lifecycle@test.com",
    "e2e_local_admin@test.com",
    # A real address
    "someone@araya.org",
    # Near misses on the timestamp
    "e2e_x_178652028394@test.com",
    "e2e_x_17865202839441@test.com",
    # Non-ASCII digits: \d would match these, [0-9] does not
    "e2e_x_١٢٣٤٥٦٧٨٩٠١٢٣@test.com",
    # A real address that merely contains a throwaway-shaped substring
    "victim+e2e_x_1786520283944@test.com",
    # Right shape, wrong domain
    "e2e_x_1786520283944@example.com",
    None,
]


@pytest.mark.parametrize("email", SWEPT)
def test_throwaway_accounts_are_swept(email):
    assert stale_uids([user(email)]) == [f"uid-{email}"]


@pytest.mark.parametrize("email", SPARED)
def test_every_other_account_is_spared(email):
    assert stale_uids([user(email)]) == []


def test_mixed_list_selects_only_the_throwaways():
    users = [user(email) for email in SWEPT + SPARED]
    assert stale_uids(users) == [f"uid-{email}" for email in SWEPT]


# Interpolations other than the timestamp, with a value they are known to take.
# Anything else fails the test: check what the expression can produce before
# adding it here, because the matcher admits only [a-z_] in the prefix.
KNOWN_INTERPOLATIONS = {"${role.toLowerCase()}": "operator"}


def rendered_spec_addresses():
    literal = re.compile(r"`(e2e_[^`]*@test\.com)`")
    for spec in sorted((REPO_ROOT / "frontend" / "e2e").glob("*.spec.ts")):
        for lit in literal.findall(spec.read_text()):
            rendered = lit.replace("${Date.now()}", "1786520283944")
            for expr, value in KNOWN_INTERPOLATIONS.items():
                rendered = rendered.replace(expr, value)
            yield spec.name, lit, rendered


# The regex admits only [a-z_] prefixes, so a spec that registers a new shape
# would silently opt out of cleanup. Render every literal the specs carry.
def test_every_throwaway_the_specs_register_is_matched():
    throwaways = [r for r in rendered_spec_addresses() if "Date.now()" in r[1]]
    assert throwaways, "no throwaway literals found: the spec naming moved"
    unknown = [r for r in throwaways if "${" in r[2]]
    assert unknown == [], "add the interpolation to KNOWN_INTERPOLATIONS"
    unmatched = [r for r in throwaways if not sweeper.THROWAWAY.fullmatch(r[2])]
    assert unmatched == []


def test_no_fixed_spec_account_is_matched():
    fixed = [r for r in rendered_spec_addresses() if "Date.now()" not in r[1]]
    matched = [r for r in fixed if sweeper.THROWAWAY.fullmatch(r[2])]
    assert matched == []


def stamped(age_ms):
    return f"e2e_admin_mutable_{NOW_MS - age_ms}@test.com"


# Concrete ages rather than offsets from GRACE_MS: expressed against the
# constant, these still pass when the grace period is mutated to zero. Three
# hours is a suite still inside playwright's 165-minute globalTimeout.
@pytest.mark.parametrize("age_ms", [0, 10 * 60 * 1000, 3 * 60 * 60 * 1000])
def test_a_concurrent_runs_accounts_are_left_alone(age_ms):
    assert stale_uids([user(stamped(age_ms))]) == []


def test_an_account_from_an_earlier_run_is_swept():
    email = stamped(6 * 60 * 60 * 1000)
    assert stale_uids([user(email)]) == [f"uid-{email}"]


class FakeAuth:
    """delete_users reports per-uid failures in its result rather than raising.

    Failures land on the LAST entries of each batch, so a batch-local index
    differs from the position in the full list and a wrong lookup is caught.
    """

    def __init__(self, failures_per_batch=0):
        self.batches = []
        self.deleted = []
        self.failures_per_batch = failures_per_batch

    def delete_users(self, uids):
        self.batches.append(len(uids))
        self.deleted += uids
        failing = range(max(len(uids) - self.failures_per_batch, 0), len(uids))
        errors = [SimpleNamespace(index=i, reason="QUOTA_EXCEEDED") for i in failing]
        return SimpleNamespace(success_count=len(uids) - len(errors), errors=errors)


def batch_of(count):
    return [user(f"e2e_batch_{1786520283944 + i}@test.com") for i in range(count)]


def test_deletes_are_batched_under_the_api_cap():
    auth = FakeAuth()
    assert sweeper.sweep(auth, batch_of(2500), NOW_MS) == 2500
    assert auth.batches == [1000, 1000, 500]


def test_sweep_deletes_exactly_the_stale_throwaways():
    auth = FakeAuth()
    users = [user(email) for email in SWEPT + SPARED] + [user(stamped(0))]
    assert sweeper.sweep(auth, users, NOW_MS) == len(SWEPT)
    assert auth.deleted == [f"uid-{email}" for email in SWEPT]


def test_a_partial_delete_is_not_reported_as_a_clean_sweep():
    auth = FakeAuth(failures_per_batch=3)
    users = batch_of(1500)
    with pytest.raises(RuntimeError, match="deleted 1494, 6 failed") as exc:
        sweeper.sweep(auth, users, NOW_MS)
    # every failed account is named: the last three of each batch
    failed = [users[i].uid for i in (997, 998, 999, 1497, 1498, 1499)]
    assert str(exc.value).endswith(str([f"{uid}: QUOTA_EXCEEDED" for uid in failed]))


def test_only_the_shared_test_project_may_be_swept(tmp_path):
    cred = tmp_path / "firebase_private.json"
    cred.write_text(json.dumps({"project_id": "araya-optinist-development"}))
    assert sweeper.credential_project(cred) == "araya-optinist-development"
    cred.write_text(json.dumps({"project_id": "araya-optinist-production"}))
    with pytest.raises(SystemExit, match="araya-optinist-production"):
        sweeper.credential_project(cred)
    cred.write_text("{}")
    with pytest.raises(SystemExit):
        sweeper.credential_project(cred)


def test_dry_run_selects_without_deleting():
    dry = sweeper.DryRun()
    users = [user(email) for email in SWEPT + SPARED]
    assert sweeper.sweep(dry, users, NOW_MS) == len(SWEPT)


def test_nothing_to_delete_makes_no_api_call():
    auth = FakeAuth()
    assert sweeper.sweep(auth, [user("e2e_ci_free@test.com")], NOW_MS) == 0
    assert auth.batches == []

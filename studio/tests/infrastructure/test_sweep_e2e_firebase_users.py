"""Tests for the e2e Firebase sweep script.

The sweep deletes accounts, so the only thing worth asserting is which addresses
it selects and which it leaves alone.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "infrastructure"
    / "scripts"
    / "sweep_e2e_firebase_users.py"
)

_spec = importlib.util.spec_from_file_location("sweep_e2e_firebase_users", MODULE_PATH)
sweeper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweeper)


def user(email):
    return SimpleNamespace(uid=f"uid-{email}", email=email)


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
    # A real address that merely contains a throwaway-shaped substring
    "victim+e2e_x_1786520283944@test.com",
    # Right shape, wrong domain
    "e2e_x_1786520283944@example.com",
    None,
]


@pytest.mark.parametrize("email", SWEPT)
def test_throwaway_accounts_are_swept(email):
    assert sweeper.stale_uids([user(email)]) == [f"uid-{email}"]


@pytest.mark.parametrize("email", SPARED)
def test_every_other_account_is_spared(email):
    assert sweeper.stale_uids([user(email)]) == []


def test_mixed_list_selects_only_the_throwaways():
    users = [user(email) for email in SWEPT + SPARED]
    assert sweeper.stale_uids(users) == [f"uid-{email}" for email in SWEPT]


def test_deletes_are_batched_under_the_api_cap():
    class FakeAuth:
        def __init__(self):
            self.batches = []

        def delete_users(self, uids):
            self.batches.append(len(uids))

    users = [user(f"e2e_batch_{1786520283944 + i}@test.com") for i in range(2500)]
    auth = FakeAuth()

    assert sweeper.sweep(auth, users) == 2500
    assert auth.batches == [1000, 1000, 500]

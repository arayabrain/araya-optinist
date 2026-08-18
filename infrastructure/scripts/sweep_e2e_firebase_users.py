#!/usr/bin/env python3
"""Delete the throwaway Firebase accounts left behind by interrupted e2e runs.

The e2e suite registers accounts named `<prefix>_<Date.now()>@test.com` and
removes them in its teardown. A run that dies first leaves the Firebase user
behind with no DB row, putting it out of reach of any DB-driven cleanup.

Run from the repo root inside the backend container:
    poetry run python infrastructure/scripts/sweep_e2e_firebase_users.py
"""

import re

import firebase_admin
from firebase_admin import auth, credentials

FIREBASE_PRIVATE_PATH = "studio/config/auth/firebase_private.json"

# The 13-digit Date.now() suffix every throwaway carries. The fixed accounts
# (e2e_ci_free, e2e_local_admin) have none, so they can never match.
THROWAWAY = re.compile(r"e2e_[a-z_]+_\d{13}@test\.com")

DELETE_BATCH = 1000  # firebase_admin's per-call cap for delete_users


def stale_uids(users):
    return [u.uid for u in users if THROWAWAY.fullmatch(u.email or "")]


def sweep(auth_module, users):
    uids = stale_uids(users)
    for i in range(0, len(uids), DELETE_BATCH):
        auth_module.delete_users(uids[i : i + DELETE_BATCH])
    return len(uids)


def main():
    firebase_admin.initialize_app(credentials.Certificate(FIREBASE_PRIVATE_PATH))
    print(sweep(auth, auth.list_users().iterate_all()))


if __name__ == "__main__":
    main()

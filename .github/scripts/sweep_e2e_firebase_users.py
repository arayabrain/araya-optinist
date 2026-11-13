"""Delete the throwaway Firebase accounts left behind by interrupted e2e runs.

The e2e suite registers accounts named `<prefix>_<Date.now()>@test.com` and
removes them in its teardown. A run that dies first leaves the Firebase user
behind with no DB row, putting it out of reach of any DB-driven cleanup.

Lives here rather than under infrastructure/scripts so it stays out of the
shipped image: the production Dockerfile copies that directory wholesale, and
this deletes by pattern rather than from a list.

Run from the repo root with an interpreter that can import firebase_admin:
    python .github/scripts/sweep_e2e_firebase_users.py [--dry-run]
Every address selected is listed on stderr; the last line of stdout is the count.
"""

import argparse
import json
import re
import sys
import time
from types import SimpleNamespace

import firebase_admin
from firebase_admin import auth, credentials

FIREBASE_PRIVATE_PATH = "studio/config/auth/firebase_private.json"

# The credential is whatever the checkout holds, so the project it names is
# checked before anything is deleted: only the shared test project may be swept.
ALLOWED_PROJECTS = {"araya-optinist-development"}

# The 13-digit Date.now() suffix every throwaway carries. The fixed accounts
# (e2e_ci_free, e2e_local_admin) have none, so they can never match.
THROWAWAY = re.compile(r"e2e_[a-z_]+_([0-9]{13})@test\.com")

# One Firebase project is shared by CI and local runs, so a concurrent suite
# still needs whatever it registered. Covers playwright.config's 165-minute
# globalTimeout, the longest a run can hold an account open.
GRACE_MS = 4 * 60 * 60 * 1000

DELETE_BATCH = 1000  # firebase_admin's per-call cap for delete_users


def credential_project(path):
    with open(path) as f:
        project = json.load(f).get("project_id", "")
    if project not in ALLOWED_PROJECTS:
        raise SystemExit(f"refusing to sweep Firebase project {project!r}")
    return project


def stale_users(users, now_ms):
    cutoff = now_ms - GRACE_MS
    return [
        u
        for u in users
        if (m := THROWAWAY.fullmatch(u.email or "")) and int(m.group(1)) < cutoff
    ]


def sweep(auth_module, users, now_ms):
    stale = stale_users(users, now_ms)
    for u in stale:
        print(f"sweeping {u.email}", file=sys.stderr)
    uids = [u.uid for u in stale]
    deleted, errors = 0, []
    for i in range(0, len(uids), DELETE_BATCH):
        batch = uids[i : i + DELETE_BATCH]
        # delete_users reports per-uid failures in its result instead of raising
        result = auth_module.delete_users(batch)
        deleted += result.success_count
        errors += [f"{batch[e.index]}: {e.reason}" for e in result.errors]
    if errors:
        raise RuntimeError(f"deleted {deleted}, {len(errors)} failed: {errors}")
    return deleted


class DryRun:
    """Stands in for firebase_admin.auth so the selection can be inspected."""

    def delete_users(self, uids):
        return SimpleNamespace(success_count=len(uids), errors=[])


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    print(f"project: {credential_project(FIREBASE_PRIVATE_PATH)}", file=sys.stderr)
    firebase_admin.initialize_app(credentials.Certificate(FIREBASE_PRIVATE_PATH))
    target = DryRun() if args.dry_run else auth
    count = sweep(target, auth.list_users().iterate_all(), time.time() * 1000)
    if args.dry_run:
        print(f"dry run: {count} would be deleted, none were", file=sys.stderr)
    print(count)


if __name__ == "__main__":
    main(sys.argv[1:])

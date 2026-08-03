# E2E Release Tests (Playwright)

Automates the browser-testable part of release verification. Each test has a
stable ID (`AUTH-01`, `WF-04`, ...) grouped by feature area; the release test
sheets reference these IDs, so a green run checks off the matching rows and a
release tester only hand-verifies what the coverage maps list as manual.

- [Quick start](#quick-start)
- [Test environment](#test-environment)
- [Credentials and test accounts](#credentials-and-test-accounts)
- [Running the tests](#running-the-tests)
- [How the suite works](#how-the-suite-works)
- [Test groups](#test-groups)
- [Coverage maps](#coverage-maps)
- [Troubleshooting](#troubleshooting)

## Quick start

```bash
cd frontend
yarn install
npx playwright install chromium

# credentials (see below) — this file is gitignored
cat > e2e/.env <<'EOF'
TEST_USER_EMAIL=<free-plan test account email>
TEST_USER_PASSWORD=<password>
TEST_PREMIUM_EMAIL=<premium test account email>   # optional
TEST_PREMIUM_PASSWORD=<password>                  # optional
BASE_URL=http://localhost:3003
EOF

yarn test:e2e
```

## Test environment

Tests run against any deployment of the app; pick one:

### A. Local stack (default)

Backend + DB in docker, frontend on the host (installing node_modules
through the docker bind mount is unusably slow, so don't use the
containerized frontend for e2e):

```bash
# from the repo root: db + backend
docker compose -f docker-compose.dev.multiuser.yml up -d db studio-dev-be

# frontend on the host — pick a free port and match BASE_URL
cd frontend && PORT=3003 BROWSER=none yarn start
```

First-time local DB setup: the `subscription_plans` table must have the
Free (id 1) and Premium (id 2) rows or registration 500s on a foreign key.
Seed with `infrastructure/scripts/seed_subscription_plans.py` (canonical,
reads `SUBSCRIPTION_PLANS_CONFIG`) or a minimal insert:

```sql
INSERT INTO subscription_plans (id, name, price, billing_cycle, features, currency, status)
VALUES (1, 'Free', 0, 1, JSON_OBJECT(), 1, 1), (2, 'Premium', 2000, 1, JSON_OBJECT(), 1, 1);
```

### B. Deployed environment

```
BASE_URL=https://<frontend-host>
API_URL=https://<backend-host>     # only if not BASE_URL with port 8000
```

The API is used by the global setup and test fixtures (workspace
find-or-create, cleanup). By default it's derived from `BASE_URL` by
swapping the port to 8000, which matches the local stack; deployed
environments usually need `API_URL` set explicitly.

**Warning:** tests create and delete workspaces named `e2e-*` and
copy/delete records inside them, and the global setup **deletes every
workspace whose name starts with `e2e-`** owned by the test account. Use a
dedicated test account; never point the suite at an account whose data you
care about.

## Credentials and test accounts

All credentials come from env vars, or `frontend/e2e/.env` (gitignored,
simple `KEY=VALUE` lines). Nothing is ever committed.

| Variable                                           | Required                          | Purpose                                                                                                                                                                                                                            |
| -------------------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TEST_USER_EMAIL` / `TEST_USER_PASSWORD`           | for logged-in tests               | free-plan account; without it only public/validation tests run, the rest skip                                                                                                                                                      |
| `TEST_PREMIUM_EMAIL` / `TEST_PREMIUM_PASSWORD`     | optional                          | enables SUB-04/05 (premium subscription state)                                                                                                                                                                                     |
| `TEST_LIFECYCLE_EMAIL` / `TEST_LIFECYCLE_PASSWORD` | optional, local stack only        | enables LC-01..16 (subscription/storage warning lifecycle). The spec registers and verifies this account itself on first run and rewrites its plan/expiry/usage in the docker DB — use a dedicated address, never a shared account |
| `BASE_URL`                                         | default `http://localhost:3000`   | frontend under test                                                                                                                                                                                                                |
| `API_URL`                                          | default `BASE_URL` with port 8000 | backend, for setup/cleanup API calls                                                                                                                                                                                               |
| `RUN_SLOW`                                         | optional                          | include the `@slow` workflow-run tests                                                                                                                                                                                             |

The account must exist in **both** Firebase (email/password sign-in,
email verified) and the target environment's DB. Note: the `test_users` in
`development.tfvars` have no passwords recorded — those accounts
authenticate via Admin-SDK-minted tokens in the load-test scripts and can't
be used for UI login as-is.

### Bootstrapping accounts for a local stack

Register through the API, then verify the email with the Firebase Admin SDK
(the backend container has the service-account key):

```bash
# 1. register (note role_id is required)
curl -X POST http://localhost:8000/api/register -H "Content-Type: application/json" \
  -d '{"name":"E2E Free","email":"<email>","password":"<password>","role_id":20}'
# → note the returned "uid"

# 2. mark the email verified
docker exec <backend-container> python -c "
import firebase_admin
from firebase_admin import auth, credentials
cred = credentials.Certificate('studio/config/auth/firebase_private.json')
firebase_admin.initialize_app(cred)
auth.update_user('<uid>', email_verified=True)"
```

For a premium account, additionally upgrade the plan **and** the storage
quota (two tables — forgetting the second leaves the account showing a 5GB
quota):

```sql
UPDATE subscription_users SET plan_id = 2,
  expiration = DATE_ADD(NOW(), INTERVAL 1 MONTH), scheduled_downgrade = 0
  WHERE user_id = <id>;
UPDATE user_storage_usage SET storage_quota_bytes = 214748364800  -- 200GB
  WHERE user_id = <id>;
```

On a local stack (no ECS), premium login shows a "Premium assignment
issue ... Falling back to shared resources" snackbar — expected.

The lifecycle spec (LC-01..16) additionally needs `SKIP_STORAGE_CHECKS=false`
in `studio/config/.env` (restart the backend after changing it). The
local-testing default of `true` disables all storage lookups backend-side —
deployed environments run with `false` — and the spec skips with a clear
reason while it's on.

## CI (GitHub Actions) secrets

The scheduled workflow (`.github/workflows/e2e.yml`) runs the local docker
stack inside the runner, so it needs every credential the local stack reads,
supplied as repo secrets. If any is unset the workflow still starts but writes
an empty config file and the backend fails to boot — the run then crawls to the
90-minute timeout instead of failing fast. Required:

| Secret / variable                                      | Written to                                 | Notes                                      |
| ------------------------------------------------------ | ------------------------------------------ | ------------------------------------------ |
| `E2E_FIREBASE_PRIVATE_JSON`                            | `studio/config/auth/firebase_private.json` | Firebase service account                   |
| `E2E_FIREBASE_CONFIG_JSON`                             | `studio/config/auth/firebase_config.json`  | Firebase web config                        |
| `E2E_STUDIO_ENV`                                       | `studio/config/.env`                       | backend/DB config; strip personal AWS keys |
| `E2E_TEST_USER_EMAIL` / `E2E_TEST_USER_PASSWORD`       | `TEST_USER_*` env                          | free-plan CI user (bootstrap registers it) |
| `E2E_TEST_PREMIUM_EMAIL` / `E2E_TEST_PREMIUM_PASSWORD` | `TEST_PREMIUM_*` env                       | premium CI user                            |
| `SUBSCRIPTION_PLANS_CONFIG` (variable, not secret)     | plan-seed step                             | pulled from the dev task definition        |

The lifecycle user needs no secret — the workflow hardcodes
`e2e_ci_lifecycle@test.com` and reuses `E2E_TEST_USER_PASSWORD`. Values and the
one-time `gh secret set` commands are in the internal drive doc linked from the
PR description.

## Running the tests

```bash
yarn test:e2e                    # everything except @slow (~5 min)
RUN_SLOW=1 yarn test:e2e         # everything including workflow runs
RUN_SLOW=1 npx playwright test --grep @slow   # only the workflow runs
yarn test:e2e 01-auth            # one group
npx playwright test -g "WS-06"   # one test case
yarn test:e2e:headed             # watch the browser
yarn test:e2e:report             # open the last HTML report
```

- Tests run sequentially in one worker (they share account state).
- Local runs get 1 automatic retry (CRA dev-server hydration occasionally
  swallows an early click); CI gets 2. A test listed as "flaky" passed on
  retry.
- `@slow` = WF-04/05/06, real workflow executions (5–10 min each; slower
  on an ARM Mac where the backend image is amd64-emulated). Keep the
  machine awake for these — `caffeinate -i RUN_SLOW=1 npx playwright test
--grep @slow` — sleep mid-run is the #1 cause of bogus failures.

## How the suite works

Understanding these makes failures much easier to read:

- **One login per run** (`global-setup.ts`): logs in through the UI once and
  saves storage state to `e2e/.auth/free.json` (gitignored); all authed
  specs reuse it via `test.use({ storageState })`. This exists because
  per-test Firebase logins hit rate limits. The auth spec (login/logout
  tests) and the premium describe still log in for real — that's what they
  test.
- **Startup cleanup** (`global-setup.ts`): deletes the test account's
  `e2e-*` workspaces via the API so leftovers can't push rows out of the
  virtualized workspace grid.
- **API-based setup, UI-based assertions** (`helpers.ts`): specs that need a
  workspace find-or-create it via the API (`ensureWorkspaceId`, reading the
  app's Bearer token from localStorage) and navigate straight to
  `/workspaces/{id}`. Only the workspace spec drives the grid UI, because
  the grid is what it tests.
- **Shared data workspace**: the workflow/record/file/dataview specs share
  one workspace named `e2e-data`; `ensureTutorialRecords` imports the sample
  data on first need, so the ~1 min import happens once per run and any spec
  can run standalone.
- **Skips are preconditions, not failures**: every `test.skip` carries a
  reason ("No experiment records", "requires a completed workflow run"). On
  a fresh local stack several tests skip; after `RUN_SLOW=1` produces run
  outputs, most of those execute. Missing credentials skip, never fail.

## Test groups

| Spec file          | IDs         | Covers                                                                                    |
| ------------------ | ----------- | ----------------------------------------------------------------------------------------- |
| `01-auth`          | AUTH-01..11 | login, logout, session persistence, unverified email, header nav, registration validation |
| `02-workspace`     | WS-01..06   | workspace create, list, navigate, storage reload, delete                                  |
| `03-workflow`      | WF-01..09   | sample data import, reproduce, tutorial runs (`@slow`), run validation, tabs              |
| `04-record`        | REC-01..09  | record list, parameters, copy, delete, workflow/Snakemake/NWB downloads                   |
| `05-file-handling` | FILE-01..04 | file tree dialog, wildcard filter, check-all, sidebar toggle                              |
| `06-dataview`      | DV-01..15   | table, filters, sort, pagination, dialogs, public access, thumbnails, publish             |
| `07-subscription`  | SUB-01..06  | free and premium plan UI, `/thanks` access guard                                          |
| `08-storage`       | STO-01..02  | under-quota login, premium assignment snackbar                                            |
| `09-visualize`     | VIS-01..05  | sidebar info, Cell-ROI plot, frame playback, second plot type, ROI editor                 |
| `10-uploads`       | UPL-01..04  | CSV and HDF5 node dialogs, image and HDF5 upload                                          |
| `11-lifecycle`     | LC-01..17   | plan, quota, expiry and inactivity lifecycle. Local stack only                            |

## Coverage maps

Which test sheet rows this suite checks off, and what stays manual, is tracked
outside this README so the two sheet families stay separate:

| Document                                                                                                               | Covers                                                                                                                               |
| ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| [`infrastructure/documentation/RELEASE_TEST_COVERAGE.md`](../../infrastructure/documentation/RELEASE_TEST_COVERAGE.md) | `Araya-OptiNiSt Release Test Cases Template` (`BT-1xx` .. `BT-11xx`) - row by row, and what each spec group leaves manual            |
| [`infrastructure/documentation/SYSTEM_TEST_COVERAGE.md`](../../infrastructure/documentation/SYSTEM_TEST_COVERAGE.md)   | `Araya-Optinist System Test Cases Template` - a separate, larger scheme, mostly covered by jest and pytest rather than by this suite |

The release map is also where per-test data preconditions are written down (how
the dataview and Visualize tests get their records without a `@slow` run).

## Troubleshooting

| Symptom                                                    | Cause / fix                                                                                                                                                                                                    |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Nearly everything skips with "TEST_USER_EMAIL ... not set" | `e2e/.env` missing or not readable; env vars beat the file                                                                                                                                                     |
| `global-setup` login times out                             | Dev server recompiling after idle/wake — retry; it attempts 3 logins with 60s each. Check `BASE_URL` actually serves the login form                                                                            |
| Tests hang for hours / half the suite flakes at once       | The machine slept mid-run. Use `caffeinate -i`, re-run                                                                                                                                                         |
| Registration/login 500s on a fresh local DB                | `subscription_plans` not seeded — see [Test environment](#test-environment)                                                                                                                                    |
| `Cannot find module` from a global npx playwright          | `frontend/node_modules` was pruned (e.g. `yarn install` after a lockfile change) — `yarn install && npx playwright install chromium`                                                                           |
| Premium account shows Free quota (5GB)                     | `user_storage_usage.storage_quota_bytes` not updated — see the premium bootstrap SQL                                                                                                                           |
| "Import sample data" does nothing                          | Known app quirk: the menu item silently no-ops until the workspace has loaded into the store, and its menu stays open over the page. The helpers wait for readiness and press Escape; do the same in new tests |
| Downloads never fire in new record tests                   | `snakemake-download-link` / `nwb-download-link` testids are on hidden anchors — click the `IconButton` in the same table cell instead                                                                          |

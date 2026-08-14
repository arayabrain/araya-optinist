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

# point the app at the backend — without REACT_APP_SERVER_* it derives the
# API host from window.location and calls itself, so the first request
# (GET /is_standalone) never answers. Both files are gitignored.
cp .env.example .env

# credentials (see below)
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
# (needs frontend/.env from the quick start, or the app calls itself for the API)
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
| `TEST_LIFECYCLE_EMAIL` / `TEST_LIFECYCLE_PASSWORD` | optional, local stack only        | enables LC-01..25 (subscription/storage warning lifecycle). The spec registers and verifies this account itself on first run and rewrites its plan/expiry/usage in the docker DB — use a dedicated address, never a shared account |
| `TEST_ADMIN_EMAIL` / `TEST_ADMIN_PASSWORD`         | optional, local stack only        | enables ADMIN-01..22 (Account Manager). Defaults to `e2e_ci_admin@test.com` and the free user's password. The spec registers this account itself and promotes it to admin with one `user_roles` UPDATE, because registration always lands as an operator — use a dedicated address |
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

The lifecycle spec (LC-01..23) additionally needs `SKIP_STORAGE_CHECKS=false`
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
| (none)                                                 | `TEST_LIFECYCLE_*`, `TEST_ADMIN_*` env     | fixed addresses reusing `E2E_TEST_USER_PASSWORD`; both specs bootstrap their own account, so no new secret is needed |
| `SUBSCRIPTION_PLANS_CONFIG` (variable, not secret)     | plan-seed step                             | pulled from the dev task definition        |

The lifecycle user needs no secret — the workflow hardcodes
`e2e_ci_lifecycle@test.com` and reuses `E2E_TEST_USER_PASSWORD`. Values and the
one-time `gh secret set` commands are in the internal drive doc linked from the
PR description.

## The weekly regression, and running it on a branch

Nothing opt-in runs per PR. `.github/workflows/e2e.yml` gathers all of it into
one Monday 00:00 UTC job: the Playwright suite with `RUN_SLOW=1`, the two
real-database pytest lanes (`make premium_lock_it`, `make workflow_count_it`),
and a re-run of the per-PR unit lanes. A sheet row whose e2e citation ends in
`@slow` is checked off by that run, not by a green PR.

The schedule only ever fires from the default branch. To exercise a feature
branch's version of the workflow before it merges, dispatch it against that
branch by name. A PR number is not a ref:

```bash
gh workflow run e2e.yml --ref <branch>
gh run list --workflow e2e.yml -L 3
```

`--ref` picks both the workflow definition and the checked-out code, so a job
that exists only on the branch still runs. Locally the same lanes are
`RUN_SLOW=1 yarn test:e2e`, `make premium_lock_it` and `make workflow_count_it`.

## Running the tests

```bash
yarn test:e2e                    # everything except @slow (~14 min, see below)
RUN_SLOW=1 yarn test:e2e         # everything including workflow runs
RUN_SLOW=1 npx playwright test --grep @slow   # only the workflow runs
yarn test:e2e 01-auth            # one group
npx playwright test -g "WS-06"   # one test case
yarn test:e2e:headed             # watch the browser
yarn test:e2e:report             # open the last HTML report
yarn test:e2e:cleanup            # delete the run's e2e-* data, on demand
```

- Tests run sequentially in one worker (they share account state).
- Local runs get 1 automatic retry (CRA dev-server hydration occasionally
  swallows an early click); CI gets 2. A test listed as "flaky" passed on
  retry.
- `@slow` = anything that performs a real workflow execution (5–10 min each;
  slower on an ARM Mac where the backend image is amd64-emulated). Keep the
  machine awake for these — `caffeinate -i RUN_SLOW=1 npx playwright test
--grep @slow` — sleep mid-run is the #1 cause of bogus failures. These groups
  are in this lane:
  - WF-04/05/06, the tutorial runs, which are the thing being tested.
  - REC-07, which downloads an NWB file. One exists only after a completed run,
    and global setup deletes the workspace at the start of every run.
  - the whole `Private Dataview` group of `06-dataview`, tagged on the describe.
    Only success records reach the dataview and the sample data ships no
    computed outputs, so its first test mints them with a real run.
  - VIS-02, whose `cell_roi` overlay is a suite2p_roi node output. The other
    VIS tests plot the sample TIFF, which the import does ship.
  - PUB-05/06 in `14-public`, which publish records and read their input data
    back anonymously on the public page. Tutorial1 carries the CSV and TIFF
    input nodes, Tutorial4 the HDF5 and MAT pair; minting either costs a real
    run when the records don't already exist.
- **The default lane runs no workflows**, which is what keeps it under 15
  minutes. The trade is that a default run leaves 23 release-sheet rows and 27
  system-sheet rows unchecked; their e2e citations are marked `@slow` in the
  sheets rather than counted as covered by a green default run.

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
  virtualized workspace grid. There is deliberately no teardown: a run's data
  survives until the next run, so it can be inspected. To drop it sooner, run
  the cleanup group (`12-cleanup`) — same `deleteE2eWorkspaces` helper, opt-in
  via `RUN_CLEANUP` so an ordinary run can never delete data mid-inspection.
  `DELETE /workspace/{id}` removes the workspace's input and output data with
  it, in S3 too when remote storage is on.
- **API-based setup, UI-based assertions** (`helpers.ts`): specs that need a
  workspace find-or-create it via the API (`ensureWorkspaceId`, reading the
  app's Bearer token from localStorage) and navigate straight to
  `/workspaces/{id}`. Only the workspace spec drives the grid UI, because
  the grid is what it tests.
- **Shared data workspace**: the workflow/record/file/dataview specs share
  one workspace named `e2e-data`; `ensureTutorialRecords` imports the sample
  data on first need, so the ~1 min import happens once per run and any spec
  can run standalone.
- **Data preconditions are asserted, not skipped**: the record and dataview
  specs used to open with `test.skip(!hasData)` over a probe that swallowed its
  own timeout, so an empty grid produced a skip — and a skip reads as a pass in
  the summary the release sheets are signed off against. They now wait for the
  rows they need and fail if they never arrive. Where the precondition costs a
  real workflow run, the test is `@slow` rather than silently paying for it on
  every default run.
- **A local run of `11-lifecycle`, `12-admin` or `13-account` fails rather than
  skips.** These groups are the only coverage several LC, ADMIN and ACC rows
  have, so on a local BASE_URL every reason they cannot execute (missing
  credentials, unreachable docker DB, `SKIP_STORAGE_CHECKS=true`) is a broken
  environment and is raised as an error naming the rows it leaves unverified.
  Off a local BASE_URL they still skip, because the DB writes they need are only
  reachable on the docker stack. A deployed smoke run therefore leaves those
  rows unverified.

## Test groups

| Spec file          | IDs         | Covers                                                                                                      |
| ------------------ | ----------- | ----------------------------------------------------------------------------------------------------------- |
| `01-auth`          | AUTH-01..17 | login, logout, session persistence, unverified email and resend, header nav, registration validation, the logged-out guard on protected routes |
| `02-workspace`     | WS-01..07   | workspace create, list, navigate, storage reload, one refresh per session, delete                           |
| `03-workflow`      | WF-01..09   | sample data import, reproduce, tutorial runs (`@slow`), run validation, tabs                                |
| `04-record`        | REC-01..09  | record list, parameters, copy, delete, workflow/Snakemake/NWB downloads                                     |
| `05-file-handling` | FILE-01..06 | file tree dialog, wildcard filter, check-all, sidebar toggle, sync progress indicators (file tree and CSV settings) |
| `06-dataview`      | DV-01..20   | table, filters (incl. workspace, private and public), sort order, pagination, dialogs, public access, thumbnails, publish, concurrent public reads, rapid-toggle last-action-wins and concurrent-publish version integrity. DV-01..08 and DV-12..20 are `@slow` (DV-20 additionally needs the local docker DB and skips elsewhere); DV-09/10/11/18 need no records and run by default |
| `07-subscription`  | SUB-01..19  | free and premium plan UI, per-card feature lists, responsive widths, `/thanks` guard, invoice page, cancel / reactivate, checkout and portal hand-offs, browser-Back out of checkout, the upgrade click-storm guard, two-tab premium consistency |
| `08-storage`       | STO-01..09  | under-quota login, the over-quota modal, dedicated / shared / still-scaling premium assignment snackbars, storage-bar colours by threshold, warning-dismissal persistence and its logout reset, the reload button's in-flight state |
| `09-visualize`     | VIS-01..05  | sidebar info, Cell-ROI plot, frame playback, second plot type, ROI editor                                   |
| `10-uploads`       | UPL-01..07  | CSV, HDF5 and MAT node dialogs, image / HDF5 / MAT upload                                                   |
| `11-lifecycle`     | LC-01..25   | plan, quota, expiry, cancellation / renewal and inactivity lifecycle, plus free-logout DB bookkeeping and its re-login reset. Local stack only |
| `12-admin`         | ADMIN-01..22 | admin Account Manager: access gating (drawer entry and dashboard tile), user list columns, sort and rows-per-page, edit / add / delete / proxy-signin / subscription modals and their Cancel paths, the create / edit / role-change / demotion happy paths and their validation, the subscription and storage columns against the DB, one real deletion of a throwaway account, and re-registration of the deleted address. All mutations land on disposable per-run accounts. Local stack only |
| `13-account`       | ACC-01..06  | Account Profile self-service: change-password modal (validation, wrong current password, a real change verified at login) and the inline name edit, on a disposable per-run account. Local stack only |
| `14-public`        | PUB-01..06  | public-instance behaviour: deep-link SPA shell and client routing, `/health`, chunk-load auto-reload, frontend error reporting, and anonymous public-page loads of published HDF5 / MAT / CSV / TIFF input data. PUB-05/06 are `@slow` (they mint and publish real records) |
| `12-cleanup`       | CLEAN-01    | on-demand deletion of the account's `e2e-*` workspaces. Skipped unless `RUN_CLEANUP=1`    |

## Coverage maps

Which test sheet rows this suite checks off, and what stays manual, is tracked
outside this README so the two sheet families stay separate:

| Document                                                                                                               | Covers                                                                                                                               |
| ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| [`infrastructure/documentation/RELEASE_TEST_COVERAGE.md`](../../infrastructure/documentation/RELEASE_TEST_COVERAGE.md) | `Araya-OptiNiSt Release Test Cases Template` (`BT-1xx` .. `BT-11xx`) - row by row, and what each spec group leaves manual            |
| [`infrastructure/documentation/SYSTEM_TEST_COVERAGE.md`](../../infrastructure/documentation/SYSTEM_TEST_COVERAGE.md)   | `Araya-Optinist System Test Cases Template` - a separate, larger scheme, mostly covered by jest and pytest rather than by this suite |

The release map is also where per-test data preconditions are written down (how
the dataview and Visualize tests get their records, and why that puts most of
them in the `@slow` lane).

## Troubleshooting

| Symptom                                                                    | Cause / fix                                                                                                                                                                                                                                                                                                              |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Nearly everything skips with "TEST_USER_EMAIL ... not set"                 | `e2e/.env` missing or not readable; env vars beat the file                                                                                                                                                                                                                                                               |
| `global-setup` login times out                                             | Dev server recompiling after idle/wake — retry; it attempts 3 logins with 60s each. Check `BASE_URL` actually serves the login form                                                                                                                                                                                      |
| Tests hang for hours / half the suite flakes at once                       | The machine slept mid-run. Use `caffeinate -i`, re-run                                                                                                                                                                                                                                                                   |
| Registration/login 500s on a fresh local DB                                | `subscription_plans` not seeded — see [Test environment](#test-environment)                                                                                                                                                                                                                                              |
| `Cannot find module` from a global npx playwright                          | `frontend/node_modules` was pruned (e.g. `yarn install` after a lockfile change) — `yarn install && npx playwright install chromium`                                                                                                                                                                                     |
| Premium account shows Free quota (5GB)                                     | `user_storage_usage.storage_quota_bytes` not updated — see the premium bootstrap SQL                                                                                                                                                                                                                                     |
| "Import sample data" does nothing, or the click times out on the menu `ul` | Two causes, both handled by `importSampleData`: the item is disabled off the Record tab, and a disabled MUI item passes pointer events to its parent list, so switch to the Record tab first; and the menu silently no-ops until `GET /workspace/{id}` has populated the store, so wait for the workspace name to render |
| Downloads never fire in new record tests                                   | `snakemake-download-link` / `nwb-download-link` testids are on hidden anchors — click the `IconButton` in the same table cell instead                                                                                                                                                                                    |

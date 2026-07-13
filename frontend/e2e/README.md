# E2E Release Tests (Playwright)

Automates the browser-testable part of release verification. Each test has a
stable ID (`AUTH-01`, `WF-04`, ...) grouped by feature area; the release test
sheets reference these IDs, so a green run checks off the matching rows and a
release tester only hand-verifies what's listed as manual below.

- [Quick start](#quick-start)
- [Test environment](#test-environment)
- [Credentials and test accounts](#credentials-and-test-accounts)
- [Running the tests](#running-the-tests)
- [How the suite works](#how-the-suite-works)
- [Test groups and coverage](#test-groups-and-coverage)
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

| Variable | Required | Purpose |
|---|---|---|
| `TEST_USER_EMAIL` / `TEST_USER_PASSWORD` | for logged-in tests | free-plan account; without it only public/validation tests run, the rest skip |
| `TEST_PREMIUM_EMAIL` / `TEST_PREMIUM_PASSWORD` | optional | enables SUB-04/05 (premium subscription state) |
| `TEST_LIFECYCLE_EMAIL` / `TEST_LIFECYCLE_PASSWORD` | optional, local stack only | enables LC-01..16 (subscription/storage warning lifecycle). The spec registers and verifies this account itself on first run and rewrites its plan/expiry/usage in the docker DB — use a dedicated address, never a shared account |
| `BASE_URL` | default `http://localhost:3000` | frontend under test |
| `API_URL` | default `BASE_URL` with port 8000 | backend, for setup/cleanup API calls |
| `RUN_SLOW` | optional | include the `@slow` workflow-run tests |

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

## Test groups and coverage

| Group (spec file) | IDs | Automated | Stays manual |
|---|---|---|---|
| Auth (`01-auth`) | AUTH-01..11 | login, logout, session persistence, unverified-email flow, header navigation, registration validation | — |
| Workspace (`02-workspace`) | WS-01..06 | create, list, navigation, storage reload, delete | — |
| Workflow (`03-workflow`) | WF-01..09 | sample data import, reproduce, tutorial runs (`@slow`), run validation, tab navigation | exact no-algorithm-nodes message (needs manual node wiring) |
| Record (`04-record`) | REC-01..09 | list, expand parameters, copy, delete (single and multi-select), workflow/snakemake/NWB downloads | — |
| File handling (`05-file-handling`) | FILE-01..04 | file tree dialog, wildcard filter, check-all, sidebar toggle | — |
| Uploads & node dialogs (`10-uploads`) | UPL-01..04 | CSV param dialog, HDF5 structure dialog, image/HDF5 upload appears in inputs | MAT structure dialog; S3-side verification |
| Dataview (`06-dataview`) | DV-01..15 | table display, ID/name column-menu filters, sort, pagination, inputs/outputs/details dialogs, public access, public/private API auth, image/ROI thumbnails, publish/unpublish + public listing, bulk publish/unpublish with confirmation | DB/S3 sync verification, sync status states |
| Subscription (`07-subscription`) | SUB-01..06 | free and premium plan UI state, `/thanks` access guard | Stripe checkout/registration, DB/Stripe dashboard verification |
| Storage (`08-storage`) | STO-01..02 | no-warning login under quota, premium-login assignment snackbar | S3 verification, over-quota states, auto-refresh tracing |
| Visualize (`09-visualize`) | VIS-01..05 | sidebar workspace/workflow info, Cell-ROI image plot, frame playback, second plot type, ROI editor open/cancel | Edit ROI commit (OK mutates ROI data and starts a processing run) |
| Lifecycle (`11-lifecycle`, local stack only) | LC-01..14 | free baseline, upgrade, over-quota warning modal (110%), usage-high indicator (95%), storage reload reset, expired-premium grace warning, overdue acknowledgment modal, downgraded-free over-quota warning, run blocked/warned at quota, expiration captions, cancel-subscription dialog, cancelled banner, inactivity warning + Stay Active (fake clock), 2h auto-release beacon, account deletion | real Stripe upgrade/downgrade, real S3 usage, reactivation/cancel API calls (the spec drives plan/expiry/usage in the docker DB and mocks premium assignment for the inactivity tests) |

Not automated at all (out of browser-test scope): premium instance
provisioning, AWS monitoring.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Nearly everything skips with "TEST_USER_EMAIL ... not set" | `e2e/.env` missing or not readable; env vars beat the file |
| `global-setup` login times out | Dev server recompiling after idle/wake — retry; it attempts 3 logins with 60s each. Check `BASE_URL` actually serves the login form |
| Tests hang for hours / half the suite flakes at once | The machine slept mid-run. Use `caffeinate -i`, re-run |
| Registration/login 500s on a fresh local DB | `subscription_plans` not seeded — see [Test environment](#test-environment) |
| `Cannot find module` from a global npx playwright | `frontend/node_modules` was pruned (e.g. `yarn install` after a lockfile change) — `yarn install && npx playwright install chromium` |
| Premium account shows Free quota (5GB) | `user_storage_usage.storage_quota_bytes` not updated — see the premium bootstrap SQL |
| "Import sample data" does nothing | Known app quirk: the menu item silently no-ops until the workspace has loaded into the store, and its menu stays open over the page. The helpers wait for readiness and press Escape; do the same in new tests |
| Downloads never fire in new record tests | `snakemake-download-link` / `nwb-download-link` testids are on hidden anchors — click the `IconButton` in the same table cell instead |

## Appendix: release-sheet coverage map

Maps every row of the "Araya-OptiNiSt Release Test Cases Template"
(renumbered 2026-07-10) to its automated test. Subjects are included so rows
stay findable if the sheet is renumbered again. "manual" = no automated
counterpart; a green run checks off exactly the non-manual rows.

AUTH-09/10/11 (registration empty fields / password mismatch / password
complexity) have no release-sheet row — they cover the System-sheet
registration validation cases.

#### 01 Login & Auth

| Sheet row | Subject | Test |
|---|---|---|
| BT-101 | Successful login | AUTH-01 |
| BT-102 | Invalid credentials | AUTH-02 |
| BT-103 | Empty fields validation | AUTH-03 |
| BT-104 | Unverified email login | AUTH-04 |
| BT-105 | Successful logout | AUTH-05 |
| BT-106 | Session persistence | AUTH-06 |
| BT-107 | Logo navigation | AUTH-07 |
| BT-108 | Dashboard button (logged in) | AUTH-08 |

#### 02 Workspace

| Sheet row | Subject | Test |
|---|---|---|
| BT-201 | Create new workspace | WS-01 |
| BT-202 | Workspace list display | WS-02 |
| BT-203 | Access workspace | WS-03 |
| BT-204 | Storage refresh | WS-04 |
| BT-205 | Dataview access | WS-05 |
| BT-206 | Delete workspace | WS-06 |

#### 03 Workflow Execution

| Sheet row | Subject | Test |
|---|---|---|
| BT-301 | Access workflow page | WF-01 |
| BT-302 | Import sample data | WF-02 |
| BT-303 | Reproduce workflow from record | WF-03 |
| BT-304 | Run Tutorial 1 workflow | WF-04 `@slow` (by-uid RUN) |
| BT-305 | Run Tutorial 2 workflow | WF-05 `@slow` (RUN ALL, full compute) |
| BT-306 | Run Tutorial 3 workflow | WF-06 `@slow` (RUN ALL, full compute) |
| BT-307 | Run without algorithm nodes | WF-07 (see note) |
| BT-308 | Run without input file | WF-07 |
| BT-309 | Run button cooldown | WF-08 (snackbar dedupe only; the run-POST debounce itself stays manual) |
| BT-310 | Tab navigation | WF-09 |
| BT-311 | File tree display | FILE-01 |
| BT-312 | File filter with wildcards | FILE-02 |
| BT-313 | Check all / uncheck all | FILE-03 |
| BT-314 | Sidebar toggle | FILE-04 |
| BT-315 | HDF5 file dialog | UPL-02 |
| BT-316 | CSV parameter dialog | UPL-01 |

Note: a fresh workspace surfaces the input-file message before the
algorithm-nodes one, so WF-07 accepts either; verifying the exact
no-algorithm-nodes message needs an input file wired in manually.

#### 04 Visualize

| Sheet row | Subject | Test |
|---|---|---|
| BT-401 | Open Visualize tab | VIS-01 |
| BT-402 | Confirm workflow info in sidebar | VIS-01 |
| BT-403 | Add Cell ROI plot | VIS-02 |
| BT-404 | Play visualization image | VIS-03 |
| BT-405 | Add additional plot type | VIS-04 |
| BT-406 | Image thumbnail display | DV-12 |
| BT-407 | Run Edit ROI | VIS-05 (editor open + Cancel; the OK commit mutates ROI data and stays manual) |

Note (how the data-backed tests avoid @slow runs): the sample-data import
ships WITH pre-computed outputs, so the Visualize plot editor has plottable
items right after a reproduce — no run needed for VIS-02..05. The dataview
needs a success record + thumbnails, which only a completed run writes;
`ensureCompletedTutorialRun` reruns the imported Tutorial1 by uid, which
snakemake treats as already complete (~15s, not 5-10 min). Two gotchas the
helper absorbs: loading a finished experiment fires a phantom "Workflow
finished" snackbar (it anchors on the run POST instead), and the success
record is written shortly AFTER the finished signal (DV-12 reload-polls the
grid).

#### 05 Record Management

| Sheet row | Subject | Test |
|---|---|---|
| BT-501 | Access record page | REC-01 |
| BT-502 | View workflow details | REC-02 |
| BT-503 | Copy single record | REC-03 |
| BT-504 | Copy multiple records | REC-08 |
| BT-505 | Delete single record | REC-04 |
| BT-506 | Delete multiple records | REC-09 |
| BT-507 | Download workflow file | REC-05 |
| BT-508 | Download Snakemake file | REC-06 |
| BT-509 | Download NWB file | REC-07 |

#### 06 Premium Features

| Sheet row | Subject | Test |
|---|---|---|
| BT-604 | Premium profile display | SUB-05 |
| BT-605 | Premium subscription page | SUB-04 |
| BT-611 | Inactivity warning | LC-14 (frontend lifecycle via fake clock; backend heartbeat/CloudWatch stays manual) |
| BT-613 | Auto-release after 2h inactivity | LC-15 (frontend half: release beacon fires; instance-side release stays manual) |
| BT-615 | Instance release on browser close | LC-15 partial (proves the release-beacon plumbing; the beforeunload trigger stays manual) |
| BT-612 | Stay Active button | LC-14 (dismiss + timer reset; DB heartbeat verification stays manual) |
| BT-601/602 | assignment snackbars | STO-02 (strict success/preparing assertion when run against a deployed env) |
| BT-603, 606..610, 614 | instance assignment, concurrency, release (AWS state) | manual |

#### 07 Dataview

| Sheet row | Subject | Test |
|---|---|---|
| BT-701 | Private Dataview Table Display | DV-01 |
| BT-702 | Publish Toggle Display | DV-02 |
| BT-703 | Publish Experiment | DV-14 (toggle + public listing; DB/S3 sync verification manual) |
| BT-704 | Unpublish Experiment | DV-14 |
| BT-705 | Bulk Publish | DV-15 |
| BT-706 | Bulk Unpublish | DV-15 |
| BT-707 | Public Dataview Table Display | DV-09 |
| BT-708 | Public Dataview Unauthenticated Access | DV-10 |
| BT-709 | UID Filter | DV-03 (column-menu filter) |
| BT-710 | Name Filter | DV-13 (column-menu filter) |
| BT-711 | Workspace Filter (Public Only) | manual |
| BT-712 | Sort by Column Header | DV-04 |
| BT-713 | Change Page Size | DV-05 |
| BT-714 | Inputs Dialog Display | DV-06 |
| BT-715 | Outputs Dialog Display | DV-07 |
| BT-716 | Workflow Details Dialog Display | DV-08 |
| BT-717 | Close Dialog | DV-08 |
| BT-720 | Image Thumbnail Display | DV-12 |
| BT-721 | ROI Thumbnail Display | DV-12 |
| BT-718, 719 | sync status, retry | manual |

Note (dataview data preconditions): the records the data-dependent tests
need are minted once per run before any of them execute — a fast no-op rerun
of the imported Tutorial1 plus a record copy of it (~2 min; only Tutorial1's
rerun is a reliable no-op, Tutorial2's recomputes CaImAn locally and fails).
Publishing requires a cloud bucket on the account, so on a local stack the
suite sets a placeholder `remote_bucket_name` attribute on the test user
(deployed users have real buckets; the S3 sync itself stays manual).

#### 08 Subscription

| Sheet row | Subject | Test |
|---|---|---|
| BT-801 | Free Plan card display | SUB-01 |
| BT-802 | Free account status display | SUB-02 |
| BT-803 | No invoice for Free user | SUB-03 |
| BT-804 | Premium plan status display | SUB-04 |
| BT-805 | Premium account status display | SUB-05 |
| BT-806 | Expiration date text | LC-11 (exact caption per state; sheet says "renews on" but the UI text is "Renew on") |
| BT-807..811 | DB / Stripe dashboard verification | manual |

#### 09 Subscription Registration

| Sheet row | Subject | Test |
|---|---|---|
| BT-906 | Prevent direct access to /thanks | SUB-06 |
| BT-907 | Subscription page updated to Premium | SUB-04 (standing premium account) |
| BT-908 | Account Profile updated to Premium | SUB-05 (standing premium account) |
| BT-917 | Initiate downgrade | LC-12 (confirmation modal + 30-day retention notice) |
| BT-918 | Cancel downgrade (click No) | LC-12 |
| BT-920 | Reactivation option | LC-13 (banner + Continue Plan visible; clicking it is Stripe-backed, manual) |
| BT-922 | Expired premium user buttons | LC-06 (Upgrade + Manage both visible) |
| BT-925 | Delete test user account | LC-16 (per-run throwaway account; active=0 + deletion records completed) |
| BT-901..905, 909..916, 919, 921, 923, 924 | checkout flow, DB/Stripe verification, confirmed cancel/reactivation | manual |

#### 10 Storage

| Sheet row | Subject | Test |
|---|---|---|
| BT-1001 | Free user login - no warning | STO-01 |
| BT-1005 | Upload image data | UPL-03 (UI half — file appears in inputs; S3 verification manual) |
| BT-1006 | Upload HDF5 file | UPL-04 (UI half — file appears in inputs; S3 verification manual) |
| BT-1007 | Premium user login | STO-02 |
| BT-1010 | Storage limit exceeded warning on login | LC-03 (premium) / LC-08 (free) |
| BT-1011 | Handle Later button | LC-03 (dismisses, stays on dashboard) |
| BT-1012 | Manage Files button | LC-08 (redirects to /workspaces) |
| BT-1013 | Cannot run workflow when over limit | LC-09 |
| BT-1014 | Storage 90-99% warning on RUN | LC-10 (snackbar + run not blocked) |
| BT-1015 | Manual storage refresh | WS-04 |
| BT-1016 | Storage values update after delete | LC-05 (delete ballast → Reload clears warning) |
| BT-1002..1004, 1008, 1009 | S3-side verification | manual (the LC rows drive real files locally, but the S3 bucket half needs a deployed env) |

#### 11 AWS Monitoring

| Sheet row | Subject | Test |
|---|---|---|
| BT-1110 | Public Dataview access + auth guard | DV-11 (API half; point BASE_URL/API_URL at the env) |
| BT-1101..1109, 1111 | AWS CLI / console probes | manual |

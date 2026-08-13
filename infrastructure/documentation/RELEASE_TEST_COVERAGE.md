# Release Test Sheets: Automated Coverage Map

## Executive Summary

- **Maps every row** of the `Araya-OptiNiSt Release Test Cases Template` sheets (`BT-1xx` .. `BT-11xx`) to the automated test that covers it, so a release tester only hand-verifies the rows marked manual
- **85 of 104 exported rows automated** - a green Playwright run checks off exactly the non-manual rows
- **Mostly Playwright** - these sheets are the browser-testable release checklist, so nearly every entry is an e2e ID from `frontend/e2e/`; a few premium rows are covered by jest instead
- **Release sheets only** - the `Araya-Optinist System Test Cases Template` sheets are a separate, much larger scheme mapped in `infrastructure/documentation/SYSTEM_TEST_COVERAGE.md`
- **The two schemes do not correspond by trailing digits** - `BT-604` is "Premium profile display", not the System sheet's `6204` concurrency race
- **Fully manual sheets:** 11 AWS Monitoring (live AWS probes) and the Stripe-dashboard tail of 08 Subscription

---

## How to read the tables

| Notation                    | Means                                                                               |
| --------------------------- | ----------------------------------------------------------------------------------- |
| `AUTH-01`, `DV-14`, `LC-12` | a Playwright e2e ID from `frontend/e2e/`; run with `yarn test:e2e` from `frontend/` |
| `*.test.ts` / `*.test.tsx`  | a jest suite under `frontend/src/`, run by `make test_frontend`                     |
| `@slow`                     | excluded unless `RUN_SLOW=1`; these are real workflow executions (5-10 min each)    |
| manual                      | no automated counterpart; follow the sheet's own Action / Expected columns          |

Setup, credentials, and troubleshooting for the Playwright suite live in
`frontend/e2e/README.md`.

The CSV sheets carry `Tests: e2e`, `Tests: unit` and `Coverage` columns, and they
are the source of truth for the counts below: `FULL` and `PARTIAL` are automated
here, `MANUAL` is not. An e2e citation ending in `@slow` is gated behind
`RUN_SLOW=1`, so a default run does not check that row off, whatever its
`Coverage` label says. Re-derive from the sheets rather
than adjusting a total by hand: on 2026-08-06 this table was found 10 rows behind
them.

---

## Coverage by sheet

| Sheet                 | Rows    | Automated | Manual |
| --------------------- | ------- | --------- | ------ |
| 01 Login & Auth       | 8       | 8         | 0      |
| 02 Workspace          | 6       | 6         | 0      |
| 03 Workflow Execution | 16      | 16        | 0      |
| 04 Visualize          | 7       | 7         | 0      |
| 05 Record Management  | 9       | 9         | 0      |
| 06 Premium Features   | 15      | 9         | 6      |
| 07 Dataview           | 21      | 21        | 0      |
| 08 Subscription       | 11      | 8         | 3      |
| 11 AWS Monitoring     | 11      | 1         | 10     |
| **Total**             | **104** | **85**    | **19** |

43 rows across all 19 sheets cite an `@slow` e2e test, 23 of them here: sheet 03's
BT-304..306 (the tutorial runs, `WF-04`..`WF-06`), BT-509 (`REC-07`, the NWB
download), BT-403 (`VIS-02`, the ROI overlay), BT-406 (`DV-12`) and the whole of
sheet 07 except BT-707, BT-708, BT-718 and BT-719. All of them need `RUN_SLOW=1`,
which only the weekly `e2e.yml` sets; dispatch it against a branch with `gh workflow run e2e.yml --ref <branch>`.
The common cause is one real workflow run: the sample data ships input files and
workflow YAML but no computed node outputs, and only success records reach the
dataview.

Sheets **09 Subscription Registration** and **10 Storage** are mapped below but
were not part of the exported CSV set, so they carry no row counts here.

---

## Suite groups: what each spec file automates

| Group (spec file)                            | IDs         | Automated                                                                                                                                                                                                                                                                                                                                                                                           | Stays manual                                                                                                                                                                           |
| -------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auth (`01-auth`)                             | AUTH-01..16 | login, logout, session persistence, unverified-email flow, header navigation, registration validation                                                                                                                                                                                                                                                                                               | —                                                                                                                                                                                      |
| Workspace (`02-workspace`)                   | WS-01..07   | create (with the new workspace's 0 Bytes), list columns and the listed row's own id and name, navigation to the id it clicked, storage reload with the refreshed count from the response, one refresh per session, delete                                                                                                                                                                             | —                                                                                                                                                                                      |
| Workflow (`03-workflow`)                     | WF-01..09   | sample data import, reproduce, tutorial runs (`@slow`), both run-validation messages (WF-07 uploads a real fixture so the algorithm-nodes branch is reachable), tab navigation judged on each panel's own content                                                                                                                                                                                     | the run-POST debounce end to end (its length is pinned by `RunButtons.test.tsx`)                                                                                                        |
| Record (`04-record`)                         | REC-01..09  | list, expand parameters, copy, delete (single and multi-select), workflow/snakemake/NWB downloads                                                                                                                                                                                                                                                                                                   | —                                                                                                                                                                                      |
| File handling (`05-file-handling`)           | FILE-01..04 | file tree dialog, wildcard filter, check-all, sidebar toggle                                                                                                                                                                                                                                                                                                                                        | —                                                                                                                                                                                      |
| Uploads & node dialogs (`10-uploads`)        | UPL-01..07 | CSV param dialog, HDF5 and MAT structure dialogs, image/HDF5/MAT upload appears in inputs, selecting a data path inside a MAT file | S3-side verification |
| Dataview (`06-dataview`)                     | DV-01..18 | table display, ID/name column-menu filters, sort, pagination, inputs/outputs/details dialogs, public access, public/private API auth, image/ROI thumbnails asserted per cell by alt text, publish/unpublish + public listing, bulk publish/unpublish with confirmation, the public workspace filter (DV-17), concurrent public reads returning identical payloads (DV-18)                                                                                                                                                            | DB/S3 sync verification (the sync-status UI is covered by `SyncStatusView.test.tsx`) |
| Subscription (`07-subscription`)             | SUB-01..15 | free and premium plan UI state, per-card feature lists against a mocked catalogue (SUB-15), `/thanks` access guard, invoice page sections and two differing invoice rows, cancel and reactivate, the checkout and billing-portal hand-offs | Stripe-hosted checkout and portal pages, DB/Stripe dashboard verification |
| Storage (`08-storage`)                       | STO-01..04 | no-warning login under quota (asserted on the limit-warning response, not just an absent modal), the over-quota modal from a fulfilled alert (STO-04), dedicated and shared premium assignment on login | S3 verification, over-quota states (see the lifecycle group), auto-refresh tracing |
| Visualize (`09-visualize`)                   | VIS-01..05  | sidebar workspace/workflow info, Cell-ROI image plot, frame playback, second plot type, ROI editor open/cancel                                                                                                                                                                                                                                                                                      | Edit ROI commit (OK mutates ROI data and starts a processing run)                                                                                                                      |
| Lifecycle (`11-lifecycle`, local stack only) | LC-01..23 | free baseline, upgrade, over-quota warning modal (110%), usage-high indicator (95%), storage reload reset, expired-premium grace warning, overdue acknowledgment modal, downgraded-free over-quota warning, run blocked/warned at quota, expiration captions, cancel-subscription dialog, cancelled banner, inactivity warning + Stay Active (fake clock), 2h auto-release beacon, account deletion | real Stripe upgrade/downgrade, real S3 usage, reactivation/cancel API calls (the spec drives plan/expiry/usage in the docker DB and mocks premium assignment for the inactivity tests) |
| Admin (`12-admin`, local stack only)         | ADMIN-01..12 | admin login reaching the Account Manager, the eight list columns, the menu entry's presence for an admin and absence for an operator, the non-admin redirect, Edit / Add / Delete modals opening on the right values, every Cancel path asserted as no write request, the own-row Delete and Proxy SignIn suppression, the Proxy SignIn and Edit Subscription confirmations, the dashboard's own admin tile, the list's sort and rows-per-page controls, and a confirmed deletion of a throwaway account through the grid | completing a proxy sign-in (it would leave the worker signed in as another user), the DataGrid filter panel, creating a user for real (the create path is `test_users_admin.py`), and the per-step writes a deletion makes (`test_user_deletion.py`) |

Not automated at all (out of browser-test scope): premium instance
provisioning, AWS monitoring.

---

## Row-by-row map

Maps every row of the "Araya-OptiNiSt Release Test Cases Template"
(renumbered 2026-07-10) to its automated test. Subjects are included so rows
stay findable if the sheet is renumbered again.

AUTH-09/10/11 (registration empty fields / password mismatch / password
complexity) have no release-sheet row - they cover the System-sheet
registration validation cases.

---

## 01 Login & Auth

| Sheet row | Subject                      | Test    |
| --------- | ---------------------------- | ------- |
| BT-101    | Successful login             | AUTH-01 |
| BT-102    | Invalid credentials          | AUTH-02 |
| BT-103    | Empty fields validation      | AUTH-03 |
| BT-104    | Unverified email login       | AUTH-04 |
| BT-105    | Successful logout            | AUTH-05 |
| BT-106    | Session persistence          | AUTH-06 |
| BT-107    | Logo navigation              | AUTH-07 |
| BT-108    | Dashboard button (logged in) | AUTH-08 |

---

## 02 Workspace

| Sheet row | Subject                | Test  |
| --------- | ---------------------- | ----- |
| BT-201    | Create new workspace   | WS-01 |
| BT-202    | Workspace list display | WS-02 |
| BT-203    | Access workspace       | WS-03 |
| BT-204    | Storage refresh        | WS-04 |
| BT-205    | Dataview access        | WS-05 |
| BT-206    | Delete workspace       | WS-06 |

---

## 03 Workflow Execution

| Sheet row | Subject                        | Test                                                                    |
| --------- | ------------------------------ | ----------------------------------------------------------------------- |
| BT-301    | Access workflow page           | WF-01                                                                   |
| BT-302    | Import sample data             | WF-02                                                                   |
| BT-303    | Reproduce workflow from record | WF-03                                                                   |
| BT-304    | Run Tutorial 1 workflow        | WF-04 `@slow` (by-uid RUN)                                              |
| BT-305    | Run Tutorial 2 workflow        | WF-05 `@slow` (RUN ALL, full compute)                                   |
| BT-306    | Run Tutorial 3 workflow        | WF-06 `@slow` (RUN ALL, full compute)                                   |
| BT-307    | Run without algorithm nodes    | WF-07 (uploads a fixture into the default image node first, so this branch is reachable, then asserts the message verbatim with the other absent); `RunButtons.test.tsx` ("Pre-run validation messages") |
| BT-308    | Run without input file         | WF-07; WF-08; `RunButtons.test.tsx`                                     |
| BT-309    | Run button cooldown            | `RunButtons.test.tsx` ("Run request cooldown": repeated clicks send one request, and `RUN_REQUEST_DEBOUNCE_MS` is pinned to 3000); WF-08 covers the snackbar's own dedupe |
| BT-310    | Tab navigation                 | WF-09                                                                   |
| BT-311    | File tree display              | FILE-01                                                                 |
| BT-312    | File filter with wildcards     | FILE-02                                                                 |
| BT-313    | Check all / uncheck all        | FILE-03                                                                 |
| BT-314    | Sidebar toggle                 | FILE-04                                                                 |
| BT-315    | HDF5 file dialog               | UPL-02                                                                  |
| BT-316    | CSV parameter dialog           | UPL-01                                                                  |

Note: both validations assign the same variable and the input-file one is
assigned last, so on a fresh workspace it always wins. WF-07 therefore uploads
`sample_data/dev_mouse2p_short_image.tiff` into the default image node first,
which clears that branch and makes the algorithm-nodes message the one under
test. It used to accept either message with a regex, which BT-307 and BT-308
could both satisfy without the algorithm-nodes copy existing at all.

---

## 04 Visualize

| Sheet row | Subject                          | Test                                                                           |
| --------- | -------------------------------- | ------------------------------------------------------------------------------ |
| BT-401    | Open Visualize tab               | VIS-01                                                                         |
| BT-402    | Confirm workflow info in sidebar | VIS-01                                                                         |
| BT-403    | Add Cell ROI plot                | VIS-02                                                                         |
| BT-404    | Play visualization image         | VIS-03                                                                         |
| BT-405    | Add additional plot type         | VIS-04                                                                         |
| BT-406    | Image thumbnail display          | DV-12                                                                          |
| BT-407    | Run Edit ROI                     | VIS-05 (editor open + Cancel; the OK commit mutates ROI data and stays manual) |

Note (the data-backed tests need a real run, so they are `@slow`):
`sample_data/tutorial` ships the input files plus workflow metadata
(`experiment/workflow/snakemake.yaml`) only, with no computed node outputs, and
the dataview lists success records only. So VIS-03/04 need no run, because they
plot the shipped `sample_mouse2p_image.tiff`, but anything reading a node output
does: VIS-02 and VIS-05 select `cell_roi` (a `suite2p_roi` output), and the
dataview needs a success record plus thumbnails, which only a completed run
writes. `ensureCompletedTutorialRun` mints that state once per run by rerunning
the imported Tutorial1 by uid, which is why `06-dataview`'s Private Dataview
group and `REC-07` are tagged `@slow`. Two gotchas the helper absorbs: loading a
finished experiment fires a phantom "Workflow finished" snackbar (it anchors on
the run POST instead), and the success record is written shortly AFTER the
finished signal (DV-12 reload-polls the grid).

---

## 05 Record Management

| Sheet row | Subject                 | Test   |
| --------- | ----------------------- | ------ |
| BT-501    | Access record page      | REC-01 |
| BT-502    | View workflow details   | REC-02 |
| BT-503    | Copy single record      | REC-03 |
| BT-504    | Copy multiple records   | REC-08 |
| BT-505    | Delete single record    | REC-04 |
| BT-506    | Delete multiple records | REC-09 |
| BT-507    | Download workflow file  | REC-05 |
| BT-508    | Download Snakemake file | REC-06 |
| BT-509    | Download NWB file       | REC-07 |

Note (BT-509 costs a real run, so it is `@slow`): an NWB file exists only after
a completed workflow, and global setup deletes the `e2e-*` workspaces at the
start of every run, so REC-07 calls `ensureCompletedTutorialRun` itself rather
than skipping on the resulting 404 as it did before 2026-08-06. That is a real
snakemake execution, so the row's citation is `@slow` and checked off by
`RUN_SLOW=1` runs only.

---

## 06 Premium Features

| Sheet row                                   | Subject                                               | Test                                                                                                                                                                                                                                                           |
| ------------------------------------------- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| BT-604                                      | Premium profile display                               | SUB-05                                                                                                                                                                                                                                                         |
| BT-605                                      | Premium subscription page                             | SUB-04 (the status line names Premium and only a premium user is offered a Downgrade); LC-12 (the downgrade dialog that page opens)                                                                                                                              |
| BT-611                                      | Inactivity warning                                    | LC-14 (frontend lifecycle via fake clock; backend heartbeat/CloudWatch stays manual)                                                                                                                                                                           |
| BT-613                                      | Auto-release after 2h inactivity                      | LC-15 (frontend half) + `TestCheckPremiumUserInactivity` / `TestCleanupStaleAssignments` (L1 teardown); real AWS stays manual                                                                                                                                  |
| BT-615                                      | Instance release on browser close                     | `PremiumLifecycleIntegration.test.tsx` (beforeunload beacons release) + LC-15 (contributory only: it fires the same beacon endpoint on a 2h inactivity clock with the endpoint mocked, so it is not the tab-close gesture and does not check the row off) + `TestSoftReleaseUserAssignment` (row kept, ALB kept, no scale-down) + `TestFinalizeExpiredPendingReleases` (the 120s finalize deletes the row); real AWS teardown and the two `[premium-trace]` log lines stay manual |
| BT-612                                      | Stay Active button                                    | LC-14 (dismiss + timer reset; DB heartbeat verification stays manual)                                                                                                                                                                                          |
| BT-601/602                                  | assignment snackbars                                  | `PremiumNotificationManager.test.tsx` (BT-601 waiting copy + BT-602 success copy) + STO-02 (success snackbar, mocked assignment); the real AWS-backed flow stays a manual deployed-env check                                                                   |
| Lifecycle chain (assign, release, reassign) | end-to-end premium routing lifecycle                  | LC-17 (fake-clock companion to the `PremiumLifecycleIntegration` jest L2 test; real AWS state stays manual)                                                                                                                                                    |
| BT-614                                      | Instance release on logout                            | `useLogout.test.ts` (logout completes + premium releases via beacon even if the API fails); LC-17; CloudWatch release log stays manual                                                                                                                          |
| BT-603, 606..610                            | instance assignment, concurrency, release (AWS state) | manual                                                                                                                                                                                                                                                         |

Note: the `BT-6xx` rows above are the release
sheet, a separate scheme from the System test sheet. The System sheet's
`600-x` cases correspond to the CSV `62xx` cases (`600-4` <-> `6204`
concurrency); `BT-6xx` does NOT line up by trailing digits (`BT-604` is
"Premium profile display", not the `6204` concurrency race). Those `600-x` /
`62xx` cases are mapped, with their L1/L2/contract/L3 levels, in
[`infrastructure/documentation/SYSTEM_TEST_COVERAGE.md`](../../infrastructure/documentation/SYSTEM_TEST_COVERAGE.md).

---

## 07 Dataview

| Sheet row | Subject                                | Test                                                                                                                                              |
| --------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| BT-701    | Private Dataview Table Display         | DV-01                                                                                                                                             |
| BT-702    | Publish Toggle Display                 | DV-02                                                                                                                                             |
| BT-703    | Publish Experiment                     | DV-14 (toggle + public listing; DB/S3 sync verification manual)                                                                                   |
| BT-704    | Unpublish Experiment                   | DV-14                                                                                                                                             |
| BT-705    | Bulk Publish                           | DV-15                                                                                                                                             |
| BT-706    | Bulk Unpublish                         | DV-15                                                                                                                                             |
| BT-707    | Public Dataview Table Display          | DV-09                                                                                                                                             |
| BT-708    | Public Dataview Unauthenticated Access | DV-10                                                                                                                                             |
| BT-709    | UID Filter                             | DV-03 (column-menu filter)                                                                                                                        |
| BT-710    | Name Filter                            | DV-13 (column-menu filter)                                                                                                                        |
| BT-711    | Workspace Filter (Public Only)         | DV-17 (filters `/public` by workspace, asserts every listed row carries it, then empties the table with a workspace that cannot match)              |
| BT-712    | Sort by Column Header                  | DV-04                                                                                                                                             |
| BT-713    | Change Page Size                       | DV-05                                                                                                                                             |
| BT-714    | Inputs Dialog Display                  | DV-06                                                                                                                                             |
| BT-715    | Outputs Dialog Display                 | DV-07                                                                                                                                             |
| BT-716    | Workflow Details Dialog Display        | DV-08                                                                                                                                             |
| BT-717    | Close Dialog                           | DV-08                                                                                                                                             |
| BT-718    | Pending Sync Status Display            | `SyncStatusView.test.tsx` (the 202 / 423 / 503 / default / network branches and the retry ceiling; the S3 sync itself stays manual)                |
| BT-719    | Manual Retry from Sync Error           | `SyncStatusView.test.tsx` (Retry re-fires the fetch); the S3 re-sync itself stays manual. The plot-wrapper suites were cited here until 2026-08-06 in error: their sync overlay cannot render for those states and is covered nowhere |
| BT-720    | Image Thumbnail Display                | DV-12                                                                                                                                             |
| BT-721    | ROI Thumbnail Display                  | DV-12                                                                                                                                             |

Note (dataview data preconditions): the records the data-dependent tests
need are minted once per run before any of them execute — a fast no-op rerun
of the imported Tutorial1 plus a record copy of it (~2 min; only Tutorial1's
rerun is a reliable no-op, Tutorial2's recomputes CaImAn locally and fails).
Publishing requires a cloud bucket on the account, so on a local stack the
suite sets a placeholder `remote_bucket_name` attribute on the test user
(deployed users have real buckets; the S3 sync itself stays manual).

---

## 08 Subscription

| Sheet row   | Subject                            | Test                                                                                  |
| ----------- | ---------------------------------- | ------------------------------------------------------------------------------------- |
| BT-801      | Free Plan card display             | SUB-01                                                                                |
| BT-802      | Free account status display        | SUB-02                                                                                |
| BT-803      | No invoice for Free user           | SUB-03                                                                                |
| BT-804      | Premium plan status display        | SUB-04                                                                                |
| BT-805      | Premium account status display     | SUB-05                                                                                |
| BT-806      | Expiration date text               | LC-11 (exact caption per state; sheet says "renews on" but the UI text is "Renew on") |
| BT-807      | Verify Premium in the DB           | `test_subscription_state_transitions.py::TestSuccessfulCheckoutWritesPremium` (partial - the row a successful checkout writes, incl. the expiration coming from Stripe; the row as phrased targets the deployed RDS) |
| BT-810      | Stripe ID registered in the DB     | `test_checkout_session_tax_config.py::TestSeededPlanValuesMatchTheConfig::test_no_seeded_plan_is_missing_a_stripe_id` (partial - every seeded plan carries a product and price id; the docker DB, not the deployed one) |
| BT-808, BT-809, BT-811 | Stripe dashboard verification | manual                                                                     |

---

## 09 Subscription Registration

| Sheet row                                 | Subject                                                              | Test                                                                         |
| ----------------------------------------- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| BT-906                                    | Prevent direct access to /thanks                                     | SUB-06                                                                       |
| BT-907                                    | Subscription page updated to Premium                                 | SUB-04 (standing premium account)                                            |
| BT-908                                    | Account Profile updated to Premium                                   | SUB-05 (standing premium account)                                            |
| BT-917                                    | Initiate downgrade                                                   | LC-12 (confirmation modal + 30-day retention notice)                         |
| BT-918                                    | Cancel downgrade (click No)                                          | LC-12                                                                        |
| BT-920                                    | Reactivation option                                                  | LC-13 (banner + Continue Plan visible; clicking it is Stripe-backed, manual) |
| BT-922                                    | Expired premium user buttons                                         | LC-06 (Upgrade + Manage both visible)                                        |
| BT-925                                    | Delete test user account                                             | LC-16 (per-run throwaway account; active=0 + deletion records completed)     |
| BT-901..905, 909..916, 919, 921, 923, 924 | checkout flow, DB/Stripe verification, confirmed cancel/reactivation | manual                                                                       |

---

## 10 Storage

| Sheet row                 | Subject                                 | Test                                                                                       |
| ------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------ |
| BT-1001                   | Free user login - no warning            | STO-01                                                                                     |
| BT-1005                   | Upload image data                       | UPL-03 (UI half — file appears in inputs; S3 verification manual)                          |
| BT-1006                   | Upload HDF5 file                        | UPL-04 (UI half — file appears in inputs; S3 verification manual)                          |
| BT-1007                   | Premium user login                      | STO-02                                                                                     |
| BT-1010                   | Storage limit exceeded warning on login | LC-03 (premium) / LC-08 (free)                                                             |
| BT-1011                   | Handle Later button                     | LC-03 (dismisses, stays on dashboard)                                                      |
| BT-1012                   | Manage Files button                     | LC-08 (redirects to /workspaces)                                                           |
| BT-1013                   | Cannot run workflow when over limit     | LC-09                                                                                      |
| BT-1014                   | Storage 90-99% warning on RUN           | LC-10 (snackbar + run not blocked)                                                         |
| BT-1015                   | Manual storage refresh                  | WS-04                                                                                      |
| BT-1016                   | Storage values update after delete      | LC-05 (delete ballast → Reload clears warning)                                             |
| BT-1002..1004, 1008, 1009 | S3-side verification                    | manual (the LC rows drive real files locally, but the S3 bucket half needs a deployed env) |

---

## 11 AWS Monitoring

| Sheet row           | Subject                             | Test                                                |
| ------------------- | ----------------------------------- | --------------------------------------------------- |
| BT-1110             | Public Dataview access + auth guard | DV-11 (API half; point BASE_URL/API_URL at the env) |
| BT-1101..1109, 1111 | AWS CLI / console probes            | manual                                              |

# System Test Automation: Preliminary Design Review

**Status:** proposal, not yet scheduled
**Baseline:** `SYSTEM_TEST_COVERAGE.md` as of 2026-08-03, after the red-team audit (`TEST_COVERAGE_AUDIT.md`)
**Scope:** the `Araya-Optinist System Test Cases Template` rows currently marked manual

## Executive Summary

- **236 of 409 System sheet rows have no automated test.** This PDR triages them and proposes which to automate
- **123 rows are automatable** with the test infrastructure that already exists (Playwright, jest, pytest). No new frameworks, services, or CI lanes are required
- **103 rows should stay manual permanently** - they assert Stripe-hosted UI, Stripe Dashboard state, or live AWS behavior that we cannot own from a test. A further 10 are automatable but deliberately deferred as low value
- **The single largest win is the admin Account Manager (sheet 03): 37 rows, entirely our own UI and API, currently zero coverage**
- **Highest risk-reduction per hour is not the biggest package** - the free-user cleanup grace window (1 row) guards irreversible data deletion and is a half-day of work
- **Recommended split is four PRs**, sequenced so the cheap high-value checks land before the large admin package

---

## Problem

The System sheets are the release gate for anything not covered by the Release
sheets. Today a release run hand-verifies 236 rows. That has three costs:

1. **Regression risk between releases.** The admin Account Manager, the storage
   warning thresholds, and the Stripe checkout session configuration have no
   automated guard, so a regression in any of them surfaces only during a manual
   release pass, or in production.
2. **Release-pass cost.** Sheets 02, 03 and 09 alone are 145 manual rows.
3. **Sheet rot.** Rows that are never executed by code drift from the product.
   Four factual errors in the sheets were found just by doing this triage
   (see [Sheet corrections](#sheet-corrections-found-during-triage)).
4. **Skipped tests read as coverage.** Two rows were credited to tests that never
   execute in CI (see WP13). A skip is indistinguishable from a pass in a summary
   line, so this class of error hides itself.
5. **Tests that could not fail were counted as coverage.** A red-team audit of all
   291 mapped rows (`TEST_COVERAGE_AUDIT.md`) found 41 materially wrong and moved
   34 System rows to `No`. Those 34 are folded in below as WP14-WP21. Two were
   _false-positive_ tests that pass while the product is broken - worse than no
   coverage, because the manual check is suppressed too.

## Goals

1. Automate the rows where the assertion is about **our** code, using the suites
   that already run on every PR.
2. Leave a defensible, written reason for every row that stays manual, so the
   next reviewer does not re-litigate the same triage.
3. Keep `SYSTEM_TEST_COVERAGE.md` truthful: every work package updates its rows
   in the same PR that adds the tests.

## Non-goals

- **Automating Stripe-hosted checkout.** The card form, Link flows, and OTP
  screens are Stripe's UI. Driving them is brittle, tests Stripe rather than us,
  and breaks whenever Stripe redesigns. 25 rows.
- **Automating Stripe Dashboard verification.** These rows ask a human to read
  live Stripe state. The automatable half is asserting what _we send_ to Stripe,
  which is covered by WP7.
- **Automating live AWS behavior** (ALB target-group routing, ASG replacement,
  EFS persistence across task replacement, CloudWatch alarm transitions). These
  need a deployed environment and are already the documented job of the manual
  sheets.
- **Raising the coverage percentage as an end in itself.** A test that asserts a
  log string or a CSS colour it also hard-codes is worse than the manual row.

---

## Triage method

Each of the 236 manual rows was classified by asking one question: **can this
assertion fail because of a change in this repository?**

| Verdict             | Meaning                                                                                                                                                      | Rows |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---- |
| Automatable         | The behavior is in our frontend, backend, or terraform, and a deterministic test can observe it                                                              | 123  |
| Permanently manual  | The assertion is about Stripe-hosted UI, live Stripe state, or deployed AWS behavior                                                                         | 103  |
| Deferred, low value | Automatable but the test would assert something that is not a contract (log wording, Stripe-generated PDFs) or cannot be mechanised ("readable at any size") | 10   |

---

## Summary of proposed work

| WP   | Package                                          | Sheet          | Rows    | Layer               | Effort |
| ---- | ------------------------------------------------ | -------------- | ------- | ------------------- | ------ |
| WP1  | Admin Account Manager                            | 03             | 37      | pytest + e2e + jest | L      |
| WP8  | Subscription state transitions in our UI and API | 02             | 16      | e2e + pytest        | L      |
| WP4  | Registration and public-header validation        | 01             | 8       | e2e                 | S      |
| WP2  | Storage warning UI details                       | 04             | 7       | jest + e2e          | S      |
| WP7  | Stripe session configuration and tax             | 09             | 7       | pytest              | S      |
| WP9  | Public instance configuration from terraform     | 08             | 5       | pytest              | S      |
| WP3  | MAT file support                                 | 04, 05         | 3       | e2e                 | S      |
| WP6  | Registration DB state                            | 01             | 2       | pytest              | S      |
| WP10 | Public and health endpoint assertions            | 08             | 2       | pytest              | XS     |
| WP5  | Free-user cleanup grace window                   | 01             | 1       | pytest              | XS     |
| WP11 | Dataview workspace filter                        | 07             | 1       | e2e                 | XS     |
| WP12 | File-sync progress indicator                     | 05             | 1       | jest                | XS     |
| WP13 | Revive the skipped suites                        | 02, 09         | 2       | pytest              | S      |
| WP16 | Premium assignment and restore gaps (**landed**) | 06, 06-2       | 6       | pytest + jest       | L      |
| WP21 | Audit fallout: assorted single rows              | 01, 02, 03, 05 | 6       | pytest + jest + e2e | M      |
| WP17 | Workflow-count tracking, done properly           | 05             | 5       | pytest              | M      |
| WP20 | Storage-warning scenario gaps                    | 04             | 4       | e2e                 | S      |
| WP18 | Dataview publish and sync DB-state assertions    | 07             | 3       | pytest              | S      |
| WP19 | On-demand input sync, per format                 | 08             | 3       | pytest              | S      |
| WP14 | `SyncStatusView` + `useSyncRetry` suite          | 07             | 2       | jest                | S      |
| WP15 | `InactivityWarning` suite (**landed**)           | 06-2           | 2       | jest                | S      |
|      | **Total**                                        |                | **123** |                     |        |

Effort key: XS = under half a day, S = 1-2 days, L = 4+ days.

---

## WP1: Admin Account Manager

**Sheet 03 rows:** 301..335, 337, 341 (37 rows)

The whole admin surface - access gating, user list, create, edit, delete,
change-password, inline name edit - has no automated coverage. It is our own
React page against our own FastAPI routers, so all of it is reachable.

Split by the cheapest layer that can prove each row:

| Layer                       | Rows                                                                                     | What it asserts                                                                                                                                                                                                                                                                             |
| --------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| pytest, router level        | 303, 304, 305, 306, 307, 308, 309, 310, 311, 312, 315, 316, 317, 321, 322, 323, 337, 341 | admin-only gating, non-admin 403, list shape and pagination, create with role, duplicate/invalid email and weak password rejection, name/role/email update persistence, subscription and storage columns, demoted admin loses access, self-delete blocked, re-registering a deleted address |
| e2e, new `12-admin.spec.ts` | 301, 302, 313, 314, 320, 324, 333, 334, 335                                              | admin login reaches Account Manager, menu visibility, modals open, Cancel closes without mutating, delete confirmation appears and Cancel aborts                                                                                                                                            |
| jest, component level       | 318, 319, 325, 326, 327, 328, 329, 330, 331, 332                                         | edit-modal field validation, change-password modal states (empty, wrong current, mismatch, success), inline name edit save/cancel/empty                                                                                                                                                     |

**New test IDs:** `ADMIN-01`..`ADMIN-09`.

**Acceptance criteria**

1. A non-admin receives 403 from every Account Manager router, asserted per
   route rather than once.
2. Creating a user with a duplicate email, an invalid email, or a weak password
   fails with the message the sheet names, and creates no partial row.
3. `test_users_admin_subscription.py` already covers the admin _subscription_
   update path. WP1 must not duplicate it - extend that file rather than
   creating a parallel one.
4. Self-delete is rejected server-side, not only hidden in the UI. If the API
   currently allows it, that is a bug to file, and the test pins the fix.

**Risk:** the change-password path calls Firebase. Mock the Admin SDK at the
boundary the existing suites already mock (`studio/tests/app/conftest.py`
patterns), do not reach the network.

---

## WP8: Subscription state transitions in our UI and API

**Sheet 02 rows:** 214, 227, 237, 243, 244, 245, 246, 251, 260, 264, 266, 270, 271, 286, 290, 291 (16 rows)

Sheet 02 is 69 manual rows, but 53 of them are Stripe's UI or dashboard. These
16 are ours.

| Approach                                                 | Rows                         | Notes                                                                                                                                                                                                  |
| -------------------------------------------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| e2e driven from DB state, as `11-lifecycle` already does | 264, 271, 286, 290           | profile after cancellation, UI after reactivation, UI after renewal, fallback to Free. The lifecycle spec already rewrites plan/expiry in the docker DB, so these are new cases in an existing harness |
| e2e with a route mock, as `STO-02` already does          | 243, 244, 245, 246, 260, 270 | invoice page sections and row formatting, confirm-cancellation, execute-reactivation. Mock our own `/subscriptions/*` responses; do not call Stripe                                                    |
| e2e asserting the outbound hop only                      | 214, 251                     | clicking Upgrade creates a checkout session and navigates to a `checkout.stripe.com` URL; the billing-portal button creates a portal session. Assert our request and the redirect target, then stop    |
| pytest                                                   | 227, 237, 266, 291           | no plan change after a declined payment, plan_id and expiration set on successful checkout, cancel touches only `scheduled_downgrade` and `updated_at`, fallback-to-Free state                         |

**New test IDs:** `SUB-07`..`SUB-12`, `LC-20`..`LC-23`.

**Acceptance criteria**

1. Row 266 asserts field-level integrity: `stripe_customer_id`, `plan_id` and
   `expiration` are byte-identical before and after a cancel, and only
   `scheduled_downgrade` and `updated_at` differ.
2. No test in this package makes a network call to Stripe. A test that needs a
   Stripe response uses a fixture, and the fixture is shared with the existing
   `test_webhook.py` fixtures rather than re-invented.
3. The invoice-page tests assert the sections the sheet names, not a snapshot.

**Risk:** route-mocked e2e tests can pass against a broken backend. Each mocked
row is paired with a pytest that covers the same transition server-side, or it
is labelled `(partial)` in the coverage map. This is the same honesty rule the
premium tables already follow.

---

## WP4: Registration and public-header validation

**Sheet 01 rows:** 100, 101, 104, 106, 108, 110, 111, 113 (8 rows)

Straightforward additions to `01-auth.spec.ts`. `AUTH-04` already registers a
throwaway account and reaches both the success screen and the unverified-login
alert, so rows 110, 111 and 113 are extra assertions on a page the suite already
has open.

**New test IDs:** `AUTH-12`..`AUTH-19`.

**Acceptance criteria**

1. Row 106 asserts the forbidden-character set the product actually enforces.
   The sheet names allowed characters `!#$%&()*+,-./@_|`; the test reads the
   validator's own constant so the two cannot drift.
2. Rows 110 and 113 assert the snackbar and the button's loading state without
   waiting on real email delivery.

**Risk:** row 110/113 trigger real Firebase verification sends. `AUTH-04`
already accepts that cost once per run; these rows must reuse that account
rather than registering more, or the weekly CI run accumulates Firebase users.

---

## WP2: Storage warning UI details

**Sheet 04 rows:** 428, 432, 438, 440, 445, 446, 447 (7 rows)

| Layer | Rows     | What it asserts                                                                                                                                           |
| ----- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| jest  | 428, 438 | progress bar colour band per usage ratio: red above 100%, orange 90-100%, blue below 90%                                                                  |
| jest  | 445      | Reload is disabled and shows a spinner while the refresh is in flight                                                                                     |
| jest  | 446, 447 | storage refreshes on first login and is skipped on the second within the throttle window, driven by the `storage-refreshed-on-login` sessionStorage guard |
| e2e   | 432, 440 | Upgrade from the over-quota modal lands on `/subscription`; expired-premium-in-grace _and_ over quota shows the correct combined state                    |

**Acceptance criteria**

1. The colour tests assert against the threshold constants the component
   imports, not against literal hex values duplicated in the test.
2. Rows 446/447 assert the guard's effect (one `/refresh-storage` call across
   two logins), not the presence of the sessionStorage key.

**Value note:** 446/447 guard a real bug class. `UserSlice.test.ts` already
asserts logout clears that sessionStorage key, but nothing asserts the throttle
it exists to implement.

---

## WP7: Stripe session configuration and tax

**Sheet 09 rows:** 903, 906, 909, 910, 917, 918, 921 (7 rows)

Sheet 09 is 33 manual rows and, as recorded in `SYSTEM_TEST_COVERAGE.md`, there
is **no automated coverage of tax anywhere in the repository**. We cannot assert
Stripe's tax engine, but we can assert every input we hand it - which is where
our bugs would live.

| Row      | Assertion                                                                                                                       |
| -------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 909      | the checkout session is created with `automatic_tax: {enabled: true}`                                                           |
| 910      | the session is created with `billing_address_collection: 'required'`                                                            |
| 903      | plan name, price, billing cycle and currency agree between `SUBSCRIPTION_PLANS_CONFIG` and the seeded `subscription_plans` rows |
| 906      | the checkout path never calls a Stripe product- or price-mutating API                                                           |
| 917, 918 | the webhook writes the purchase record with the correct `user_id` and `plan_id`, and violates no constraint                     |
| 921      | the webhook handler reads `total_details` / `amount_tax` from the payload                                                       |

**Acceptance criteria**

1. Rows 909 and 910 fail if the keyword is removed from the session-creation
   call. Verify by deleting it locally and re-running, and record that in the
   PR's manual test cases.
2. Row 903 runs against the same config the deployment reads, so a tfvars change
   that desyncs price from Stripe fails a PR.

**Value note:** this is the highest-consequence package per line of test code.
Tax silently disabled is a compliance and revenue problem that no other test in
the repo would catch.

---

## WP9: Public instance configuration from terraform

**Sheet 08 rows:** 807, 820, 821, 825, 826 (5 rows)

`test_compute_config.py::TestEFSMountPaths` is the precedent: it asserts
terraform configuration by reading the `.tf` source. The same technique covers:

| Row | Assertion                                                                                                                                       |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 807 | every premium ALB rule priority falls in 100-199 and cannot collide with the public band; `MAX_PREMIUM_PRIORITY` is enforced, not just declared |
| 820 | the EFS lifecycle policy sets `transition_to_ia = AFTER_7_DAYS`                                                                                 |
| 821 | the cleanup Lambda's EventBridge schedule is the intended daily cron                                                                            |
| 825 | the public log group name and `retention_in_days = 30`                                                                                          |
| 826 | the public ASG `min_size` and `desired_capacity`                                                                                                |

**Acceptance criteria**

1. Tests assert against the terraform source, and are explicitly labelled as
   configuration assertions rather than behavioral ones. Terraform saying
   `AFTER_7_DAYS` is not proof AWS applied it - the deployed check stays manual.
2. Row 807 is a real routing-collision guard, so it asserts the invariant
   (`premium priorities and public priorities are disjoint`) rather than
   re-stating the constant.

---

## WP3: MAT file support

**Sheet 04 rows:** 409, 410. **Sheet 05 row:** 526 (3 rows)

MAT upload, MAT data selection, and the MAT structure dialog are the one input
format with no e2e coverage. HDF5 has `UPL-02` and `UPL-04`; this mirrors them.

**New test IDs:** `UPL-05`, `UPL-06`, `UPL-07`.

**Risk:** needs a small `.mat` fixture committed to the repo. Keep it under a
few hundred KB, and generate it with a script checked in beside it so it can be
regenerated rather than trusted.

---

## WP6, WP10, WP5, WP11, WP12: small packages

| WP   | Rows     | Work                                                                                                                                          |
| ---- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| WP6  | 116, 117 | pytest: after `/register`, the `users` row exists with `active = 1`, and the user starts Free (no `subscription_users` row, or `plan_id = 1`) |
| WP10 | 802, 803 | pytest: the SPA catch-all returns the shell rather than 404 for a client-side route; the health endpoint returns 200                          |
| WP5  | 124      | pytest: `DataCleanupJob._get_users_for_cleanup()` excludes a user inside the grace window and includes one past it                            |
| WP11 | 714      | e2e `DV-16`: the public dataview workspace filter narrows the table                                                                           |
| WP12 | 529      | jest: the file-tree `LinearProgress` shows while a sync is in flight and clears on completion                                                 |

**WP5 deserves attention despite being one row.** The existing
`TestGetUsersForCleanupInstanceFilter` asserts only that the query filters by
instance; nothing asserts the `logged_out_at` grace interval. That interval is
the only thing standing between a user who just logged out and irreversible
deletion of their workflow outputs. It is the cheapest high-consequence test in
this document.

---

## WP13: Revive the skipped suites

**Sheet 02 row:** 289. **Sheet 09 row:** 922 (2 rows, plus latent risk)

Both rows were credited to tests that exist but never run:

| Row    | Cited test                                          | Why it does not run                                                                                                                                                                                                      |
| ------ | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 02/289 | `test_crud_users_context.py` grace-period cases     | `@pytest.mark.skip(reason="Requires integration test with real DB")`. **12 of the 14 tests in that file are skipped**, covering every subscription-tier state and every storage-usage case                               |
| 09/922 | `test_checkout.py::test_webhook_requires_signature` | Depends on the `check_api_running` fixture, which calls `pytest.skip()` unless a live API answers at `{api_url}/docs`. The docker lane runs pytest with no server, which is why the routers lane reports exactly 3 skips |

The 12 skipped cases are the real prize. They assert the Premium / Limit Grace /
Expired boundaries that `get_user_with_context()` derives - the logic that decides
whether a user keeps paid access. That derivation is pure given its query result,
and the tests already build a `create_query_result(...)` fixture, so the stated
skip reason ("requires a real DB") looks avoidable.

**Approach**

1. Re-point the 12 cases at the pure derivation instead of a DB round trip and
   remove the skip markers. If one genuinely needs a database, split it out and
   leave that single case skipped with a narrower reason.
2. For 922, give `TestCheckoutIntegration` a `TestClient` instead of a live
   `requests` call. The assertion - an unsigned webhook is rejected - needs no
   server.

**Acceptance criteria**

1. `test_crud_users_context.py` reports zero skips, or each remaining skip names
   the specific missing fixture.
2. The routers lane's skip count drops from 3, with the remainder asserted to be
   the opt-in premium-lock lane only.
3. A guard rejects new unconditional `@pytest.mark.skip` without a linked issue.
   The premium-lock lane already has this pattern
   (`test_opt_in_env_is_consistent` fails if the env is half-configured) - reuse
   it rather than inventing a mechanism.

**Value note:** this package recovers coverage the map _thought_ it had. Twelve
dead tests across the subscription tier boundaries is a silent gap in exactly the
logic that gates paid access.

---

## Audit fallout: WP14 - WP21

These eight packages exist because the red-team audit moved 34 rows from
"covered" to `No`. Three of those are permanently manual (02/288 Stripe past-due
state, 06/603 the CloudWatch heartbeat line, 06/608 real cross-instance S3
recovery); the remaining 31 are the packages below. Per-row evidence is in
`TEST_COVERAGE_AUDIT.md`.

### WP14: `SyncStatusView` + `useSyncRetry` suite

**Rows:** 07/717, 07/718 (2)

`SyncStatusView.tsx` and `useSyncRetry.ts` implement the sync-status icon, error
alert and Retry button that four sheet rows describe, and **neither has any
test**. The rows had been credited to `ImagePlotSimple.test.tsx`, a different
component fed by a different redux slice.

One jest suite covering the 202 / 423 / 503 branches, the retry-count bump and the
timeout terminal state closes 717 and 718, upgrades 07/720 and 07/725 out of
unbounded `(partial)`, and closes release rows BT-718 and BT-719.

**Acceptance criteria:** the suite fails if `handleRetry` stops re-firing
`fetchFn`, and if the error branch stops rendering all three of icon, alert and
button.

### WP15: `InactivityWarning` suite (landed)

**Rows:** 06-2/6228, 06-2/6230 (2)

`InactivityWarning.tsx` had no test file, yet three rows asserted its behavior:
the Stay Active button, `recordActivity()`, `performLogout()`, and the "Session
Expired" copy. 6228 had been credited to `crossTabSync.test.ts` (the transport
primitive, not the subscriber) and 6230 to `axiosRefresh.test.ts` (a different
trigger entirely).

**Acceptance criteria (met):** `InactivityWarning.test.tsx` covers Stay Active
sending the heartbeat and then dismissing, the 401 flip to `Session Expired`
(action removed so it cannot be re-clicked) followed by `performLogout()` after
the read delay, and the non-401 branch still dismissing so a transient error
cannot pin an undismissable snackbar. The cross-tab dismiss lives in the provider
rather than the component, so it landed in `PremiumInactivityActivity.test.tsx`:
the mocked `onActivityFromOtherTab` subscriber is invoked directly, and the test
asserts the warning clears, no heartbeat of our own is sent, and the release
deadline moves with the borrowed timestamp. Closes 6228 and 6230, upgrades 6229.

### WP16: Premium assignment and restore gaps (landed)

**Rows:** 06/601, 06-2/6203, 06-2/6208, 06-2/6209, 06-2/6213, 06-2/6219 (6)

The largest audit cluster, and the one that matters most: sheet 06-2 is PR #780's
own acceptance evidence.

| Row         | What was missing                                                                                                                                                                                                                                             | What landed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 06/601      | No test asserted the "Please wait while your dedicated premium resource is being prepared" copy                                                                                                                                                              | Two cases in `PremiumNotificationManager.test.tsx`: the copy plus its `info` / `persist` options on `isAssigning` and on a shared-only assignment, and the `dedicated_ready` dismissal                                                                                                                                                                                                                                                                                                                                                                                                              |
| 6203 / 6208 | The restore itself. `TestHeartbeatRestoresPendingRelease` covers the middleware/heartbeat half only - the Lambda's `restore_pending_release` transaction was patched out of every test that touched it                                                       | `TestSoftReleaseUserAssignment` (the state both rows start from: row flipped to `pending_release`, ALB rule and target group kept, usage log closed, no scale-down while the instance is still allocated) and `TestRestorePendingReleaseTransaction`: the restore UPDATE never re-stamps `assigned_at` (the sheet's "same id, same `assigned_at`" check), no ALB resources are created, the pool marker skips the EC2 liveness probe, and a dead instance deletes instead of restores. FE half: a re-login case in `PremiumLifecycleIntegration.test.tsx` asserting adoption with no `/assign` call |
| 6209        | `finalize_expired_pending_releases` was mocked out everywhere                                                                                                                                                                                                | `TestFinalizeExpiredPendingReleases`: the grace-window predicate (`PENDING_RELEASE_GRACE_SECONDS`, `FOR UPDATE`), a status-scoped DELETE per expired row, and the usage-log close                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 6213        | The FE refresh path for an autoscaling-pool assignment                                                                                                                                                                                                       | A case in `PremiumPollingRoutingRestore.test.tsx`: adoption of the pool marker, the stale instance pin cleared (the marker has no verifiable hash), polling resumed, `/assign` never called                                                                                                                                                                                                                                                                                                                                                                                                         |
| 6219        | The assignment row _surviving_ expiry. Note the audit's "the sweep asserts the opposite" is about the DB-only trigger the row uses: since #629 P3 the webhook path does release, via `_release_premium_assignment` (the row's stale note has been corrected) | `TestExpiryLeavesTheAssignmentDangling` compiles the sweep's predicates and pins the survival window to `now - GRACE_PERIOD_DAYS`, so a just-expired subscription is out of scope and the row dangles                                                                                                                                                                                                                                                                                                                                                                                               |

**Acceptance criteria (met):** each row's central DB assertion (row identity
preserved, or row deleted and replaced) is asserted against a compiled statement
or the executed SQL and its parameter binding, not a `MagicMock` call count. The
restore assertion was mutation-checked: adding `assigned_at = NOW()` to the
restore UPDATE fails `test_restores_same_row_to_active`, and dropping the
instance-pin clear fails the 6213 case.

**Left out:** the monitor's step-10a wiring (the loop that hands each finalized
row to `_teardown_alb_resources`) - reaching it means reproducing
`handle_scheduled_monitoring`'s ~15-patch stub stack. The teardown it calls is
covered directly instead: `test_teardown_drops_the_per_user_tg_but_never_the_shared_one`
pins the per-user delete and the shared-ASG skip, which the premium-manager copy
of that helper had never been tested for.

### WP17: Workflow-count tracking, done properly

**Rows:** 05/538, 05/539, 05/540, 05/543, 05/544 (5)

Every existing `test_workflow_tracking.py` case asserts `session.execute.called`
and patches `_get_user_tier` out - which is precisely the free-versus-premium
decision rows 540-544 exist to cover. A bug writing to `FreeUserAssignment` for a
premium user passes today.

**Acceptance criteria**

1. Assert the compiled statement's target table, so the tier branch is observable.
2. Failure path: raise inside `snakemake_execute` and assert
   `decrement_workflow_count` still ran. That invariant lives in a `finally` block
   no test reaches.
3. Concurrency (539/544) needs a real DB. Put it in the existing opt-in lane
   beside `test_premium_lock_integration.py` rather than inventing a new one.

### WP18: Dataview publish and sync DB-state assertions

**Rows:** 07/721, 07/722, 07/724 (3)

All three claimed coverage from classes that assert `execute.called` against a
`MagicMock`, so the DB values the rows name are unobservable.

- 721: assert `_get_pending_experiments` selects only `pending` and `error` - the
  WHERE clause behind the design gap the row itself documents.
- 722: assert the `error -> synced` transition and the `ExperimentsSynced` metric.
- 724: a publish / unpublish / publish sequence asserting last-write-wins.

### WP19: On-demand input sync, per format

**Rows:** 08/817, 08/818, 08/822 (3)

Five rows had been pinned to `TestEnsureInputFileSynced`, whose two tests cover
only the already-cached fast path and an exception propagation, and neither varies
file format. 815 and 816 are now re-pointed at the real regression tests in
`test_structured_outputs.py`; CSV, TIFF and the post-cleanup re-fetch have no
coverage at all.

**Acceptance criteria:** one test per call site (the `outputs.py` CSV and TIFF
paths) asserting `ensure_input_file_synced` is awaited when the local file is
absent.

### WP20: Storage-warning scenario gaps

**Rows:** 04/413, 04/423, 04/429, 04/430 (4)

- 413 / 423: the shared-instance snackbar. The cited `STO-02` mocks
  `is_shared: false`, so the shared branch is unreachable. Needs a fixture with
  `is_shared: true`. **Also correct the sheet copy** - the string it names does not
  exist in the product; the nearest is "Falling back to shared resources."
- 429: expired premium in grace **and** over quota. `LC-08` sets `plan_id = 1`, so
  it lands in the free branch and never exercises the 200GB-to-5GB effective quota
  drop. This combined state has no test at all.
- 430: Handle later on the premium modal (`LC-08` clicks Manage Files instead).

### WP21: Audit fallout, assorted single rows

**Rows:** 01/126, 02/272, 02/299, 03/338, 05/516, 05/528 (6)

| Row    | Work                                                                                                                                                                                               |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01/126 | Test the real `studio/app/.../workflow_count_recovery.py` (zero test references today) and assert the `_get_users_for_cleanup` predicates. The cited test covers the Lambda implementation instead |
| 02/272 | Assert the reset via the upsert or `call_args`, not the event field the handler echoes back                                                                                                        |
| 02/299 | The Premium-branch deletion warning copy. `LC-16` registers a free user, so the premium lines are unreachable                                                                                      |
| 03/338 | A login attempt against a deactivated user asserting 401/403                                                                                                                                       |
| 05/516 | The 3-second run-POST debounce. `WF-08` covers the snackbar dedupe and says so in its own comment                                                                                                  |
| 05/528 | The file-tree sync progress indicator (jest). The backend half is a source-string grep suite with no regression value                                                                              |

---

## Remediation: repairs with no sheet rows of their own

These close no rows - they stop existing tests from lying. Highest priority in
this document.

| Item                                                                                                                   | Why                                                                                                                                                                                                                                                                                                                                                                                               |
| ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Fix `nwb.py` returning HTTP 200 with body `false`** when no `.nwb` exists, and make BT-509 assert a real NWB payload | A product bug. The test passes on a 5-byte `false` blob, so the automated _and_ manual checks are both suppressed                                                                                                                                                                                                                                                                                 |
| **Fix BT-403 (Cell-ROI)** to assert an ROI-specific artefact                                                           | It re-asserts a plot already awaited before the ROI was selected; a failed ROI fetch does not hide it                                                                                                                                                                                                                                                                                             |
| **Delete or rewrite `PremiumHeartbeatRetry.test.ts` and `PremiumSleepDetection.test.ts`** (**done**)                   | Both re-implemented the logic under test inside the test file and asserted against their own copies. `PremiumHeartbeatRetry.test.ts` was rewritten against the real provider (attempt count, growing backoff, terminal rethrow, free-tier no-op, device-wake wiring); `PremiumSleepDetection.test.ts` was deleted, its subject being covered by `useSleepDetection.test.ts` against the real hook |
| **Correct the sample-data precondition** in `helpers.ts` and `RELEASE_TEST_COVERAGE.md`                                | Both claim the import ships pre-computed outputs; it ships metadata YAML only, so snakemake recomputes. Also reconcile the 600s `beforeEach` sitting inside an 840s inner wait                                                                                                                                                                                                                    |
| **Add a skip gate** to the e2e run                                                                                     | For a sign-off sheet a skipped test proves nothing. Either fail the run when a High-priority mapped test skips, or emit a per-row skip summary the tester reconciles against the sheet                                                                                                                                                                                                            |
| **Require `(partial)` to name its uncovered half**                                                                     | Most of the 85 MEDIUM audit findings are rows where `(partial)` was doing unbounded work. The premium tables already model the honest style                                                                                                                                                                                                                                                       |

---

## Recommended sequencing

Eight PRs, smallest-risk-first so the branch does not become one unreviewable
diff. PR 0 comes first: it repairs tests that currently mislead.

| PR  | Contents                  | Rows | Rationale                                                                                                                                                              |
| --- | ------------------------- | ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | WP5, WP7, WP9, WP10, WP13 | 23   | All pytest, no new harness, highest consequence per line. Lands the data-deletion and tax guards, and stops two rows claiming coverage they do not have                |
| 2   | WP2, WP4, WP11, WP12, WP3 | 20   | Small jest and e2e additions to specs that already exist                                                                                                               |
| 3   | WP1                       | 37   | The admin package. Large enough to review on its own, and touches a surface with no existing tests to lean on                                                          |
| 4   | WP8                       | 16   | Depends on the route-mock and DB-driven patterns being settled; benefits from landing after PR 2                                                                       |
| 0   | Remediation table         | 0    | **Before any of the above.** Fixes two tests that pass on a broken product and deletes two that cannot fail. Cheap, and it stops the map lying while the rest is built |
| 5   | WP14, WP15, WP18, WP19    | 10   | Four small suites against components and call sites that have none today. Closes the audit's clearest gaps                                                             |
| 6   | WP16, WP17                | 11   | The premium restore cluster and workflow-count tracking. WP16 should land before PR #780 merges, since sheet 06-2 is that PR's acceptance evidence                     |
| 7   | WP20, WP21                | 10   | Scenario gaps and assorted single rows; several also need sheet-copy corrections                                                                                       |

Each PR updates its rows in `SYSTEM_TEST_COVERAGE.md`. A PR that adds tests
without updating the map is incomplete, because the map is what the release
tester reads.

---

## Out of scope, with reasons

| Rows                                                                | Count   | Why it stays manual                                                                                                                    |
| ------------------------------------------------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| 02: 215..226, 229, 230, 231, 275..284                               | 25      | Stripe-hosted checkout and Link UI. Testing it tests Stripe                                                                            |
| 09: 901, 902, 904, 905, 907, 908, 911..916, 919, 923..928, 930..936 | 26      | Stripe Dashboard and Stripe-rendered checkout UI state. Row 929 is excluded because `test_stripe_customer_lookup.py` already covers it |
| 02: 228, 238..242, 250, 252, 253, 267, 268, 269, 273, 274, 292, 305 | 16      | Live Stripe Dashboard verification                                                                                                     |
| 08: 800, 801, 804, 805, 806, 809, 810, 811, 819, 824, 827, 831, 832 | 13      | Deployed ALB routing, ASG replacement, EFS persistence, CloudWatch alarms, load behavior                                               |
| 04: 403, 406, 407, 417, 420, 421                                    | 6       | Real S3 bucket and object verification                                                                                                 |
| 02: 201, 206, 207, 285, 293..296                                    | 8       | Email delivery, renewal waits, Stripe trial lifecycle                                                                                  |
| 06: 605, 607. 06-2: 6216                                            | 3       | Real-AWS multi-instance and ECS crash-recovery behavior                                                                                |
|                                                                     | **100** |                                                                                                                                        |
| 05: 545, 546                                                        | 2       | Hours of real compute; the `@slow` lane already covers single tutorial runs                                                            |
| 01: 118                                                             | 1       | Live Stripe customer state                                                                                                             |

### Deferred as low value

| Rows                        | Count  | Why                                                                                                                                                                                     |
| --------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 04: 402, 415, 416, 427, 433 | 5      | Backend log wording. A test that asserts a log string pins prose, not behavior, and the underlying `calculate_limit_warning` logic is already covered by `test_storage_limit_alerts.py` |
| 02: 247, 248, 249           | 3      | The PDFs are Stripe-generated and Stripe-hosted. We could assert the link exists; we cannot assert its contents                                                                         |
| 02: 298                     | 1      | Responsive design across breakpoints. Playwright can resize, but the assertion ("no overlapping, readable") is not mechanisable without pinning a visual snapshot                       |
| 08: 814                     | 1      | Public experiment detail with visualizations needs published data with a real S3 round-trip                                                                                             |
|                             | **10** |                                                                                                                                                                                         |

Note: sheet 03 rows 336, 338, 339 and 340 are **already** automated by
`test_user_deletion.py`, which is why WP1 covers 37 rows and not 41.

---

## Sheet corrections found during triage

These are sheet defects, not product defects. All four are already applied to the
CSVs; they are recorded here because they change what the rows mean.

1. **Sheet 02, row 289** read "plan_id = 1 during grace period". Wrong:
   `crud_users.py` derives `Limit Grace` only when `plan_id == PREMIUM` and the
   expiration is 0 to -30 days old. Corrected to "plan_id remains 2 (Premium),
   expiration is in the past but within the 30-day grace, status reads Limit
   Grace".
2. **Sheet 02, row 291** read "plan_id = 2 (Free)". The number was right and the
   label wrong - nothing in the codebase writes `plan_id = FREE` on expiry or
   payment failure, so the premium row is retained and the tier is derived from
   the expiration date. Corrected to say no row is downgraded, and that past the
   30-day grace the status reads Expired.
3. **Sheet 02, row 222** ("Invalid Month") stated a mechanism with no outcome
   ("Input will be 01/32. Since there is no 13 in month"). Corrected to state the
   invariant - an invalid month cannot be submitted - while keeping the original
   tester's observation of the field re-slotting the digits.
4. **Sheet 05, row 516** was titled "Run Workflow Tutorial 3", but its Action
   ("Click RUN rapidly multiple times within 3 seconds") and Expected (cooldown
   enforced) both describe the run-button cooldown, so the **Subject** was the
   defect. Retitled "Run button cooldown (Tutorial 3)". Its mapping moved from
   `WF-06` to `WF-08 (partial)`, and the red-team audit argues for "No": `WF-08`'s
   own comment records that it covers the SnackbarProvider's `preventDuplicate`,
   **not** the run-POST debounce the row asserts.

Consequence of 4: no System row now covers a Tutorial 3 run to completion.
`WF-06` does that, but no sheet row claims it.

## Open questions

1. **Does the admin API currently reject self-delete server-side, or only hide
   the button?** WP1's acceptance criteria assume a server-side check. If there
   is none, WP1 grows a small production fix and should say so.
2. **Should `12-admin.spec.ts` run in the weekly CI e2e job?** It needs an admin
   account, which the current bootstrap script does not create. Either extend
   `.github/scripts/e2e-bootstrap.sh` or keep WP1's e2e half local-only and
   carry the API half in CI.
3. **Is a MAT fixture acceptable in-repo?** WP3 needs one. If binary fixtures
   are unwanted, WP3 drops to a generated-at-setup fixture, which costs runtime.

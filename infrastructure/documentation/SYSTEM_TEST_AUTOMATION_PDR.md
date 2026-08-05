# System Test Automation: Preliminary Design Review

**Status:** all three PRs written. PR 1 landed (30 rows); PR 2 under review (42 rows); PR 3 written (43 rows, and the one production fix Q1 found). Open questions resolved 2026-08-04 (see [Resolved questions](#resolved-questions)); the deviations each PR hit are recorded in [What PR 2 did differently](#what-pr-2-did-differently) and [What PR 3 did differently](#what-pr-3-did-differently)
**Baseline:** `SYSTEM_TEST_COVERAGE.md` as of 2026-08-03, after the red-team audit of all mapped rows
**Stacked on:** [PR #786](https://github.com/arayabrain/araya-optinist/pull/786), so that WP15 and WP16 are readable as code rather than as claims
**Scope:** the `Araya-Optinist System Test Cases Template` rows currently marked manual

## Executive Summary

- **236 of 409 System sheet rows have no automated test.** This PDR triages them and proposes which to automate
- **123 rows are automatable** with the test infrastructure that already exists (Playwright, jest, pytest). No new frameworks, services, or CI lanes are required
- **103 rows should stay manual permanently** - they assert Stripe-hosted UI, Stripe Dashboard state, or live AWS behavior that we cannot own from a test. A further 10 are automatable but deliberately deferred as low value
- **The single largest win is the admin Account Manager (sheet 03): 37 rows, entirely our own UI and API, with no coverage beyond the deletion and subscription paths**
- **Highest risk-reduction per hour is not the biggest package** - the free-user cleanup grace window (1 row) guards irreversible data deletion and is a half-day of work
- **Recommended split is three PRs**, grouped by reviewer lens: pytest + repairs, then jest/e2e, then the admin surface. See [Recommended sequencing](#recommended-sequencing)
- **WP1 carries a production fix.** The admin delete API accepts an admin deleting themselves; only the button is hidden. Firebase is deleted first and irreversibly, so the API call is unrecoverable

---

## Problem

The System sheets are the release gate for anything not covered by the Release
sheets. Today a release run hand-verifies 236 rows. That has five costs:

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
   291 mapped rows found 41 materially wrong and moved 34 System rows to `No`.
   Those 34 are folded in below as WP14-WP21, and each one's replacement mapping
   is recorded against its row in `SYSTEM_TEST_COVERAGE.md`. Two were
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
| WP16 | Premium assignment and restore gaps (**landed, PR #786**) | 06, 06-2 | 6       | pytest + jest       | L      |
| WP21 | Audit fallout: assorted single rows              | 01, 02, 03, 05 | 6       | pytest + jest + e2e | M      |
| WP17 | Workflow-count tracking, done properly           | 05             | 5       | pytest              | M      |
| WP20 | Storage-warning scenario gaps                    | 04             | 4       | e2e                 | S      |
| WP18 | Dataview publish and sync DB-state assertions    | 07             | 3       | pytest              | S      |
| WP19 | On-demand input sync, per format                 | 08             | 3       | pytest              | S      |
| WP14 | `SyncStatusView` + `useSyncRetry` suite          | 07             | 2       | jest                | S      |
| WP15 | `InactivityWarning` suite (**landed, PR #786**)    | 06-2           | 2       | jest                | S      |
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
4. Self-delete is rejected server-side, not only hidden in the UI. **It is not
   today** - see [Q1](#q1-does-the-admin-api-reject-self-delete-server-side), so
   WP1 carries the production fix and the test pins it.

**Scope addition: server-side self-delete guard.** `delete_user` in
`users_admin.py` already resolves `current_admin`; `crud_users.delete_user` never
receives it, so the guard belongs in the router, next to the identity it already
has. Rejecting `user_id == current_admin.id` there is the whole fix, and it
covers every caller of that route rather than the one path the UI takes. The
sheet's `cannot-delete-self` row becomes a real assertion instead of a UI
observation.

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

**No fixture work required.** `sample_data/tutorial/input/sample_matlab.mat` is
already tracked, and `10-uploads.spec.ts` already resolves its HDF5 fixture from
that same directory through its `SAMPLE` constant. WP3 is one more `path.join`
against a file the repo ships - see [Q3](#q3-is-a-mat-fixture-acceptable-in-repo).

**Risk:** the fixture is 16 MB, the same order as the HDF5 one `UPL-02` and
`UPL-04` already upload, so the added runtime is a known quantity rather than a
new one.

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
recovery) and are carried in [Out of scope](#out-of-scope-with-reasons); the
remaining 31 are the packages below. Per-row evidence is the replacement mapping
recorded against each row in `SYSTEM_TEST_COVERAGE.md`.

**Where the landed packages live.** WP15 and WP16, and the
`PremiumHeartbeatRetry` / `PremiumSleepDetection` remediation, are part of
[PR #786](https://github.com/arayabrain/araya-optinist/pull/786), which this
document is stacked on. Their tests are present in this tree and reach
`develop-main` when #786 merges, which is why they are counted in the 123 but
not scheduled into a PR below.

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

### WP15: `InactivityWarning` suite (landed, [PR #786](https://github.com/arayabrain/araya-optinist/pull/786))

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

### WP16: Premium assignment and restore gaps (landed, [PR #786](https://github.com/arayabrain/araya-optinist/pull/786))

**Rows:** 06/601, 06-2/6203, 06-2/6208, 06-2/6209, 06-2/6213, 06-2/6219 (6)

The largest audit cluster, and the one that matters most: sheet 06-2 is
[PR #786](https://github.com/arayabrain/araya-optinist/pull/786)'s own acceptance
evidence.

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
| 01/126 | Assert the sweep's predicates, not just its row counts. Note the row's own Action imports `studio/app/.../workflow_count_recovery.py`, which has no callers: the logic moved to the Common User Manager Lambda and that copy was left behind, so the mapping belongs on the Lambda |
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
| **Fix `nwb.py` returning HTTP 200 with body `false`** when no `.nwb` exists, and make BT-509 assert a real NWB payload (**done, PR 1**) | A product bug. The test passed on a 5-byte `false` blob, so the automated _and_ manual checks were both suppressed. Both routes now 404; `test_nwb.py` pins it and REC-07 checks the HDF5 signature. The two pytest cases that lived there asserted `status_code == 200` and `response.url`, so they passed on the bug |
| **Fix BT-403 (Cell-ROI)** to assert an ROI-specific artefact (**done, PR 1**)                                           | It re-asserted a plot already awaited before the ROI was selected. VIS-02 now counts Plotly traces across the selection (1 -> 2) and asserts the ROI response is 200                                                                                                                                                                                                                              |
| **Delete or rewrite `PremiumHeartbeatRetry.test.ts` and `PremiumSleepDetection.test.ts`** (**done, PR #786**)            | Both re-implemented the logic under test inside the test file and asserted against their own copies. `PremiumHeartbeatRetry.test.ts` was rewritten against the real provider (attempt count, growing backoff, terminal rethrow, free-tier no-op, device-wake wiring); `PremiumSleepDetection.test.ts` was deleted, its subject being covered by `useSleepDetection.test.ts` against the real hook |
| **Correct the sample-data precondition** in `helpers.ts` and `RELEASE_TEST_COVERAGE.md`                                | Both claim the import ships pre-computed outputs; it ships metadata YAML only, so snakemake recomputes. Also reconcile the 600s `beforeEach` sitting inside an 840s inner wait                                                                                                                                                                                                                    |
| **Fix `importSampleData` clicking a disabled menu item** (**done, PR 2**) | Hit while verifying WP3 and WP11, which could not run at all without it. `3a86c953a` narrowed the import menu entry from `disabled={!workspaceReady}` to `disabled={!isRecordTab}`, while the helper switched to the Workflow tab first, because that is where it probes for the workspace name that signals the store is populated. The two requirements became contradictory, so every data-dependent spec failed in its `beforeEach` with a misleading pointer-interception error: a disabled `MenuItem` takes no pointer events, so the click lands on the menu list. The helper now probes on Workflow and imports from Record. The same fix was already in flight elsewhere; this one matches it line for line so whichever lands first, the other is a no-op |
| **Fix REC-07's NWB precondition check** (**done, PR 2**) | Surfaced by the helper repair above, not caused by it: with the spec unblocked, REC-07 became reachable and reliably red. Its guard asked whether the NWB button was enabled, which is always true - the imported tutorial metadata declares an `nwb` section while no `.nwb` ships - so it clicked, the route answered the 404 that PR 1 introduced in place of `200 false`, and no download ever arrived. The guard now keys off the response status: it skips with an accurate reason when there is no completed run, and still asserts the HDF5 signature when there is one (verified with `RUN_SLOW=1`, where WF-04 mints a real NWB first) |
| **Add a skip gate** to the e2e run                                                                                     | For a sign-off sheet a skipped test proves nothing. Either fail the run when a High-priority mapped test skips, or emit a per-row skip summary the tester reconciles against the sheet                                                                                                                                                                                                            |
| **Require `(partial)` to name its uncovered half**                                                                     | Most of the 85 MEDIUM audit findings are rows where `(partial)` was doing unbounded work. The premium tables already model the honest style                                                                                                                                                                                                                                                       |

---

## Recommended sequencing

Eight PRs, smallest-risk-first so the branch does not become one unreviewable
diff. PR 0 comes first: it repairs tests that currently mislead.

Reorganised from eight PRs into **three**, grouped so each carries one reviewer
lens rather than one work package. The eight-PR split minimised individual diff
size but spread the same review context across several PRs: the pytest packages
alone appeared in four of them.

| PR  | Branch                        | Contents                                                     | Rows | Reviewer lens                                                                                                     |
| --- | ----------------------------- | ------------------------------------------------------------ | ---- | ----------------------------------------------------------------------------------------------------------------- |
| 1   | `feature/system-test-automation-1` (**landed**) | Remediation + WP5, WP6, WP7, WP9, WP10, WP13, WP17, WP18, WP19 | 30   | pytest, terraform config, and the repairs. No new harness. Carries one production fix (`nwb.py`)                  |
| 2   | `feature/system-test-automation-2` (**written**) | WP2, WP3, WP4, WP11, WP12, WP14, WP20, WP8                   | 42   | jest and Playwright, all additions to specs that already exist                                                    |
| 3   | `feature/system-test-automation-3` (**written**) | WP1 (+ the self-delete fix), WP21                            | 43   | The admin surface. The only PR that changes production code beyond PR 1's `nwb.py` fix                            |

30 + 42 + 43 = 115. The missing 8 are WP15's 2 and WP16's 6, already written in
PR #786 and so counted in the 123 above but not scheduled again; 115 + 8 = 123.

Two ordering constraints from the original split are preserved: the remediation
lands first, and WP8's route-mock pattern now settles in the *same* PR as the
rest of the e2e work rather than in the one after it. WP17, WP18 and WP19 moved
into PR 1 because they are pytest, and would otherwise be strays in a frontend
PR.

Each PR updates its rows in `SYSTEM_TEST_COVERAGE.md`. A PR that adds tests
without updating the map is incomplete, because the map is what the release
tester reads.

---

## What PR 2 did differently

Five things in PR 2 came out other than this document specified. Each is a fact
about the product that the triage got wrong, not a change of plan.

1. **Rows 110, 111 and 113 have no IDs of their own.** They are extra assertions
   inside `AUTH-04`, which is what WP4's own risk note asks for ("these rows must
   reuse that account rather than registering more"). A second `AUTH-1x` test
   would mean a second throwaway Firebase account per weekly run, and the
   success screen is redux-only, so reaching it again means registering again.
   `AUTH-12`..`AUTH-16` cover the other five rows.
2. **413 / 423's real copy is the waiting notice, not the fallback warning - and
   that is arguably a product gap.** A premium user parked on shared compute is
   told only "your dedicated resource is being prepared", indefinitely, with
   nothing distinguishing that from an assignment still in flight. The rows
   originally asserted that such a user is told they are on shared resources.
   Correcting the sheet to match the product closes the row but does not answer
   whether the product should say something; that is worth a decision rather
   than a silent sheet edit. `STO-03` therefore asserts the state the routing
   service records, which is the only thing that distinguishes the two.
   This document guessed "Falling back to shared resources." That string is the
   *assignment-error* branch. A successful shared assignment simply is not a
   dedicated one, so `PremiumNotificationManager` keeps
   "Please wait while your dedicated premium resource is being prepared." up.
3. **Row 251 creates no portal session.** WP8 said "the billing-portal button
   creates a portal session. Assert our request and the redirect target." There
   is no request: `handleManageBilling` opens a hardcoded
   `billing.stripe.com/p/login/...` link in a new tab. `SUB-14` asserts the
   destination host, and the row stays `(partial)`.
4. **Row 447's Action was wrong about the product**, and is now corrected in the
   sheet. It says to log out and log back in, but both logout paths clear
   `storage-refreshed-on-login` deliberately, so the next login refreshes again -
   correctly, since it may be a different user. The invariant is one refresh per
   session across repeated auth checks, which is what the test asserts.
5. **DV-16 runs on `/dataview` (all workspaces), not `/public`.** Both render
   `DataviewRecords` with no `workspaceId`, so it is the same `filterable:
   !workspaceId` column and the same server-side filter, and this avoids a
   publish/unpublish dance that `DV-14` already owns. `DV-16` also asserts the
   carve-out the row names: no filter menu at `/dataview/{id}`.

Also worth recording for PR 3: rows 429 and 440 needed the ballast in
`11-lifecycle.spec.ts` to grow past the free limit for real. An expired premium
account is held to the free-tier quota by `_effective_quota_bytes` whatever its
quota column says, so no amount of dialing `storage_quota_bytes` reaches that
state.

---

## What PR 3 did differently

Six things came out other than this document specified. Each is a fact about the
code that the triage got wrong, not a change of plan.

1. **The admin gate is not per route, so WP1's acceptance criterion 1 could not
   be met as written.** It asks for a 403 "asserted per route rather than once".
   Each route does take `current_admin: User = Depends(get_admin_user)`, but that
   parameter is how the route learns who is calling: enforcement is the
   `dependencies=[Depends(get_admin_user)]` on `include_router`, and **either one
   alone answers 403**. Removing a single route's parameter is therefore
   unobservable, and a per-route assertion cannot catch it - verified by mutation,
   which is how it was found. The tests now do both: a request per route (which
   fails only when the gate is genuinely gone, from both places) plus an
   assertion, against the mounted app rather than one router, that every `/admin`
   route resolves `get_admin_user` in its dependency tree. That second half is
   the one a newly mounted admin router would fail.
2. **Rows 311 and 312's real server-side gate is the Pydantic schema, not the
   Firebase error mapping.** `UserCreate` declares `email: EmailStr` and
   `password: str = Field(regex=password_regex)`, so both are rejected with 422
   before `crud_users.create_user` runs; its `INVALID_EMAIL` and `WEAK_PASSWORD`
   branches are unreachable through the router. The tests pin the schema. And
   `password_regex` **forbids nothing**: it is three lookaheads over `.{6,255}`,
   so `abcd1!<` is accepted server-side. The forbidden-character rule the sheet
   describes is frontend-only. That divergence is recorded in the coverage map
   rather than asserted, because a test pinning the backend's permissiveness
   would fail the day someone tightens it.
3. **Row 338 answers 404, not the 401/403 WP21 predicted.** `authenticate_user`
   filters `active.is_(True)` in its lookup, so a deactivated account has nothing
   left to report. The sheet asks for "an appropriate error", which this is.
4. **WP1's e2e half bootstraps its own admin instead of taking one from the CI
   script, and Q2's three-line bootstrap edit shrank to one.** Q2 proposed
   registering a third account in `e2e-bootstrap.sh` and fixing up `user_roles`
   there. `12-admin.spec.ts` does that itself, the way `11-lifecycle.spec.ts`
   already bootstraps its own account, so the spec also runs locally rather than
   only after a CI-only script. The bootstrap change is the one line that deletes
   the stale Firebase user (dev Firebase persists between runs while the CI DB
   starts empty), and `e2e.yml` gains a fixed address plus the existing password
   secret - no new secret.
5. **Rows 318/319 are jest against the modal, not against the grid.** The row's
   Edit button lives in the Account Manager grid's last column, which MUI's
   DataGrid virtualizes away in jsdom (the viewport measures zero wide), so
   `ModalComponent` is exported and rendered directly. That the modal opens on
   the right row's values - row 314 - is asserted in `12-admin.spec.ts`, where
   the viewport is real. This is the only production edit in the PR besides the
   self-delete guard: one `export` keyword.
6. **Row 299 is jest, not e2e.** WP21 says `LC-16` registers a free user, so the
   premium warning lines are unreachable. Reaching them from a real login means a
   real paid subscription, so the per-tier copy is asserted in
   `AccountProfile.test.tsx` with the subscription in the store - both the
   premium lines' presence and their absence for a free and an expired-premium
   account.

Three items PR 3 first listed as out of scope were then pulled in on request,
and each turned out to be more than the mechanical change it looked like:

- **`getFilesTree` had no `rejected` case**, so a failed file-tree fetch left
  `isLoading` set and the progress bar spinning with nothing said. The open
  question was whether `isLatest` should flip back to `true`; `useFileTree`
  answers it - it refetches on `!isLatest && !isLoading`, so clearing the loading
  flag while leaving `isLatest` false would turn one failure into a request loop.
  It now mirrors `deleteFile.rejected` exactly, and the retry path is the same
  Select File button that dispatches unconditionally. Writing the test also
  surfaced that notistack 3 assigns its standalone `enqueueSnackbar` inside
  `SnackbarProvider`'s constructor, so the real one is undefined in a reducer
  test - which is why the sibling `deleteFile.rejected` had never been tested.
- **The SQLite harness is now one module**, `studio/tests/app/common/sqlite_harness.py`,
  imported by four test files that each carried their own copy of the `@compiles`
  block and the `ON UPDATE CURRENT_TIMESTAMP` strip. The process-global caveat is
  documented once, in the one place that causes it. The full app lane is
  unchanged at 1467 passed, which is what makes it a refactor rather than a
  change.
- **A confirmed deletion is now driven through the admin UI** (`ADMIN-08`), on an
  account the test registers for the purpose. It leaves nothing behind in
  Firebase, because deleting the Firebase account is step 1 of the pipeline under
  test. It also found that row 336's `user_roles` expectation does not match the
  code: `crud_users.delete_user` never deletes from that table, so a tester
  following the sheet literally would mark the row FAIL. Recorded in the coverage
  map for a decision rather than silently corrected.

One process note worth carrying forward: **mutation-checking a destructive e2e
path destroys local state.** Making ADMIN-06's Cancel confirm instead really did
delete the local free e2e user, Firebase account first. It is recoverable (re-register
at the same address, which is row 341's behaviour), and the recovery is written
down at the top of `12-admin.spec.ts`. The same check also found a race in the
test itself: reading the database straight after Cancel can beat the request it
is meant to catch, because the modal closes without awaiting the dispatch. Each
Cancel row now asserts that no write request was issued, with the listing GET as
the positive control.

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
| 05: 545, 546                                                        | 2       | Hours of real compute; the `@slow` lane already covers single tutorial runs                                                            |
| 01: 118                                                             | 1       | Live Stripe customer state                                                                                                             |
| 02: 288. 06: 603, 608                                               | 3       | Audit fallout that is manual, not automatable: Stripe past-due state, the CloudWatch heartbeat line, real cross-instance S3 recovery   |
|                                                                     | **103** |                                                                                                                                        |

### Deferred as low value

| Rows                        | Count  | Why                                                                                                                                                                                     |
| --------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 04: 402, 415, 416, 427, 433 | 5      | Backend log wording. A test that asserts a log string pins prose, not behavior, and the underlying `calculate_limit_warning` logic is already covered by `test_storage_limit_alerts.py` |
| 02: 247, 248, 249           | 3      | The PDFs are Stripe-generated and Stripe-hosted. We could assert the link exists; we cannot assert its contents                                                                         |
| 02: 298                     | 1      | Responsive design across breakpoints. Playwright can resize, but the assertion ("no overlapping, readable") is not mechanisable without pinning a visual snapshot                       |
| 08: 814                     | 1      | Public experiment detail with visualizations needs published data with a real S3 round-trip                                                                                             |
|                             | **10** |                                                                                                                                                                                         |

Note: sheet 03 rows 336, 339 and 340 are **already** automated by
`test_user_deletion.py`. Row 338 was in that set until the audit moved it to
`No` - `test_contract_firebase_deleted_blocks_login` asserts that Firebase's
`delete_user` was called, never that a subsequent login is rejected - so it is
now WP21's. 37 + 3 + 1 = the sheet's 41 rows, which is why WP1 covers 37.

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

5. **Sheet 03, rows 336 and 340** both expected `user_roles` to hold 0 rows after
   a deletion. `crud_users.delete_user` never touches that table - it deletes
   `user_preferences` and soft-deletes workspaces, but the role link survives - so
   a tester following the sheet would have marked both rows FAIL against correct
   behaviour. Corrected to expect the row to remain, with the reason inline. The
   link is inert: `users.active` is 0 and every user query filters on it. Found
   while writing `ADMIN-08` (PR 3).

## Resolved questions

All three questions that blocked this PDR were answered against the code on
2026-08-04. Each answer changed a work package, so they are recorded with the
evidence rather than as a decision log.

### Q1: Does the admin API reject self-delete server-side?

**No. The guard is UI-only, and the delete is unrecoverable.**

`AccountManager/index.tsx` hides the Delete and Proxy SignIn buttons for the
signed-in admin's own row by testing `params.row?.id === user?.id`. Nothing
mirrors that server-side: `users_admin.py`'s `delete_user` passes only `user_id`
and `organization_id` into `crud_users.delete_user`, which therefore never
learns who the caller is and could not enforce the rule even if it wanted to. A
`DELETE /admin/users/{own_id}` from any admin token is accepted.

The consequence is not cosmetic. `crud_users.delete_user` deletes the Firebase
account **first**, by design and documented in its own docstring, because that is
the hardest step to reverse. An admin who calls the route against themselves
destroys their own auth account before anything else happens.

Both halves came from the same commit, `5194e3357` ("[93] add account manage
page", 2023-09-22), which added the page and replaced the delete route's stub
with its real implementation. The author did think about self-deletion - the
DataGrid check is deliberate, and it suppresses Proxy SignIn on the same row -
and simply implemented it in the layer they were building. The fix preserves
that intent rather than reverting anything.

**Effect on the PDR:** WP1 grows a production fix, one guard in
`users_admin.py::delete_user` where `current_admin` is already resolved. The
Executive Summary and WP1 both now say so, and PR 3 is flagged as the only PR in
the sequence that changes production code.

### Q2: Should `12-admin.spec.ts` run in the weekly CI e2e job?

**Yes, and the bootstrap change is three lines.**

`.github/workflows/e2e.yml` runs on `cron: "0 0 * * 1"` plus `workflow_dispatch`,
and calls `.github/scripts/e2e-bootstrap.sh`. That script registers both CI users
through `/api/register` with `role_id: 20`, which is `UserRole.operator`;
`UserRole.admin` is `1`. So the premise of the question holds - CI has no admin.

Registration cannot be asked for one. `crud_users.create_user` overwrites
`data.role_id` with `UserRole.operator.value` whenever `verified` is false, which
is exactly the `/register` path, so a client cannot self-elevate. Only the admin
router calls it with `verified=True` and an honoured `role_id`.

The bootstrap already solves this shape of problem once: it registers the premium
user normally, then fixes up `subscription_users` and `user_storage_usage` with a
SQL block. An admin user is the same move against `user_roles` - register a third
account, then `UPDATE user_roles SET role_id = 1` for it. That is cheaper than
splitting WP1's e2e half into a local-only lane, and it keeps the sign-off sheet
reading from one CI run.

**Effect on the PDR:** WP1's e2e half stays in CI. The bootstrap edit belongs in
PR 3 alongside `12-admin.spec.ts`.

### Q3: Is a MAT fixture acceptable in-repo?

**The question is moot - the fixture is already committed.**

`sample_data/tutorial/input/sample_matlab.mat` is tracked, and it sits in the
same directory `10-uploads.spec.ts` already reads through its `SAMPLE` constant
to reach `sample_hdf5.h5` for `UPL-02` and `UPL-04`. WP3 adds a `path.join`, not
a fixture.

**Effect on the PDR:** WP3's stated risk - commit a small `.mat`, generate it
from a checked-in script - is deleted. The residual risk is runtime: the file is
16 MB, comparable to the 16 MB HDF5 the suite already uploads.

---

## Known inconsistency outside this document

Stacking on #786 is what makes the sheet 06-2 rows of `SYSTEM_TEST_COVERAGE.md`
true: they name `PremiumLifecycleIntegration.test.tsx`,
`TestSoftReleaseUserAssignment` and `TestRestorePendingReleaseTransaction`, which
exist in this tree and nowhere on `develop-main`. Landing this document on
`develop-main` ahead of #786 would reintroduce that gap.

One inconsistency survives the stack: **some rows still carry their pre-audit
mapping.** 07/717 and 07/718 still read `ImagePlotSimple.test.tsx (partial)`, the
mapping WP14 exists to replace, and 03/338 still reads
`test_contract_firebase_deleted_blocks_login`, which asserts that Firebase's
`delete_user` was called rather than that a later login is rejected - the reason
the audit moved it to WP21.

That is not a defect in this PDR and it does not change the triage. It is
recorded because Goal 3 makes the map's truthfulness a deliverable, and because a
reader reconciling this document against the map will otherwise hit it.

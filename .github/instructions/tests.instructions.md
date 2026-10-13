---
applyTo: "studio/tests/**,frontend/src/**/*.test.ts,frontend/src/**/*.test.tsx,frontend/e2e/**"
---

# Tests

- Coverage means the test fails with the fix reverted. Flag a test that guards on a condition its setup already guarantees, or whose only assertion is that a mock was called, unless that call is itself the contract.
- An unconditional `@pytest.mark.skip` must cite a linked issue in its decorator (`test_no_dead_tests.py` enforces this); otherwise use `skipif` on a real condition.
- A PR that changes a wrapper's output shape must update the fixtures that depend on it.
- A test on full-size data belongs under the `heavier_processing` marker, which the default lane excludes.

## Playwright specs (frontend/e2e)
- Locate rows and items by exact text or a test id, not by substring (`has-text`): a copied record whose name extends the original also matches.
- No fixed sleeps: use web-first assertions or `expect.poll` with a bounded timeout.
- Specs run serially (`workers: 1`) and share workspaces. A spec that creates data must tolerate leftovers from an earlier failed run and clean up after itself.
- Tag real workflow runs `@slow` and cases that degrade the shared environment `@disruptive`; they are excluded unless `RUN_SLOW` or `RUN_DISRUPTIVE` is set.

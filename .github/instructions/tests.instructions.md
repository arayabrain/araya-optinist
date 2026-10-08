---
applyTo: "studio/tests/**,frontend/src/**/*.test.ts,frontend/src/**/*.test.tsx,frontend/e2e/**"
---

# Tests

- Coverage means the test fails with the fix reverted. Flag a test that guards on a condition its setup already guarantees, or whose only assertion is that a mock was called, unless that call is itself the contract.
- An unconditional `@pytest.mark.skip` must cite a linked issue in its decorator (`test_no_dead_tests.py` enforces this); otherwise use `skipif` on a real condition.
- A PR that changes a wrapper's output shape must update the fixtures that depend on it.
- A test on full-size data belongs under the `heavier_processing` marker, which the default lane excludes.

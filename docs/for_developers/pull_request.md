## Pull Request
- GitHub Pull Request page
  - [https://github.com/arayabrain/araya-optinist/pulls](https://github.com/arayabrain/araya-optinist/pulls)

### Pre Commit
- run following command before your first commit
  ```
  pre-commit install
  ```
  - Once installed, it automatically checks your coding style on every commits.

---

# Pull Request Style Guide

Standards for branches, commits, and pull requests on this project.

---

## Branch Rules

- You can submit a Pull Request by pushing a new branch.
  - Make sure the base branch is `develop-main`, and the PR targets `develop-main`.
  - You can't push to `develop-main` directly -- the branch is protected.

## Branch Naming

```
<type>/<short-description>
```

| Type | When to use | Example |
|------|-------------|---------|
| `feature/` | New functionality | `feature/export-csv` |
| `fix/` | Bug fix | `fix/login-redirect-loop` |
| `refactor/` | Code restructuring with no behavior change | `refactor/download-coordinator` |
| `test/` | Test-only additions or fixes | `test/staleness-spot-check` |
| `docs/` | Update documentation | `docs/premium-user` |

Rules:
- Lowercase, hyphens between words (no underscores, no camelCase).
- Keep it under ~40 characters after the type prefix.

---

## Commit Messages

```
<type>: <imperative summary>
```

**Always a single line -- no body.**
- Start with a lowercase type prefix followed by colon and space.
- Use imperative mood ("add", "fix", "remove" -- not "added", "fixes", "removing").
- Keep under 72 characters.
- Do not end with a period.
- Do not mention Claude.
- Reference issues inline if needed: `fix: resolve login redirect loop (Closes #42)`

**Types** -- same set as branch naming:
`feature`, `fix`, `refactor`, `test`, `docs`

Examples:

```
fix: return grace period warning for expired users under free limit
```

```
refactor: replace direct S3 calls with DownloadCoordinator
```

```
test: add coverage for staleness spot-check
```

What to avoid:
- `Update files` -- too vague, says nothing about intent.
- `Fix bug` -- which bug? Be specific.
- `WIP` -- squash or reword before opening a PR.

---

## Pull Request Template

Every PR description must include the sections below. Copy this skeleton and fill it in.

```markdown
# Pull Request: <Short title matching the branch purpose>

**Current Branch:** <branch-name>
**Target Branch:** <base-branch>

----------
### Content

#### Summary
- <Give a 3-5 line executive summary of the changes. >

#### Design Decisions
- <Explain each non-obvious choice. Why this approach over alternatives?>
- <If a decision has trade-offs, state them.>

### Evidence
- <Include log entries, screenshots, or recordings that demonstrate the change.>

### References
- <Links to tickets, related PRs, docs, or Slack threads>

### Files changed
- `path/to/file.py` -- <one-line summary of what changed and why>
- `path/to/other_file.tsx` -- <one-line summary>

### Manual Testcases
- <Step-by-step manual verification instructions>
- <Include user/role, action, and expected result>
- <End with the test command and expected outcome, e.g.:>
  `pytest path/to/tests/ -- all N tests pass`

### Unit, Integration, Contract Test Coverage

### Others

#### Difficulties (if any)
<What was tricky, what took longer than expected, or "None">

#### Risk Assessment
| Area | Risk | Notes |
|------|------|-------|
| <component or behavior> | Low / Medium / High | <why and what to watch for> |
```

---

## Section Tips

Guidance for sections that need more than what the template placeholders say.

### Title

- Start with a verb matching the branch type: a `fix/` branch → "Fix ...", a `feature/` branch → "Add ...".
- Keep under 70 characters.

Good: `Fix grace period warning not shown for expired users under free limit`
Bad: `Updated cloud_utils.py`

### Summary

Focus on user-visible or system-visible outcomes, not implementation details. If the PR fixes a bug, state the symptom and the fix. If it adds a feature, state what the user can now do.

```markdown
#### Summary
- Premium users whose subscription has expired now see a warning dialog
  on the Dashboard showing the number of grace-period days remaining.
- Free-tier users and active premium users are unaffected.
- Added CloudWatch logging for the premium popup endpoint.
```

### Design Decisions

This is the most important section. Answer the questions a reviewer would otherwise ask:
- **Why this approach?** -- "Used file-based claim sentinels instead of Redis because the system runs on single-node EBS without a shared cache."
- **What was considered and rejected?** -- "Considered adding a retry queue but deferred since the coordinator already deduplicates."
- **What are the trade-offs?** -- "Input file downloads still bypass the coordinator because they use a different directory structure. Documented as a known limitation."

If the change is straightforward, a single bullet is fine.

### Files changed

For large PRs, group by area:

```markdown
#### Backend
- `studio/app/common/routers/outputs.py` -- Route all downloads through DownloadCoordinator
- `studio/app/common/core/storage/sync_tier.py` -- New: define download tier hierarchy

#### Frontend
- `frontend/src/store/slice/DisplayData/DisplayDataActions.ts` -- Add structured error payloads to thunks
```

### Difficulties

Examples of useful entries:
- "The atomic claim file needed O_CREAT|O_EXCL for race-free creation; standard `open()` has a TOCTOU window."
- "Mocking the lazy import inside `run()` required patching the source module, not the importing module."
- "None" is a valid answer.

### Risk Assessment

Be honest -- "Low" everywhere is a signal you haven't thought about it.

| Risk Level | When to use |
|------------|-------------|
| **Low** | Isolated change, single consumer, good test coverage |
| **Medium** | Multiple consumers, behavioral change, partial test coverage |
| **High** | Shared infrastructure, data migration, no rollback path |

Example:

| Area | Risk | Notes |
|------|------|-------|
| Download deduplication | Medium | New singleton; crashes during init could block all downloads |
| Input file sync | Low | Bypasses coordinator intentionally; no behavior change |

---

## Checklist Before Opening a PR

- [ ] Branch name follows `<type>/<description>` convention.
- [ ] All commits use imperative mood with a type prefix.
- [ ] PR title is under 70 characters and starts with a verb.
- [ ] Every file in the diff is listed under "Files changed" with a summary.
- [ ] "Design Decisions" explains any non-obvious choices.
- [ ] "Testcase" has step-by-step manual verification instructions.
- [ ] "Testcase" ends with the automated test command and expected result.
- [ ] "Risk Assessment" table is filled in honestly.
- [ ] No `WIP` commits remain (squash or reword them).
- [ ] CI is green.

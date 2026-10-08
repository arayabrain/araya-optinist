---
name: code-review
description: OptiNiSt pull request review rules for security and workspace authorisation, memory and resource use, deployment tiers, and saved workflows. Use when reviewing pull requests in this repository.
---

# OptiNiSt code review

Area rules live in `.github/instructions/*.instructions.md` and apply only to matching files. Where code lives: `references/repo-map.md`.

## How to review
- Comment only on a concrete failure: a wrong result, crash, security hole, data loss, or a limit exceeded at a realistic input size. For size, name it and the limit ("a 2,250-frame 512x512 uint16 stack is 1.2 GB; this holds it twice").
- Severity: High = security, data loss or outage. Medium = wrong result or user-visible failure. Low = maintainability.
- Skip anything a workflow in `.github/workflows/` already enforces (lint, format, spelling, migrations).
- Rules state an invariant; the symbol named is today's example. If it no longer exists, apply the invariant and say in the summary that this skill entry is stale.
- Review new or changed code only. No rewrites of untouched code, no docs requests unless public behaviour changed, no comments on PR body formatting.

## Security (High)
- A workspace-scoped route needs a per-route authorisation dependency (today `is_workspace_owner` for writes, `is_workspace_available` for reads). The router-level `get_current_user` only authenticates. Admin actions need an admin guard (today `get_admin_user`).
- A path from request data must be `join_filepath([<trusted root>, ...parts])`, first element server-side (`DIRPATH.*`). A bare string or a request-controlled first element is not contained; only `..` is refused.
- Reject any rewrite of the containment check in `join_filepath`: its `startswith()` must stay the whole `if`, or CodeQL loses the sanitizer and `py/path-injection` reopens repo-wide.
- `like`, `ilike` or `contains` on user input needs `autoescape=True`.
- Never trust routing headers (`x-user-tier`, `x-routing-id`) from the client; `secure_routing_middleware.py` sets them.

## Resource use (High when it can take the service down)
API and workflow runs share each instance's memory and disk, so one oversized allocation fails `/health` for every user.
- Flag reading user data (TIFF, NWB/HDF5, CSV, S3 objects) in full when part is needed; High in the API process. Ask for range reads.
- Flag extra full-size copies, and `.tolist()` or JSON of full-size arrays.
- Flag unbounded caches, result sets and worker counts. Lock waits, external calls and polling loops need timeouts.
- `async def` handlers: new blocking file, S3, subprocess or heavy numpy work goes off the event loop.

## Deployment modes
- `INSTANCE_MODE` picks the tier; `public` skips the workflow routers, so module-level wrapper or snakemake imports in routers it loads break its startup.
- `IS_STANDALONE` switches single and multi-user; code assuming a DB, Firebase user or subscription must handle standalone, and the reverse.

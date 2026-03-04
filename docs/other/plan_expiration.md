# Data Retention & Deletion Policy

## Overview

When a premium subscription ends, users enter a **30-day grace period**. During this period, all data remains intact (up to the 200 GB premium limit). After the grace period, data is automatically reduced to fit within the **5 GB free-tier limit**.

**Deleted data cannot be recovered.**

## Grace Period Timeline

### Day 1 — Subscription Ends

- Email notification: *"Your subscription has ended. You have 30 days to download or manage your data."*
- Include link to storage management page
- Include link to download tool

### Day 20 — Reminder

- Email with **storage usage breakdown** by workspace
- Link to manually delete or download data
- Reminder of the deletion date and current deletion priority setting

### Day 30 — Auto-Deletion

- All-at-once deletion using the user's chosen priority order
- Stops as soon as usage is at or below 5 GB
- Workflow YAML files are **never deleted**

### Post-Deletion — Confirmation

- Email summary of what was removed (workspace names, data types, total size freed)

## Deletion Priority Setting

Users can configure their preferred deletion order in account settings. Within each tier, **oldest data is deleted first**, and **published workspaces are deleted last** (after all unpublished data in that tier has been removed).

| Setting | Order | Best For |
|---------|-------|----------|
| **Preserve Outputs** (default) | Intermediates → Inputs → Outputs | Users who can re-upload raw data |
| **Preserve Inputs** | Intermediates → Outputs → Inputs | Users who can re-run workflows but can't easily re-obtain source data |

In both modes:

- Workflow YAML files are **never deleted**
- **Published workspaces are prioritized for preservation** — within each data tier, unpublished workspace data is deleted before published workspace data
- Deletion proceeds oldest-first within each tier
- Deletion stops as soon as storage is at or below 5 GB

Data deletion order (default)
  1. Intermediates (unpublished, oldest first)
  2. Intermediates (published, oldest first)
  3. Inputs (unpublished, oldest first)
  4. Outputs (unpublished, oldest first)
  5. Inputs (published, oldest first)
  6. Outputs (published, oldest first)
  7. YAMLs — never

## Data Download

Before the grace period ends, users can download data per-workspace with selectable checkboxes:

- Input data
- Output data (NWB files)
- Workflow YAML files

Intermediate/node outputs are **not offered for download** (they are regenerable by re-running the workflow).

## Key Policy Statements

1. **Deleted data is permanently gone.** Re-subscribing does not restore deleted data.
2. **No workspace-level protection.** Users cannot exempt specific workspaces from auto-deletion — they must download what they want to keep.
3. **YAMLs are always preserved.** Workflow definitions remain available regardless of storage status, enabling users to re-run workflows if they re-subscribe or re-upload data.

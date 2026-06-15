## Summary

- Add `.dockerignore` to prevent credentials in `studio/config/.env` from being copied into Docker images via `COPY studio /app/studio` (Dockerfile:58)

## Problem

`studio/config/.env` contains hardcoded credentials (AWS access keys, Stripe secret keys, DB credentials). While `.gitignore` correctly excludes `.env*` files from version control, no `.dockerignore` existed at the project root. This meant `COPY studio /app/studio` in the Dockerfile copied the `.env` file into every Docker image pushed to ECR.

| Item | Git | Docker (before) | Docker (after) |
|------|-----|-----------------|----------------|
| `studio/config/.env` | Excluded (`.gitignore`) | **Included** | Excluded (`.dockerignore`) |
| `studio/config/.env.example` | Included | Included | Included |

## Affected credentials

| Credential | Present in `.env` |
|------------|:-----------------:|
| AWS Access Key (`AKIAZI2LI65BAJKI2GUW`) | Yes |
| AWS Secret Access Key | Yes |
| Stripe Secret Key (`sk_test_...`) | Yes |
| Stripe Webhook Secret (`whsec_...`) | Yes |
| DB credentials (`admin/admin`) | Yes |

> **Note:** Production DB credentials are not affected — `cloud-startup.sh` overrides `MYSQL_*` vars from ECS task definition environment variables at runtime. However, the AWS and Stripe keys in the image are valid credentials.

## Changes (1 file, +35)

| File | Change |
|------|--------|
| `.dockerignore` (new) | Excludes `.env*`, `.git`, IDE files, `__pycache__`, `node_modules`, `.claude`, Terraform state. Preserves `.env.example` files. |

## Required follow-up actions (operational)

- [ ] **Key rotation:** Rotate the exposed AWS access key and Stripe keys
- [ ] **Image rebuild:** Rebuild and redeploy after merge so existing ECR images with embedded credentials are replaced

## Test plan

- [ ] Verify `docker build -f studio/config/docker/Dockerfile -t test .` succeeds (no missing files)
- [ ] Verify `studio/config/.env` is NOT present inside the built image: `docker run --rm test ls -la /app/studio/config/.env` should return "No such file or directory"
- [ ] Verify `studio/config/.env.example` IS present inside the built image: `docker run --rm test cat /app/studio/config/.env.example` should show the template

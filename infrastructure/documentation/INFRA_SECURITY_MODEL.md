# Infrastructure Security Model

> See also: [Architecture](TERRAFORM_ARCHITECTURE.md) | [Deployment](INFRA_DEPLOYMENT_PROCEDURE.md)

---

## Safety Model

### Why Accidental Cross-Environment Destruction Is Impossible

Each environment has its own S3 backend (state bucket). When you run `terraform init -backend-config=backends/development.hcl`, Terraform physically connects to the development state bucket and **cannot see** production resources.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        terraform/ (code)                            │
│                                                                     │
│   ┌──────────────────────┐       ┌──────────────────────┐          │
│   │ backends/             │       │ environments/         │          │
│   │  production.hcl ─────┼──┐    │  production.tfvars    │          │
│   │  development.hcl ────┼──┼─┐  │  development.tfvars   │          │
│   └──────────────────────┘  │ │  └──────────────────────┘          │
└─────────────────────────────┼─┼────────────────────────────────────┘
                              │ │
                    ┌─────────┘ └─────────┐
                    ▼                     ▼
        ┌───────────────────┐  ┌───────────────────┐
        │ S3: subscr-       │  │ S3: development-   │
        │ optinist-for-     │  │ optinist-for-      │
        │ cloud-tfstate     │  │ cloud-tfstate      │
        │                   │  │                    │
        │ (production       │  │ (development       │
        │  state only)      │  │  state only)       │
        └───────────────────┘  └───────────────────┘
                 │                       │
                 ▼                       ▼
        ┌───────────────────┐  ┌───────────────────┐
        │ AWS Resources:    │  │ AWS Resources:     │
        │ subscr-optinist-* │  │ development-       │
        │                   │  │ optinist-*         │
        └───────────────────┘  └───────────────────┘
```

---

## S3 Bucket Isolation (IAM Policy)

Each environment's IAM user has a **two-layer** S3 permission model:

| Layer | Action | Scope | Purpose |
|-------|--------|-------|---------|
| **Allow** | `s3:GetObject`, `PutObject`, `DeleteObject`, `ListBucket`, `CreateBucket`, `DeleteBucket` | `${env}-optinist-*` only | CRUD operations scoped to this environment's buckets only |
| **Deny** | Same CRUD actions | `NotResource: ${env}-optinist-*` | **Explicit deny** on all buckets outside this environment. Even if another policy grants access, this deny overrides it. |

`s3:ListAllMyBuckets` is intentionally **not granted**. This means the dev IAM user cannot see production bucket names at all — not even as metadata. The AWS Console S3 page will show an empty list, and `aws s3 ls` will return nothing. Users must access their environment's buckets by direct name (e.g., `aws s3 ls s3://development-optinist-app-storage/`).

**Result for the development IAM user:**

| Operation | `development-optinist-app-storage` | `subscr-optinist-app-storage` |
|-----------|-----------------------------------|-------------------------------|
| List all bucket names (`aws s3 ls`) | **Not visible** | **Not visible** |
| List bucket contents (`aws s3 ls s3://...`) | Allowed | **Denied** |
| Read objects (`s3:GetObject`) | Allowed | **Denied** |
| Write objects (`s3:PutObject`) | Allowed | **Denied** |
| Delete objects (`s3:DeleteObject`) | Allowed | **Denied** |
| Delete bucket (`s3:DeleteBucket`) | Allowed | **Denied** |

The explicit deny (`DenyS3CrossEnvironment`) ensures that even if a second policy is accidentally attached to the dev IAM user, cross-environment S3 access remains blocked. In AWS IAM, an explicit deny always overrides any allow.

---

## Verifying S3 Isolation

After deploying the development environment, run these tests using the **development IAM user credentials**:

```bash
# Configure AWS CLI with development IAM user credentials
export AWS_ACCESS_KEY_ID=<dev-access-key>
export AWS_SECRET_ACCESS_KEY=<dev-secret-key>
export AWS_DEFAULT_REGION=ap-northeast-1

# --- Tests that SHOULD succeed ---

# 1. List development bucket contents (by direct name)
aws s3 ls s3://development-optinist-app-storage/
# Expected: Success — shows objects in dev bucket

# 2. Upload to development bucket
echo "test" > /tmp/test-s3-isolation.txt
aws s3 cp /tmp/test-s3-isolation.txt s3://development-optinist-app-storage/test-s3-isolation.txt
# Expected: Success — upload completed

# 3. Download from development bucket
aws s3 cp s3://development-optinist-app-storage/test-s3-isolation.txt /tmp/test-s3-download.txt
# Expected: Success — download completed

# 4. Delete from development bucket
aws s3 rm s3://development-optinist-app-storage/test-s3-isolation.txt
# Expected: Success — delete completed

# --- Tests that SHOULD fail (Access Denied / empty) ---

# 5. List all bucket names (not granted)
aws s3 ls
# Expected: Empty output (s3:ListAllMyBuckets not granted)

# 6. List production bucket contents
aws s3 ls s3://subscr-optinist-app-storage/
# Expected: Access Denied

# 7. Read from production bucket
aws s3 cp s3://subscr-optinist-app-storage/test.txt /tmp/test.txt
# Expected: Access Denied

# 8. Write to production bucket
aws s3 cp /tmp/test-s3-isolation.txt s3://subscr-optinist-app-storage/test-s3-isolation.txt
# Expected: Access Denied

# 9. Delete from production bucket
aws s3 rm s3://subscr-optinist-app-storage/test-s3-isolation.txt
# Expected: Access Denied

# Clean up
rm -f /tmp/test-s3-isolation.txt /tmp/test-s3-download.txt
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
```

---

## Identifying Resources in AWS Console

All resources are tagged automatically via `default_tags`:

| Tag           | Value                     | Purpose                                                     |
| ------------- | ------------------------- | ----------------------------------------------------------- |
| `Environment` | `subscr` or `development` | Identify which environment owns the resource                |
| `ManagedBy`   | `terraform`               | Distinguish Terraform-managed vs manually-created resources |
| `Project`     | `optinist-cloud`          | Filter across all OptiNiSt resources                        |

**Filter in AWS Console**: Use `Environment = development` to see only dev resources.

**Filter via CLI**:

```bash
# List only development EC2 instances
aws ec2 describe-instances \
  --filters "Name=tag:Environment,Values=development" \
  --query "Reservations[].Instances[].[InstanceId,Tags[?Key=='Name'].Value|[0]]" \
  --output table

# List only production S3 buckets by prefix
aws s3 ls | grep subscr-optinist
```

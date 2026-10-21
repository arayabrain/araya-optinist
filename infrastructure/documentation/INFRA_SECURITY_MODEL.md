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

Terraform creates no IAM user for the application. S3 access is granted only to each environment's own roles, and every grant names that environment's buckets:

| Principal | Actions | Scope |
|-----------|---------|-------|
| ECS task role (`${env}-optinist-cloud-task-role`) | `s3:GetObject`, `PutObject`, `DeleteObject`, `ListBucket`, `GetBucketLocation`, `CreateBucket`, `DeleteBucket` | `${env}-optinist-app-storage` and the per-user buckets `${s3_user_bucket_prefix}-*` |
| ECS instance role, premium manager Lambda role | `s3:GetObject`, `PutObject`, `DeleteObject`, `ListBucket` | `${env}-optinist-app-storage` |
| Image Builder role | `s3:PutObject` | `image-builder-logs/` in `${env}-optinist-app-storage` |

`s3_user_bucket_prefix` differs per environment (`development-optinist-user`, `optinist-user`), and neither wildcard matches the other environment's buckets. `s3:ListAllMyBuckets` is not granted to any of these roles.

There is no explicit deny: isolation rests on these scoped grants being the only S3 permissions the roles hold. This model covers the application roles; operator access is governed separately.

**Result for the development ECS task role:**

| Operation | `development-optinist-app-storage` | `subscr-optinist-app-storage` |
|-----------|-----------------------------------|-------------------------------|
| List all bucket names (`s3:ListAllMyBuckets`) | **Denied** | **Denied** |
| List bucket contents (`s3:ListBucket`) | Allowed | **Denied** |
| Read objects (`s3:GetObject`) | Allowed | **Denied** |
| Write objects (`s3:PutObject`) | Allowed | **Denied** |
| Delete objects (`s3:DeleteObject`) | Allowed | **Denied** |

---

## Verifying S3 Isolation

The IAM policy simulator evaluates a role's policies without assuming the role. After deploying the development environment, run these checks with your operator credentials. Pass one resource per call, so that each row of the table is that resource's decision.

```bash
ROLE_ARN=$(aws iam get-role --role-name development-optinist-cloud-task-role \
  --query 'Role.Arn' --output text)

# --- Expected: allowed ---

# 1. Objects in the development app storage bucket
aws iam simulate-principal-policy --policy-source-arn "$ROLE_ARN" \
  --action-names s3:GetObject s3:PutObject s3:DeleteObject \
  --resource-arns arn:aws:s3:::development-optinist-app-storage/test.txt \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table

# 2. A development per-user bucket
aws iam simulate-principal-policy --policy-source-arn "$ROLE_ARN" \
  --action-names s3:ListBucket s3:DeleteBucket \
  --resource-arns arn:aws:s3:::development-optinist-user-test \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table

# --- Expected: implicitDeny ---

# 3. Objects in the production app storage bucket
aws iam simulate-principal-policy --policy-source-arn "$ROLE_ARN" \
  --action-names s3:GetObject s3:PutObject s3:DeleteObject \
  --resource-arns arn:aws:s3:::subscr-optinist-app-storage/test.txt \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table

# 4. A production per-user bucket
aws iam simulate-principal-policy --policy-source-arn "$ROLE_ARN" \
  --action-names s3:ListBucket s3:DeleteBucket \
  --resource-arns arn:aws:s3:::optinist-user-test \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table

# 5. List all bucket names
aws iam simulate-principal-policy --policy-source-arn "$ROLE_ARN" \
  --action-names s3:ListAllMyBuckets \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table
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

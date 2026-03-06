# Terraform Development Environment: Analysis Report

## Overview

The document analyzes three approaches for managing production and development Terraform environments:

| | **Option A: Parameterized** | **Option B: Workspaces** | **Option C: Shared Module** |
|---|---|---|---|
| State isolation | Separate S3 buckets | Same bucket, different keys | Separate S3 buckets |
| Code duplication | None | None | Low (thin wrappers) |
| Complexity | Low-Medium | Low | Medium-High |
| Cross-env safety | Strong | Weak | Strong |

---

## Question 1: `default_tags` for Distinguishing Production vs Dev

**Yes, absolutely recommended.** Adding `default_tags` to the AWS provider automatically tags every resource that supports tagging, making it trivial to distinguish environments in the AWS Console, Cost Explorer, and CLI.

### Implementation

Add to `main.tf` provider block:

```hcl
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Environment = var.environment
      ManagedBy   = "terraform"
      Project     = "optinist-cloud"
    }
  }
}
```

Then in each `.tfvars`:

```hcl
# production.tfvars
environment = "subscr"

# development.tfvars
environment = "development"
```

### Benefits

- **AWS Console**: Filter any resource list by `Environment = subscr` or `Environment = development`
- **Cost Explorer**: Group costs by the `Environment` tag to see per-environment spend
- **CLI**: `aws ec2 describe-instances --filters "Name=tag:Environment,Values=development"`
- **Billing alerts**: Set up separate CloudWatch billing alarms per environment tag

### Gotchas

- If you also set `Environment` in individual resource `tags {}` blocks, it will conflict with `default_tags`. Use one or the other per key, not both.
- Auto Scaling Groups don't propagate `default_tags` to launched EC2 instances automatically — you need `tag` blocks on the ASG with `propagate_at_launch = true` for that.

---

## Question 2: Cross-Environment Safety (Apply/Destroy Isolation)

### Option A (Parameterized — current implementation): Safe

Each environment uses a **separate backend** (`-backend-config=backends/production.hcl` vs `backends/development.hcl`). The state files live in completely different S3 buckets. There is **no mechanism** by which `terraform destroy -var-file=environments/development.tfvars` could touch production resources because:

1. `terraform init -backend-config=backends/development.hcl` points state to `development-optinist-for-cloud-tfstate` bucket
2. Terraform only knows about resources tracked in that state file
3. Production resources exist in a different state file in a different bucket

**Worst-case mistake**: Running `terraform destroy` without `-var-file`. This would prompt for required variables and fail, or use defaults that don't match any real environment.

### Option B (Workspaces): Dangerous

#### What Are Terraform Workspaces?

Terraform workspaces let you maintain **multiple state files** from the same configuration directory. By default, every Terraform project starts with a single workspace called `default`.

```bash
# Create a new workspace
terraform workspace new development

# List workspaces (* = current)
terraform workspace list
  default
* development

# Switch workspace
terraform workspace select default
```

**What a workspace actually does**: It changes the **state file path** within the same backend. With an S3 backend, the state files look like:

```
s3://my-terraform-state/
├── terraform.tfstate                    # "default" workspace
└── env:/
    ├── development/terraform.tfstate    # "development" workspace
    └── production/terraform.tfstate     # "production" workspace
```

All workspaces share the **same S3 bucket**, the **same `.tf` files**, and the **same backend config**. The only thing that differs is a subfolder prefix in the state key.

You can reference the current workspace in HCL:

```hcl
resource "aws_instance" "example" {
  tags = {
    Name = "${terraform.workspace}-my-server"
  }
}
```

#### The Core Danger: Workspace and `-var-file` Are Independent

This is where "Possible (wrong workspace)" is a real risk. Terraform has **two independent selectors** that must match, but it **never checks** that they do:

| Selector | Controls | Set by |
|---|---|---|
| **Workspace** | Which state file to read/write | `terraform workspace select` |
| **`-var-file`** | What variable values to use | `-var-file=` flag on each command |

Here's the exact failure scenario, step by step:

```bash
# Step 1: Developer initializes and selects production workspace
terraform workspace select production
terraform apply -var-file=environments/production.tfvars
# State file: env:/production/terraform.tfstate
# Variables: environment = "subscr", vpc_cidr = "10.1.0.0/16"
# Result: Production resources created and tracked in production state

# Step 2: Developer switches to dev, does dev work
terraform workspace select development
terraform apply -var-file=environments/development.tfvars
# State file: env:/development/terraform.tfstate
# Variables: environment = "development", vpc_cidr = "10.2.0.0/16"
# Result: Dev resources created and tracked in dev state

# Step 3: Days later, developer wants to tear down dev
# They FORGOT to check which workspace is active
# Current workspace is still: production (switched in another terminal, or after reboot)

terraform destroy -var-file=environments/development.tfvars
# ^^^ This DESTROYS PRODUCTION resources!
```

**What happens at Step 3:**

1. Terraform reads the **production** state file (because workspace = production)
2. The state file contains all production resources: VPC, RDS, ECS, ALB, etc.
3. Terraform sees the `-var-file` says `environment = "development"`, `vpc_cidr = "10.2.0.0/16"`
4. Terraform compares: "State has `subscr-optinist-app-storage`, but config says it should be `development-optinist-app-storage`"
5. **Terraform plans to destroy every production resource** and optionally create dev-named ones
6. If the developer types `yes` at the prompt (which says "Plan: 0 to add, 0 to change, 87 to destroy"), **production is gone**

The destroy prompt shows resource names, but in a long list of 80+ resources, a tired developer may not notice they're all `subscr-*` instead of `development-*`.

#### Danger: Workspace State Is Invisible

There's no visual indicator in your terminal of which workspace is active. Unlike git branches (which shell prompts often display), the workspace is a hidden piece of state stored in the `.terraform/` directory:

```bash
# This is the ONLY way to know your current workspace
cat .terraform/environment
# outputs: production

# There's no workspace info in your shell prompt by default
# Your terminal looks exactly the same whether workspace is "production" or "development"
```

You can add workspace to your shell prompt, but that's an extra setup step each developer must do.

#### Danger: `terraform init` Doesn't Reset Workspace

```bash
# Developer re-initializes (e.g., after pulling new code)
terraform init

# This does NOT change the workspace!
# If workspace was "production" before init, it's still "production" after
# Developer might assume init "reset" everything to a clean state
```

#### Danger: Same Bucket = Shared Blast Radius

With workspaces, all state files live in the **same S3 bucket**. This means:

- A single S3 bucket policy mistake can expose or corrupt all environments' state
- S3 bucket deletion destroys state for **all** environments
- No way to set different access controls per environment (e.g., "junior devs can only access dev state")

With Option A (separate backends), production state is in `subscr-optinist-for-cloud-tfstate` and dev state is in `development-optinist-for-cloud-tfstate`. You can:

- Restrict IAM access to the production bucket
- Set MFA-delete on the production bucket only
- Use different encryption keys per environment

#### Danger: No CI/CD Automation Safety

In CI/CD pipelines, you must add `terraform workspace select` before every command:

```bash
# CI/CD script for dev deployment
terraform workspace select development   # What if this fails silently?
terraform apply -var-file=environments/development.tfvars -auto-approve
```

If `workspace select` fails (workspace doesn't exist, state locked, etc.) and the script doesn't check the exit code, the next command runs against whatever workspace was previously active.

#### Possible Workaround (Brittle)

You could add a validation check in HCL:

```hcl
variable "expected_workspace" {}
locals {
  workspace_check = terraform.workspace == var.expected_workspace ? true : tobool("Workspace mismatch!")
}
```

But this is a brittle workaround that developers must remember to maintain.

#### When Workspaces ARE Appropriate

Workspaces aren't inherently bad. They're designed for:

- **Short-lived feature branches**: Testing a PR's infrastructure changes in isolation
- **Identical environments**: When dev and prod are truly identical (same config, same variables), just separate state
- **Single-developer projects**: Where the "wrong workspace" risk is lower

They're **not appropriate** when:

- Environments have different configurations (different VPC CIDRs, domain settings, instance counts) — which is this project's case
- Multiple developers manage infrastructure
- Production safety is critical

#### How Option A Prevents All of This

```bash
# Option A: Developer wants to destroy dev
terraform init -backend-config=backends/development.hcl
terraform destroy -var-file=environments/development.tfvars
```

**Why this is safe**: `terraform init` physically connects Terraform to the `development-optinist-for-cloud-tfstate` S3 bucket. The production state file in `subscr-optinist-for-cloud-tfstate` is **unreachable**. Terraform literally cannot see production resources. Even if the developer passes the wrong `-var-file`, the worst that happens is a plan error (variable values don't match state), not production destruction.

To switch to production, you must **explicitly re-init**:

```bash
terraform init -backend-config=backends/production.hcl -reconfigure
```

The `-reconfigure` flag makes it an intentional, obvious action. There's no way to "forget" which backend you're pointing to — the init step forces the choice.

### Option C (Shared Module): Safe

Like Option A, each environment is a separate Terraform root module with its own backend configuration. `cd environments/production && terraform destroy` only affects production state. `cd environments/development && terraform destroy` only affects development state. The directory structure enforces isolation.

### Safety Summary

| | Accidental cross-env destroy possible? | Protection mechanism |
|---|---|---|
| **Option A** | No | Separate backends (different S3 buckets) |
| **Option B** | **Yes** | None built-in; relies on human workspace selection |
| **Option C** | No | Separate directories with separate backends |

---

## Question 3: Developer Experience (AWS GUI & CLI)

### Option A (Parameterized) — AWS Console

**Resource naming**: All resources have environment prefix baked into the name:

- Production: `subscr-optinist-app-storage`, `subscr-optinist-cloud-ecs-cluster`
- Development: `development-optinist-app-storage`, `development-optinist-cloud-ecs-cluster`

**GUI experience**: In any AWS service page (EC2, ECS, S3, etc.), both environments' resources appear in the same list. You distinguish them by name prefix. With `default_tags`, you can also filter by `Environment` tag.

**CLI for tests**:

```bash
# Target dev ECS cluster
aws ecs list-services --cluster development-optinist-cloud-ecs-cluster

# Target dev ALB
aws elbv2 describe-target-health --target-group-arn <dev-tg-arn>

# Invoke dev Lambda
aws lambda invoke --function-name development-free-manager output.json
```

### Option B (Workspaces) — AWS Console

**Resource naming**: Identical to Option A if you parameterize names. The key difference is operational — you manage state via workspace commands rather than backend configs.

**GUI experience**: Same as Option A — resources coexist in the same AWS account with different name prefixes.

**CLI for tests**: Same as Option A.

**Terraform operations** (the difference):

```bash
# Switch to dev
terraform workspace select development
terraform plan -var-file=environments/development.tfvars

# Switch to prod (MUST remember this)
terraform workspace select production
terraform plan -var-file=environments/production.tfvars
```

### Option C (Shared Module) — AWS Console

**Resource naming**: Same as A and B — prefix-based distinction.

**GUI experience**: Identical to Option A.

**CLI for tests**: Identical to Option A.

**Terraform operations** (the difference):

```bash
# Dev operations
cd infrastructure/terraform/environments/development
terraform init
terraform plan

# Prod operations
cd infrastructure/terraform/environments/production
terraform init
terraform plan
```

### Key Insight

From the **AWS Console and CLI perspective, all three options look identical.** The difference is purely in the **Terraform workflow** — how developers switch between environments and how state is isolated.

---

## Recommendation

**Option A (current implementation) is the best choice** for this project because:

1. **Safety**: Separate backends make cross-environment destruction impossible
2. **Simplicity**: Single set of `.tf` files, no module wrappers needed
3. **Familiar workflow**: Standard `-var-file` and `-backend-config` flags
4. **Add `default_tags`**: Easy to implement and gives immediate visibility in AWS Console and Cost Explorer

The only addition recommended is the `default_tags` block in the provider, which takes about 5 lines of code and works with all three options.

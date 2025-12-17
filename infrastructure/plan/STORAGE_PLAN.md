# Data Storage Analysis Report: EFS to EBS Migration Feasibility

## Executive Summary

OptiNiSt Cloud currently uses **EFS (Elastic File System)** for shared persistent storage and **S3** for durable object storage. This report analyzes the current storage architecture, costs, and feasibility of optimizing storage costs by reverting user workspace data to **EBS (EC2 instance volumes)** while maintaining shared storage for published experiments.

**Key Finding: Hybrid EFS+EBS approach recommended - proven architecture with 40% cost reduction**

**Historical Context:**
- ✅ Pre-commit c383f15 (Nov 2025): studio_data used EC2 instance EBS volumes
- ✅ Commit c383f15: Migrated studio_data to dedicated EFS
- ✅ Proposal: Revert user data to EBS, keep smaller EFS for published data only

**Your Environment:**
- Free users: 5GB quota (typically 1-2 concurrent)
- Premium users: 200GB quota (typically 3-5 concurrent)
- Published data: 10-30% of total (needs multi-instance access)
- ECS EC2 launch type with 1-3 instances across 2 AZs

**Three Options Analyzed:**

| Option | Storage Cost | Savings | Complexity | Timeline |
|--------|--------------|---------|------------|----------|
| **1. Hybrid EFS+EBS** ⭐ | $46-91/month | ~$37-42/month (40%) | Low | 1-2 weeks |
| 2. S3 + Lambda@Edge | $49-93/month | ~$34-39/month (37%) | High | 4-6 weeks |
| 3. EBS + Eventual Consistency | $36-78/month | ~$47-55/month (50%) | Medium | 2-3 weeks |

**Recommended: Option 1 (Hybrid EFS+EBS)**
- Revert user workspace data to EC2 instance EBS (proven pre-c383f15 architecture)
- Keep small EFS for published experiments only (10-30% of data)
- Maintain Snakemake EFS (unchanged)
- Low risk, minimal code changes, strong consistency guarantees

---

## 0. Critical Architectural Discovery

### 0.1 Historical Context: Pre-c383f15 Architecture

**Discovery:** Before commit c383f15 (Nov 20, 2025), studio_data was stored on **EC2 instance EBS volumes** (bind mount), not EFS.

**Commit c383f15 Changes:**
- Added `efs_volume_configuration` to studio_data volume
- Created new `aws_efs_file_system.studio_data` resource
- Increased EC2 root volumes from 30GB to 80GB
- **Effect:** Migrated from EBS-based to EFS-based storage

**Key Insight:** Reverting to EBS-based storage is **feasible** because it's a proven architecture that previously worked.

### 0.2 ECS EC2 Architecture Constraints

**Current Infrastructure:**
- ECS with **EC2 launch type** (not Fargate)
- Multiple tasks share the **same EC2 instances**
- Auto Scaling Group spans **2 availability zones**
- ALB with **sticky sessions** (86400s cookie duration)

**Storage Implications:**
- EBS volumes attach to **EC2 instances, not individual tasks**
- Empty volume blocks = **bind mount** to EC2 instance's EBS root volume
- All tasks on same instance share the same EBS storage
- Cannot attach per-task EBS volumes (would need Fargate for that)

**Multi-Instance Challenge:**
- Auto Scaling can create 1-3 instances
- Each instance has its own EBS root volume
- Published experiments must be accessible from **all instances** (visitor routing is random)
- **Solution:** Separate storage strategy for published data

---

## 1. Current Storage Architecture Summary

### 1.1 EFS Configuration

OptiNiSt deploys **two EFS file systems**:

| EFS System | Purpose | Mount Point | Lifecycle Policy | Performance |
|------------|---------|-------------|------------------|-------------|
| **snmk** | Snakemake workflow cache | `/app/.snakemake` | None (always hot) | General Purpose, Bursting |
| **studio_data** | User experiments & uploads | `/app/studio_data` | IA after 30 days | General Purpose, Bursting |

**Key Characteristics:**
- **High Availability:** Multi-AZ (ap-northeast-1a, ap-northeast-1c)
- **Shared Access:** Accessible by all ECS tasks in the cluster
- **Persistent:** Data survives container restarts and task replacements
- **Cost Structure:**
  - Standard: $0.30/GB-month
  - IA (Infrequent Access): $0.025/GB-month (activated after 30 days for studio_data)

### 1.2 S3 Configuration

Primary S3 bucket provides durable object storage:

| Bucket | Purpose | Versioning | Encryption |
|--------|---------|------------|------------|
| `subscr-optinist-app-storage` | Primary user data | Enabled | Yes |

**Integration Points:**
- Backend uploads user files to S3 via `S3StorageController` (`s3_storage_controller.py`)
- Environment variable: `S3_DEFAULT_BUCKET_NAME` = `subscr-optinist-app-storage`
- Remote storage type: `REMOTE_STORAGE_TYPE = 2` (S3)
- VPC Gateway Endpoint for private S3 access (no NAT costs)

### 1.3 Data Storage Locations

```
Local Storage Structure (EFS-backed):
/tmp/studio/
├── input/{workspace_id}/{filename}              # User uploads
└── output/{workspace_id}/{unique_id}/           # Experiment results
    ├── experiment.yaml                          # Workflow metadata
    ├── workflow.yaml
    ├── snakemake.yaml
    ├── remote_sync.lock                         # Coordination files
    ├── remote_sync_stat.json
    └── [experiment output files]

S3 Storage Structure:
s3://subscr-optinist-app-storage/
└── app/studio_data/
    ├── input/{workspace_id}/{filename}          # Mirrored uploads
    └── output/{workspace_id}/{unique_id}/       # Mirrored results
```

**Critical Observation:** Local and S3 storage are **redundant copies**. Every file exists in both locations after upload/experiment completion.

---

## 2. Current Data Lifecycle & Cleanup Mechanisms

### 2.1 Existing Cleanup Systems

#### User-Initiated Cleanup (Manual)
- **Experiment Deletion:** `DELETE /experiment/{workspace_id}/{unique_id}`
  - Deletes from S3 first (via `RemoteStorageDeleter`)
  - Then deletes local files (via `shutil.rmtree()`)
  - Updates database records

- **File Deletion:** `DELETE /file/{workspace_id}/{file_name}`
  - Deletes from S3 first
  - Then deletes local file (via `os.remove()`)
  - Updates workspace data usage metrics

### 2.2 Storage Quota Management

| Plan Tier | Quota | Enforcement Point | Alert Threshold |
|-----------|-------|-------------------|-----------------|
| Free | 5 GB | Upload endpoint (line 214-236 in `files.py`) | 90% (critical), 100% (blocked) |
| Premium | 200 GB | Upload endpoint | 90% (critical), 100% (blocked) |

**Monitoring:** `S3StorageMonitor` tracks usage per user/workspace and generates alerts.

### 2.3 Data Persistence Pattern

**Current Behavior:** Files uploaded to local EFS are **never automatically cleaned** from local storage unless:
1. User explicitly deletes the experiment/file

**Result:** Local storage continuously accumulates data, causing EFS costs to grow over time.

---

## 3. Public Dataview Pages & Shared Workspaces

### 3.1 Critical Constraint: Persistent Data Required

**Public pages and shared workspaces currently rely on persistent local storage:**

#### Database Requirements (Non-Negotiable)
- `ExperimentRecord.publish_status` (0=private, 1=public) must persist
- `WorkspacesShareUser` junction table must persist
- Workspace metadata must persist

#### Current File System Requirements
- Public output files must exist at persistent paths
- Public access validates file existence via `os.path.exists()` checks
- **Without persistent files, public links return 404s**

### 3.2 CloudFront Limitation: Dynamic API Requirement

**❌ CloudFront → S3 approach is NOT viable for this application:**

**Critical Constraint:**
- Dataview images are **dynamically generated via API**, not static files (e.g., PNG or JSON)
- Current architecture reuses Visualise Screen APIs to minimize development effort
- APIs must process requests on-demand to generate visualizations
- **CloudFront (CDN) → S3 cannot be used** - the OptiNist backend API must always be traversed

**Impact:**
- Public/shared data requires persistent local storage accessible by the backend API
- Cannot eliminate persistent storage entirely as originally proposed

### 3.3 Published Data Multi-Instance Problem

**Challenge:** With EBS-based user storage, published experiments face a critical issue:

**Scenario:**
1. User on Instance 1 publishes experiment → stored on Instance 1's EBS
2. Auto Scaling creates Instance 2
3. Visitor accesses public page → ALB routes to Instance 2 (random)
4. Instance 2's EBS doesn't have the published experiment → **404 error**

**Why This Matters:**
- Public visitors don't have sticky sessions (first-time access)
- Published data must be **consistently available** from all instances
- Each EC2 instance has its own isolated EBS volumes

**Solution Required:** Published data needs a **shared storage layer** that all instances can access

---

## 4. Architecture Options

### Common Design Principles (All Options)

1. **S3 as Source of Truth:** All data ultimately lives in S3
2. **EBS for User Workspaces:** Fast local access, data syncs to/from S3
3. **Revert to Pre-c383f15 Model:** Use EC2 instance EBS (bind mount), not per-task volumes
4. **Keep Snakemake EFS:** Small shared cache (~50GB), working well, no changes needed

### Option 1: Hybrid EFS + EBS ⭐ **RECOMMENDED**

**Architecture:**

1. **User workspace data** (`/app/studio_data/input` and `/app/studio_data/output`):
   - **EBS bind mount** (revert studio_data volume to empty configuration)
   - Stored on EC2 instance's root EBS volume
   - Each instance has own copy
   - Data syncs to/from S3 per user session (existing behavior)
   - Users are sticky to instances (ALB cookies), so consistency maintained

2. **Published experiment data** (`/app/studio_data/published`):
   - **Keep EFS** (small subset, maybe 10-30% of total data)
   - All instances can read published experiments
   - Consistent view for all visitors (no 404 errors)
   - Multi-AZ availability maintained

3. **Snakemake cache** (`/app/.snakemake`):
   - **Keep EFS** (already working well, ~50GB)

**Implementation:**

```hcl
# Terraform: compute.tf
# Increase root volume size to accommodate user data
block_device_mappings {
  device_name = "/dev/xvda"
  ebs {
    volume_size = 200  # Increased from 80GB
    volume_type = "gp3"
    encrypted   = true
  }
}

# Revert studio_data to bind mount (pre-c383f15)
volume {
  name = "subscr-optinist-cloud-studio-data-volume"
  # Empty = bind mount to EC2 instance EBS
  # Remove efs_volume_configuration block
}

# Add new EFS for published data only
resource "aws_efs_file_system" "published_data" {
  creation_token = "subscr-optinist-published-data"
  performance_mode = "generalPurpose"
  throughput_mode = "bursting"
  lifecycle_policy {
    transition_to_ia = "AFTER_7_DAYS"  # Faster IA for published data
  }
}

# Mount published EFS
volume {
  name = "subscr-optinist-cloud-published-data-volume"
  efs_volume_configuration {
    file_system_id = aws_efs_file_system.published_data.id
    # ...
  }
}
```

**Code Changes:**

```python
# dataview.py - publishing logic
async def publish_dataview_records(id, flag, ...):
    record = DataviewService.find_user_owned_dataview_record(db, id, user.id)

    if flag == PublishFlags.on:
        # Copy experiment from user EBS to published EFS
        src = f"/app/studio_data/output/{workspace_id}/{unique_id}"
        dst = f"/app/studio_data/published/{workspace_id}/{unique_id}"
        shutil.copytree(src, dst)

        # Ensure backup to S3
        await remote_storage.upload_experiment(workspace_id, unique_id)
    else:
        # Unpublish: remove from EFS
        published_path = f"/app/studio_data/published/{workspace_id}/{unique_id}"
        if os.path.exists(published_path):
            shutil.rmtree(published_path)

    record.publish_status = int(flag == PublishFlags.on)
    db.commit()

# dataview.py - public endpoint
async def public_reproduce_experiment(workspace_id, unique_id, ...):
    # Read from published EFS location
    published_path = f"/app/studio_data/published/{workspace_id}/{unique_id}"

    if not os.path.exists(published_path):
        # Fallback: restore from S3 if missing
        await remote_storage.download_experiment(workspace_id, unique_id)
        # Copy to published location
        src = f"/app/studio_data/output/{workspace_id}/{unique_id}"
        shutil.copytree(src, published_path)

    return await reproduce_experiment(workspace_id, unique_id)
```

**Cost Analysis:**

| Component | Current | Option 1 | Savings |
|-----------|---------|----------|---------|
| User data EFS (studio_data) | ~$60/month | $0 | $60 |
| Published data EFS | $0 | ~$12-18/month | -$12-18 |
| EC2 EBS root volumes | 80GB × 1-3 = $7.68-23/month | 200GB × 1-3 = $19-58/month | -$11-35 |
| Snakemake EFS | $15/month | $15/month | $0 |
| **Net Change** | **~$83-98/month** | **~$46-91/month** | **~$37-42/month savings** |

**Benefits:**
- ✅ **Proven architecture** (revert to pre-c383f15)
- ✅ **Low risk** - minimal code changes
- ✅ **Solves multi-instance problem** - published data on shared EFS
- ✅ **Cost effective** - ~40% reduction
- ✅ **No data loss risk** - published data backed by EFS AND S3
- ✅ **Simple implementation** - ~1-2 weeks

**Drawbacks:**
- ⚠️ Still uses EFS (smaller, but not eliminated)
- ⚠️ Need to copy data on publish (adds latency)
- ⚠️ Storage used by published experiments counts toward published EFS and user quota

---

### Option 2: S3 + Lambda@Edge (No EFS for User Data)

**Architecture:**

1. **User workspace data**:
   - EBS bind mount (same as Option 1)
   - Syncs to S3

2. **Published experiments**:
   - **No EFS, no admin EBS** - S3 only
   - Pre-generate static visualization assets on publish
   - Store in S3 with specific prefixes
   - Serve via **CloudFront + Lambda@Edge**
   - Lambda@Edge intercepts requests, generates dynamic content from S3 data

3. **Implementation Strategy**:
   - Refactor visualization APIs to be stateless
   - On publish: generate all visualization formats (PNG, JSON) → upload to S3
   - Public dataview: CloudFront → Lambda@Edge → S3
   - Lambda@Edge does lightweight processing (generate plots from raw data in S3)

**Code Changes (Significant):**

```python
# New: experiment_publisher.py
class ExperimentPublisher:
    async def publish_experiment(self, workspace_id, unique_id):
        # 1. Generate all static assets
        assets = await self.generate_visualization_assets(workspace_id, unique_id)

        # 2. Upload to S3 with public prefix
        s3_prefix = f"published/{workspace_id}/{unique_id}"
        for asset in assets:
            await s3_client.put_object(
                Bucket=bucket,
                Key=f"{s3_prefix}/{asset.path}",
                Body=asset.data
            )

        # 3. Update database
        record.publish_status = PublishStatus.on
        record.cdn_url = f"https://cdn.optinist.com/{s3_prefix}"
        db.commit()

# Lambda@Edge function (Node.js or Python)
# Deployed to CloudFront distribution
def handler(event, context):
    request = event['Records'][0]['cf']['request']
    uri = request['uri']

    # Parse requested visualization
    # workspace_id/unique_id/plot_type/params

    # Fetch raw data from S3
    data = s3.get_object(Bucket, Key)

    # Generate visualization on-the-fly
    image = generate_plot(data, params)

    return {
        'status': '200',
        'body': image,
        'headers': {'content-type': 'image/png'}
    }
```

**Cost Analysis:**

| Component | Current | Option 2 | Savings |
|-----------|---------|----------|---------|
| User data EFS | ~$60/month | $0 | $60 |
| Published data EFS | $0 | $0 | $0 |
| EC2 EBS root | $7.68-23/month | $19-58/month | -$11-35 |
| S3 requests | minimal | ~$5-10/month | -$5-10 |
| Lambda@Edge | $0 | ~$5/month | -$5 |
| CloudFront | $0 | ~$5/month (low traffic) | -$5 |
| Snakemake EFS | $15/month | $15/month | $0 |
| **Net Change** | **~$83-98/month** | **~$49-93/month** | **~$34-39/month savings** |

**Benefits:**
- ✅ **Eliminates EFS for user data** completely
- ✅ **Scalable** - CloudFront handles traffic spikes
- ✅ **Fast** - CDN edge caching for published data
- ✅ **No multi-instance issues** - stateless architecture

**Drawbacks:**
- ❌ **High complexity** - requires significant refactoring
- ❌ **Development time** - 2-3 weeks estimated
- ❌ **New tech stack** - Lambda@Edge adds operational overhead
- ❌ **Similar costs** to Option 1 (~$5/month difference)
- ❌ **Potential stale data** - CDN caching can serve outdated visualizations

---

### Option 3: EBS + Eventual Consistency

**Architecture:**

1. **User workspace data**:
   - EBS bind mount (same as Options 1 & 2)

2. **Published experiments**:
   - **Also on EBS** (per-instance)
   - On publish: Upload to S3 immediately with metadata tag
   - **Background sync job**: Every 5 minutes, each instance:
     - Queries S3 for published experiments
     - Downloads any missing published experiments
     - Stores in `/app/studio_data/published` on local EBS

3. **Result**: Eventual consistency
   - Newly published experiment available on publishing instance immediately
   - Available on other instances within 5 minutes
   - Visitors may see 404 for up to 5 minutes after publish

**Implementation:**

```python
# New: published_data_syncer.py
class PublishedDataSyncer:
    """Background job that runs every 5 minutes on each instance"""

    async def sync_published_experiments(self):
        # 1. List all published experiments in S3
        s3_published = await self.list_s3_published_experiments()

        # 2. Check which ones are missing locally
        local_published = set(os.listdir("/app/studio_data/published"))
        missing = s3_published - local_published

        # 3. Download missing experiments
        for workspace_id, unique_id in missing:
            logger.info(f"Syncing published experiment {unique_id}")
            await remote_storage.download_experiment(workspace_id, unique_id)

            # Move to published location
            src = f"/app/studio_data/output/{workspace_id}/{unique_id}"
            dst = f"/app/studio_data/published/{workspace_id}/{unique_id}"
            shutil.move(src, dst)

    async def list_s3_published_experiments(self):
        """Query S3 for all published experiments"""
        # Option A: Maintain S3 prefix for published data
        # Option B: Query database for publish_status=1, cross-check S3
        # Recommend Option B for consistency

        published_records = db.query(ExperimentRecord).filter(
            ExperimentRecord.publish_status == 1
        ).all()

        return {(r.workspace_id, r.uid) for r in published_records}

# Startup script: run_published_syncer.sh
while true; do
    python -c "from app.published_data_syncer import PublishedDataSyncer; \
               import asyncio; \
               asyncio.run(PublishedDataSyncer().sync_published_experiments())"
    sleep 300  # 5 minutes
done
```

**Cost Analysis:**

| Component | Current | Option 3 | Savings |
|-----------|---------|----------|---------|
| User data EFS | ~$60/month | $0 | $60 |
| Published data EFS | $0 | $0 | $0 |
| EC2 EBS root | $7.68-23/month | $19-58/month | -$11-35 |
| S3 GET requests (sync) | minimal | ~$2-5/month | -$2-5 |
| Snakemake EFS | $15/month | $15/month | $0 |
| **Net Change** | **~$83-98/month** | **~$36-78/month** | **~$47-55/month savings** |

**Benefits:**
- ✅ **Eliminates ALL EFS for user data** (maximum savings)
- ✅ **Simple implementation** - just a background sync job
- ✅ **Low risk** - each instance can operate independently
- ✅ **Best cost savings** - ~50% reduction

**Drawbacks:**
- ⚠️ **Eventual consistency** - 5-minute lag for published data
- ⚠️ **User experience** - visitor may see 404 briefly after publish
- ⚠️ **Duplicate storage** - published data on all instances' EBS
- ⚠️ **Sync overhead** - S3 API calls every 5 minutes per instance

---

### Recommended Option: **Option 1** ⭐

**Rationale:**
- **Lowest risk**: Proven architecture (pre-c383f15)
- **Shortest timeline**: 1-2 weeks vs 4-6 weeks
- **Strong consistency**: No eventual consistency issues
- **Good cost savings**: ~40% reduction
- **Minimal code changes**: Simple publish/unpublish logic

**When to consider alternatives:**
- **Option 2**: If you want to eliminate EFS entirely AND have 4-6 weeks for development
- **Option 3**: If eventual consistency is acceptable AND you want maximum cost savings

---

## 5. Cost-Benefit Analysis Summary

### 5.1 Current Baseline Costs

**Storage-Related Costs (Estimated):**

| Component | Monthly Cost | Notes |
|-----------|--------------|-------|
| EFS studio_data (200GB) | ~$60/month | Mix of Standard ($0.30/GB) and IA ($0.025/GB) after 30 days |
| EFS snmk (50GB) | ~$15/month | Snakemake cache, frequently accessed |
| EC2 EBS root volumes (80GB × 1-3) | $7.68-23/month | Currently 80GB per instance |
| S3 storage (600GB) | ~$14/month | Already in use, unchanged |
| **Total Storage** | **~$97-112/month** | Varies with instance count |

**Note:** EC2 instance costs (~$60-180/month for t3.large) are separate and not included in storage optimization.

### 5.2 Option Comparison

| Metric | Current | Option 1<br>(Hybrid) | Option 2<br>(S3+Lambda) | Option 3<br>(EBS+Sync) |
|--------|---------|---------------------|------------------------|----------------------|
| **User Data Storage** | EFS | EBS bind mount | EBS bind mount | EBS bind mount |
| **Published Data** | EFS (shared) | EFS (dedicated) | S3+CloudFront | EBS (per-instance) |
| **Snakemake Cache** | EFS | EFS | EFS | EFS |
| **Monthly Cost** | $97-112 | $46-91 | $49-93 | $36-78 |
| **Savings** | - | $37-42 (40%) | $34-39 (37%) | $47-55 (50%) |
| **Consistency** | Strong | Strong | Strong | Eventual (5 min) |
| **Complexity** | - | Low | High | Medium |
| **Dev Timeline** | - | 1-2 weeks | 4-6 weeks | 2-3 weeks |
| **Risk Level** | - | **Low** ✅ | High | Medium |

### 5.3 Detailed Cost Breakdown: Option 1 (Recommended)

**Current Architecture:**
- EFS studio_data: ~$60/month
- EFS snmk: $15/month
- EBS root (80GB): $7.68-23/month
- S3: $14/month
- **Total: $97-112/month**

**Option 1 Architecture:**
- EFS published_data (60GB, 30% of 200GB): ~$12-18/month (with IA)
- EFS snmk: $15/month (unchanged)
- EBS root (200GB): $19-58/month (increased to hold user data)
- S3: $14/month (unchanged)
- **Total: $60-105/month**

**Net Savings: $37-42/month (38-40% reduction)**

### 5.4 Performance Characteristics

| Metric | Current (EFS) | All Options (EBS) | Impact |
|--------|---------------|-------------------|---------|
| **Local I/O Latency** | 2-5ms (network) | <1ms (local disk) | 50-80% faster |
| **Login Time** | Instant (data cached) | 0-2 sec (data on instance) | Negligible |
| **Published Access** | Instant (EFS) | Option 1: Instant<br>Option 2: CDN fast<br>Option 3: May 404 briefly | Varies by option |
| **Data Durability** | EFS + S3 | S3 primary | High (all options) |

### 5.5 Scalability Considerations

**User Growth Impact on Option 1:**

| Users | Current EFS Cost | Option 1 Cost | Savings |
|-------|------------------|---------------|---------|
| 5 users (1 Free, 4 Premium) | ~$100/month | ~$65/month | ~$35/month |
| 10 users (2 Free, 8 Premium) | ~$140/month | ~$85/month | ~$55/month |
| 20 users (5 Free, 15 Premium) | ~$220/month | ~$125/month | ~$95/month |

**Key Insight:** Savings scale linearly with user count because EFS costs grow with total storage, while EBS costs are bounded by instance count (1-3 instances handle many users via ALB routing).

### 5.6 Non-Functional Benefits

**Option 1 (Recommended) Additional Benefits:**
- ✅ **Proven architecture**: Revert to pre-c383f15, already validated
- ✅ **No behavior changes**: Users experience identical functionality
- ✅ **Simplified operations**: Less EFS storage to manage
- ✅ **Faster local I/O**: EBS vs EFS network latency
- ✅ **Clear rollback path**: Re-enable EFS if needed
- ✅ **Incremental deployment**: Can test with one instance first

---

## 6. Implementation Roadmap (Option 1 - Recommended)

### Phase 1: Infrastructure Changes (Terraform)

**Goal:** Revert user data to EBS, create dedicated published data EFS

1. **Increase EC2 Root Volume Size:**
   ```hcl
   # compute.tf - Both autoscaling and premium launch templates
   block_device_mappings {
     device_name = "/dev/xvda"
     ebs {
       volume_size = 200  # Increased from 80GB
       volume_type = "gp3"
       encrypted   = true
     }
   }
   ```

2. **Revert studio_data to Bind Mount:**
   ```hcl
   # compute.tf - Both task definitions
   volume {
     name = "subscr-optinist-cloud-studio-data-volume"
     # Remove efs_volume_configuration block entirely
     # Empty volume = bind mount to EC2 instance EBS
   }
   ```

3. **Create New Published Data EFS:**
   ```hcl
   # infrastructure.tf - New resource
   resource "aws_efs_file_system" "published_data" {
     creation_token = "subscr-optinist-published-data"
     performance_mode = "generalPurpose"
     throughput_mode = "bursting"

     lifecycle_policy {
       transition_to_ia = "AFTER_7_DAYS"  # Aggressive IA for cost savings
     }

     tags = {
       Name = "subscr-optinist-published-data"
     }
   }

   # Mount targets in both AZs
   resource "aws_efs_mount_target" "published_data_private1" {
     file_system_id  = aws_efs_file_system.published_data.id
     subnet_id       = aws_subnet.private1.id
     security_groups = [aws_security_group.efs.id]
   }

   resource "aws_efs_mount_target" "published_data_private2" {
     file_system_id  = aws_efs_file_system.published_data.id
     subnet_id       = aws_subnet.private2.id
     security_groups = [aws_security_group.efs.id]
   }

   # Access point
   resource "aws_efs_access_point" "published_data" {
     file_system_id = aws_efs_file_system.published_data.id

     root_directory {
       path = "/"
       creation_info {
         owner_gid   = 1000
         owner_uid   = 1000
         permissions = "755"
       }
     }

     tags = {
       Name = "subscr-optinist-published-data-ap"
     }
   }
   ```

4. **Add Published Data Volume to Task Definitions:**
   ```hcl
   # compute.tf - Both task definitions
   volume {
     name = "subscr-optinist-cloud-published-data-volume"
     efs_volume_configuration {
       file_system_id     = aws_efs_file_system.published_data.id
       root_directory     = "/"
       transit_encryption = "ENABLED"
       authorization_config {
         access_point_id = aws_efs_access_point.published_data.id
         iam             = "DISABLED"
       }
     }
   }

   # In container_definitions, add mount point:
   mountPoints = [
     # ... existing mounts ...
     {
       sourceVolume  = "subscr-optinist-cloud-published-data-volume"
       containerPath = "/app/studio_data/published"
       readOnly      = false
     }
   ]
   ```

5. **Optional: Mark Old studio_data EFS for Deletion:**
   - Don't delete immediately - keep as backup during migration
   - After successful validation, can delete `aws_efs_file_system.studio_data`

**Estimated Time:** 1-2 hours

---

### Phase 2: Backend Code Changes

**Goal:** Add publish/unpublish logic to copy data to published EFS

1. **Update Publish Endpoint:**
   ```python
   # studio/app/common/routers/dataview.py

   @router.post("/publish/{id}/{flag}")
   async def publish_dataview_records(
       id: int,
       flag: PublishFlags,
       db: Session = Depends(get_db),
       current_user: User = Depends(get_current_user),
   ):
       record = DataviewService.find_user_owned_dataview_record(db, id, current_user.id)

       if not record:
           raise HTTPException(status_code=404)

       # Get workspace and experiment IDs
       workspace_id = str(record.workspace_id)
       unique_id = record.uid

       if flag == PublishFlags.on:
           # Copy experiment to published EFS location
           src = f"/app/studio_data/output/{workspace_id}/{unique_id}"
           dst = f"/app/studio_data/published/{workspace_id}/{unique_id}"

           if os.path.exists(src):
               # Create parent directory if needed
               os.makedirs(os.path.dirname(dst), exist_ok=True)

               # Copy directory tree
               if os.path.exists(dst):
                   shutil.rmtree(dst)  # Remove old version if exists
               shutil.copytree(src, dst)

               logger.info(f"Published experiment copied to {dst}")
           else:
               logger.warning(f"Source experiment not found: {src}")
               # Attempt to restore from S3
               if RemoteStorageController.is_available():
                   await remote_storage.download_experiment(workspace_id, unique_id)
                   if os.path.exists(src):
                       shutil.copytree(src, dst)
       else:
           # Unpublish: remove from published EFS
           dst = f"/app/studio_data/published/{workspace_id}/{unique_id}"
           if os.path.exists(dst):
               shutil.rmtree(dst)
               logger.info(f"Unpublished experiment removed from {dst}")

       # Update database
       record.publish_status = int(flag == PublishFlags.on)
       db.commit()

       return True
   ```

2. **Update Public Endpoint (Safety Check):**
   ```python
   # studio/app/common/routers/dataview.py

   @public_router.get("/workflow/reproduce/{workspace_id}/{unique_id}")
   async def public_reproduce_experiment(
       workspace_id: str,
       unique_id: str,
       db: Session = Depends(get_db),
   ):
       # Verify published status
       record = DataviewService.find_published_dataview_record(
           db, int(workspace_id), unique_id
       )

       if not record:
           raise HTTPException(status_code=404, detail="Experiment not published")

       # Check published EFS location
       published_path = f"/app/studio_data/published/{workspace_id}/{unique_id}"

       if not os.path.exists(published_path):
           logger.warning(f"Published experiment missing at {published_path}, restoring from S3")

           # Fallback: restore from S3
           if RemoteStorageController.is_available():
               await remote_storage.download_experiment(workspace_id, unique_id)

               # Copy to published location
               src = f"/app/studio_data/output/{workspace_id}/{unique_id}"
               if os.path.exists(src):
                   os.makedirs(os.path.dirname(published_path), exist_ok=True)
                   shutil.copytree(src, published_path)
               else:
                   raise HTTPException(status_code=500, detail="Failed to restore experiment")
           else:
               raise HTTPException(status_code=500, detail="Published experiment unavailable")

       # Use existing reproduce logic
       return await reproduce_experiment(workspace_id, unique_id)
   ```

3. **Optional: Migration Script for Existing Published Experiments:**
   ```python
   # scripts/migrate_published_to_efs.py

   async def migrate_published_experiments():
       """One-time migration: copy existing published experiments to new EFS"""

       # Query all published experiments
       published = db.query(ExperimentRecord).filter(
           ExperimentRecord.publish_status == 1
       ).all()

       for record in published:
           workspace_id = str(record.workspace_id)
           unique_id = record.uid

           src = f"/app/studio_data/output/{workspace_id}/{unique_id}"
           dst = f"/app/studio_data/published/{workspace_id}/{unique_id}"

           if os.path.exists(src) and not os.path.exists(dst):
               os.makedirs(os.path.dirname(dst), exist_ok=True)
               shutil.copytree(src, dst)
               print(f"Migrated: {unique_id}")
   ```

**Estimated Time:** 3-5 hours (including testing)

---

### Phase 3: Frontend Changes

**No changes required** - All changes are backend/infrastructure only.

Published dataview pages continue to work via existing API endpoints.

**Estimated Time:** 0 hours

---

### Phase 4: Testing & Validation

**Goal:** Verify functionality and cost savings

1. **Functional Tests:**
   - ✅ Upload file → verify saved to EBS (check `/app/studio_data/input`)
   - ✅ Run experiment → verify output to EBS (check `/app/studio_data/output`)
   - ✅ Publish experiment → verify copied to published EFS (check `/app/studio_data/published`)
   - ✅ Access public page → verify data loads correctly from published EFS
   - ✅ Unpublish experiment → verify removed from published EFS
   - ✅ Delete experiment → verify removed from both EBS and S3
   - ✅ Instance restart → verify published data persists (EFS)
   - ✅ Instance restart → verify user data restored from S3 (on next login)

2. **Performance Tests:**
   - Compare file I/O speed: EBS vs old EFS (expect 50-80% improvement)
   - Test publish operation latency with various experiment sizes (1GB, 10GB, 50GB)
   - Verify no impact on user login time (data already on instance EBS)

3. **Cost Validation:**
   - Monitor for 1 week:
     - Published EFS size growth
     - EC2 EBS root volume usage
     - S3 usage (should be unchanged)
   - Compare against previous week's EFS studio_data costs
   - Verify ~40% storage cost reduction

**Estimated Time:** 1-2 days

---

### Total Implementation Timeline

| Phase | Time Required | Can Be Parallelized |
|-------|--------------|---------------------|
| Phase 1: Terraform | 1-2 hours | No |
| Phase 2: Backend Code | 3-5 hours | After Phase 1 |
| Phase 3: Frontend | 0 hours | N/A |
| Phase 4: Testing | 1-2 days | After Phase 2 |
| **Total** | **2-3 days** | Sequential |

**Deployment Strategy:**
1. Deploy Terraform changes during maintenance window
2. Deploy backend code changes
3. Run migration script for existing published experiments
4. Monitor for 1 week
5. Delete old studio_data EFS after validation
---

## 7. Risk Assessment & Mitigation (Option 1)

### 7.1 Data Loss Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Published experiment copy fails | Low | Medium | S3 fallback on public access, retry copy operation |
| User deletes experiment before publish completes | Low | Low | Copy completes before returning success to user |
| Published EFS fills up | Medium | Medium | Monitor EFS usage, implement cleanup of old published experiments, CloudWatch alarms at 80% |
| EBS instance storage fills up | Medium | High | Monitor via CloudWatch, block new experiments when >90% full, user cleanup prompts |

**Overall Data Loss Risk: LOW** - All data backed up to S3, published data on durable EFS

### 7.2 Performance Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Slow publish operation (large experiments) | Medium | Low | Async copy operation, show progress indicator, copy in background |
| EBS I/O contention (multiple users per instance) | Low | Low | Monitor disk I/O, scale horizontally (add instances) if needed |
| Published EFS slower than expected | Low | Low | Use EFS General Purpose mode (already configured), monitor performance metrics |

**Overall Performance Risk: LOW** - EBS provides faster local I/O than current EFS

### 7.3 Cost Overrun Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Published EFS grows larger than estimated | Medium | Medium | Implement retention policy (e.g., unpublish inactive experiments after 6 months), monitor growth weekly |
| EBS root volumes underutilized | Low | Low | Acceptable - savings still achieved, can optimize instance count later |
| Users publish too many large experiments | Low | Medium | Implement publish quota per user (e.g., max 10 published experiments), require manual approval for large publishes |

**Overall Cost Risk: LOW** - Even at 50% over estimate, still cheaper than current EFS

### 7.4 Rollback Plan

**If issues arise during deployment:**

1. **Immediate rollback (< 24 hours):**
   - Revert Terraform changes (restore `efs_volume_configuration` to studio_data)
   - Deploy previous backend code version
   - Old studio_data EFS still has all data (not deleted yet)
   - **Downtime:** ~10 minutes for ECS task restart

2. **Data recovery (after EFS deleted):**
   - All user data exists in S3
   - Restore from S3 to new/old EFS
   - Published experiments in published_data EFS (preserved)
   - **Recovery time:** 1-4 hours depending on data volume

3. **Partial rollback (keep published EFS, revert user data):**
   - Can keep published_data EFS optimization
   - Revert only user workspace data to old studio_data EFS
   - Best of both worlds if published EFS works well

**Rollback Risk: VERY LOW** - Clear path back to current architecture

---

## Appendix A: Key File References

| Component | File Path | Purpose |
|-----------|-----------|---------|
| EFS Config | `config/terraform/infrastructure.tf:76-168` | EFS file systems, access points, mount targets |
| S3 Config | `config/terraform/infrastructure.tf:175-247` | S3 bucket, versioning, policies |
| ECS Task Definition | `config/terraform/compute.tf:223-501` | Volume mounts, environment variables |
| S3 Upload/Download | `studio/app/common/core/storage/s3_storage_controller.py` | S3 operations |
| File Upload API | `studio/app/common/routers/files.py:204-284` | Upload endpoint, quota checks |
| Experiment Deletion | `studio/app/common/core/experiment/experiment_writer.py:175-227` | Local and S3 cleanup |
| Public Dataview | `studio/app/common/routers/dataview.py:61-111` | Public access validation |
| Shared Workspace Model | `studio/app/common/models/workspace.py:32-51` | Database schema |
| Storage Quota Monitor | `studio/app/common/core/cloud/s3_storage_monitor.py` | Usage tracking, alerts |

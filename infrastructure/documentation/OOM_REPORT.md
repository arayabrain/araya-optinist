# OOM Investigation Report

## Date: 2026-01-22

## Executive Summary

Running 5 concurrent workflows causes "Cannot allocate memory" errors and 502 Bad Gateway. CloudWatch shows only ~65-80% memory utilization, but the **container cgroup limit** (5 GB) is hit before the EC2 instance memory (8 GB) is exhausted.

**Solution Implemented:** Hybrid approach combining increased memory limits + application-level workflow limiting.

---

## Current Architecture (Before Fix)

### EC2 Instance (t3.large)
| Resource | Value |
|----------|-------|
| vCPUs | 2 |
| Memory | 8 GB (7857 MB usable) |
| Storage | 120 GB gp3 |

### ECS Task Definition (`subscr-optinist-cloud-taskdef`)
| Parameter | Value | File Location |
|-----------|-------|---------------|
| Task CPU | 2048 | compute.tf:570 |
| Task Memory | 6144 MB (6 GB) | compute.tf:571 |
| Container CPU | 1536 | compute.tf:579 |
| **Container Memory (hard limit)** | **5120 MB (5 GB)** | compute.tf:580 |
| Memory Reservation (soft limit) | 3072 MB (3 GB) | compute.tf:581 |

### Application Settings
| Setting | Value | File Location |
|---------|-------|---------------|
| Uvicorn Workers | 5 (default) | cloud-startup.sh:62 |
| Snakemake Cores | 1 per workflow | snakemake_executor.py:115 |
| Free User Threshold | 5 users | free_manager.tf:56 |

---

## Root Cause Analysis

### The Memory Mismatch Problem

```
┌─────────────────────────────────────────────────────────────┐
│  EC2 Instance (t3.large)                                    │
│  Total: 7857 MB                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  ECS Task                                             │  │
│  │  Reserved: 6144 MB                                    │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  Container (cgroup limit)                       │  │  │
│  │  │  Hard Limit: 5120 MB  ◄── THIS IS THE BOTTLENECK│  │  │
│  │  │                                                 │  │  │
│  │  │  5 workflows × ~1 GB = 5+ GB needed             │  │  │
│  │  │  Container can only use 5 GB                    │  │  │
│  │  │  → OOM at 5 GB even though instance has 8 GB    │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Why CloudWatch Shows 80% But OOM Occurs

| Perspective | Calculation | Result |
|-------------|-------------|--------|
| CloudWatch (EC2 view) | 5 GB used / 8 GB instance | ~65% |
| Container Reality | 5 GB used / 5 GB cgroup limit | **100% → OOM** |

### Evidence from Investigation

| Metric | Value |
|--------|-------|
| Cgroup memory limit | 5,368,709,120 bytes (5120 MB) |
| Max usage recorded | 5,383,397,376 bytes (5134 MB - exceeded!) |
| memory.failcnt | 8,212,903 hits |

### Swap Memory: Not Configured

Verified on 2026-01-22 - **no swap is configured** on host or container:

```
=== HOST (EC2 Instance) ===
              total        used        free
Mem:           7857        2170         180
Swap:             0           0           0    ← NO SWAP

=== CONTAINER ===
SwapTotal:         0 kB    ← NO SWAP
SwapFree:          0 kB
```

**Impact:** Without swap, there's no fallback when memory is exhausted. The OOM killer activates immediately when the cgroup limit is hit, terminating processes without warning.

**Fix:** Added 32 GB swap as a safety net (see Stage 2 below). Configured with `swappiness=20` at both host and container level so it only activates under real memory pressure, not during normal operation.

---

## Measured Memory Profile

### Base Application Memory (Idle State)

Measured on 2026-01-22 via SSM command on running container (no active workflows):

| Metric | Bytes | MB | GB |
|--------|-------|----|----|
| **Current Usage** | 1,889,030,144 | **1801 MB** | **1.8 GB** |
| Max Usage (peak) | 1,892,048,896 | 1804 MB | 1.8 GB |
| Limit | 5,368,709,120 | 5120 MB | 5.0 GB |
| Fail Count | 0 | - | - |

**Base app (idle) uses ~1.8 GB**, which includes:
- 5 Uvicorn worker processes
- Python interpreter + loaded modules (numpy, pandas, snakemake, etc.)
- System overhead

### Memory Budget Calculation

**Old Configuration (caused OOM):**
```
Container limit:         5120 MB
- Base app (idle):      -1800 MB
─────────────────────────────────
Available for workflows: 3320 MB
÷ 5 concurrent workflows
= 664 MB per workflow    ← TOO TIGHT!
```

**New Configuration (implemented):**
```
Container limit:         6656 MB
- Base app (idle):      -1800 MB
─────────────────────────────────
Available for workflows: 4856 MB
÷ 3 concurrent workflows (limited)
= 1619 MB per workflow   ← ADEQUATE
```

---

## Failed Workflows (Original Incident)

| Workflow | User | Bucket | Status | Evidence |
|----------|------|--------|--------|----------|
| beea8ee7 | optinist_test_user_free_3@araya.org | optinist-user-11-55c9b56849 | FAILED | workflow.yaml 404 |
| 4a3b86d6 | optinist_test_user_free_2@araya.org | optinist-user-10-65650ac9d8 | FAILED | workflow.yaml 404 |
| 3 others | Various | Various | SUCCESS | Completed before OOM |

### Timeline

```
11:17:32  Memory: 4230 MB (54% of instance, 83% of container limit)
11:17:43  Memory: 5174 MB (66% of instance, 101% of container limit!)
11:17:51  Memory: 5266 MB (67% of instance, 103% of container limit!)
11:18:05  Memory: 5138 MB - Cgroup limit exceeded, OOM killer active
11:18:xx  OOM killer terminates workflow processes
11:19:xx  502 Bad Gateway (app unresponsive)
11:22:xx  New task spins up on new instance (ASG triggered)
11:23:xx  App responsive again
```

---

## Alternatives Considered

### Reduce Uvicorn Workers (5 → 3)

**Proposal:** Reduce `UVICORN_WORKERS` from 5 to 3 in `cloud-startup.sh`:

```bash
# Current
UVICORN_WORKERS=${UVICORN_WORKERS:-5}

# Proposed (not implemented)
UVICORN_WORKERS=${UVICORN_WORKERS:-3}
```

**Rationale:** Fewer workers = lower base memory footprint.

**Why we did NOT implement this:**

1. **Workers ≠ Concurrent Workflows**
   - Uvicorn workers handle HTTP requests, not workflow execution
   - Workflows run as background tasks in separate processes (`ProcessPoolExecutor`)
   - Reducing workers wouldn't directly limit concurrent workflows

2. **Incorrect Mental Model**
   ```
   WRONG assumption:  3 workers → max 3 workflows
   ACTUAL behavior:   3 workers → max 3 concurrent HTTP requests
                      But each request spawns a background workflow
                      All 5 workflows still run simultaneously
   ```

3. **Would Hurt HTTP Throughput**
   - Fewer workers = slower response to concurrent HTTP requests
   - Users would experience slower UI responsiveness
   - Doesn't solve the actual memory problem

4. **Better Solution Exists**
   - Stage 3 (workflow limiter) directly limits concurrent workflows at the application level
   - This is the correct layer to enforce the limit
   - Workflows queue gracefully instead of HTTP requests timing out

**When reducing workers WOULD help:**
- If base app memory (idle) was the problem
- Each worker consumes ~100-200 MB
- 5 workers → 3 workers would save ~200-400 MB
- But our measured idle usage (1.8 GB) isn't the bottleneck - workflow execution is

---

## Implemented Solution

### Overview: Defense-in-Depth Approach

Three layers of protection against OOM errors:

1. **Stage 1: Increase container memory** - Use more of available EC2 instance memory
2. **Stage 2: Add swap as safety net** - 32 GB swap with low swappiness absorbs spikes
3. **Stage 3: Limit concurrent workflows** - Max 3 via application-level semaphore, excess queued

### Stage 1: Memory Limit Increases (Terraform)

**Changes in `infrastructure/terraform/compute.tf`:**

| Parameter | Old Value | New Value | Change |
|-----------|-----------|-----------|--------|
| Task Memory | 6144 MB | 7168 MB | +1 GB |
| Container Memory (hard) | 5120 MB | 6656 MB | +1.5 GB |
| Memory Reservation (soft) | 3072 MB | 4096 MB | +1 GB |

Applied to both:
- Autoscaling task definition (lines 570-581)
- Premium task definition (lines 805-816)

**New environment variable added:**
- `MAX_CONCURRENT_WORKFLOWS=3` (used by Stage 3)

### Stage 2: Swap Configuration (Host + Container)

**Modified: `infrastructure/scripts/ecs-user-data.sh`**

Adds swap space during EC2 instance initialization:
- Creates 32 GB swap file at `/swapfile`
- Sets `vm.swappiness=20` at host level
- Persists across reboots via `/etc/fstab`

```bash
# Host-level configuration
SWAP_SIZE_MB=32768
sysctl vm.swappiness=20
```

**Modified: `infrastructure/terraform/compute.tf`**

Adds container-level swappiness in task definition:

```hcl
linuxParameters = {
  swappiness = 20  # Matches host setting
}
```

**Two-level configuration:**
| Level | Setting | Purpose |
|-------|---------|---------|
| Host (EC2) | `vm.swappiness=20` | Default for all processes |
| Container (ECS) | `linuxParameters.swappiness=20` | Explicit for container |

**Why swappiness=20:** Default swappiness (60) causes unnecessary swapping during normal operation. A conservative-moderate value (20) provides a balance:
- Low enough to strongly prefer RAM for active workflow data
- High enough to gradually move inactive pages to swap before pressure hits
- Middle ground between too aggressive (10, risk of swap storm) and too eager (60, unnecessary swapping)

### Stage 3: Workflow Limiter (Application Code)

**New file: `studio/app/common/core/workflow/workflow_limiter.py`**

Cross-process semaphore using file-based locking (`fcntl`) that:
- Limits concurrent workflows to `MAX_CONCURRENT_WORKFLOWS` (default: 3)
- Works across all Uvicorn worker processes
- Queues excess workflows (with configurable timeout)
- Automatically releases slots on completion or error

**Modified: `studio/app/common/core/snakemake/snakemake_executor.py`**

- Acquires workflow slot before execution
- Releases slot in finally block (guaranteed cleanup)
- Raises error if timeout waiting for slot (default: 300s)

**Modified: `cloud-startup.sh`**

- Resets workflow slot counter at container startup
- Prevents stuck counters after crashes

### How It Works

```
Request 1 → acquire_slot() → runs (1/3 slots used)
Request 2 → acquire_slot() → runs (2/3 slots used)
Request 3 → acquire_slot() → runs (3/3 slots used)
Request 4 → acquire_slot() → waits... → runs when slot freed
Request 5 → acquire_slot() → waits... → runs when slot freed
```

---

## Deployment Plan

### Stage 1: Deploy Memory Increases (Terraform)

**Changes:** Task definition memory limits increased.

```bash
cd infrastructure/terraform
terraform plan
terraform apply
```

Then force new deployment:

```bash
aws ecs update-service --cluster subscr-optinist-cloud-cluster \
  --service subscr-optinist-cloud-service --force-new-deployment \
  --region ap-northeast-1
```

**Effect:** Container memory limit increases from 5120 MB → 6656 MB. The `MAX_CONCURRENT_WORKFLOWS` env var is added but unused until Stage 3.

### Stage 2: Deploy Swap Configuration (Terraform)

**Changes:** EC2 user data script adds 32 GB swap with low swappiness.

**File:** `infrastructure/scripts/ecs-user-data.sh`

```bash
# Creates /swapfile (2 GB)
# Sets vm.swappiness=20 (only swap under pressure)
```

The swap is created on new EC2 instances. To apply to existing instances:

```bash
# Option A: Terminate existing instances (ASG will launch new ones)
aws autoscaling set-desired-capacity \
  --auto-scaling-group-name subscr-optinist-cloud-asg \
  --desired-capacity 0 --region ap-northeast-1
# Wait, then set back to 1

# Option B: Manually add swap to running instance via SSM
aws ssm send-command --instance-ids <INSTANCE_ID> \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["dd if=/dev/zero of=/swapfile bs=1M count=32768","chmod 600 /swapfile","mkswap /swapfile","swapon /swapfile","echo /swapfile swap swap defaults 0 0 >> /etc/fstab","sysctl vm.swappiness=20"]' \
  --region ap-northeast-1
```

**Effect:** 32 GB swap provides safety buffer. Under memory pressure, processes swap to disk instead of being OOM-killed immediately.

### Stage 3: Deploy Workflow Limiter (Application Code)

**Changes:** Application-level concurrent workflow limiting.

Push code changes and build new Docker image:
- `workflow_limiter.py` (new file)
- `snakemake_executor.py` (modified)
- `cloud-startup.sh` (modified)

**Effect:** Workflow limiter activates, limiting concurrent workflows to 3. Excess workflows queue instead of running simultaneously.

---

## Stage 1 Verification Results (2026-01-22)

### Test Configuration
- **5 concurrent workflows** triggered simultaneously by 5 different users
- **Container memory limit:** 6656 MB (6.5 GiB) - Stage 1 applied
- **Swap:** None configured (Stage 2 not yet applied)
- **Workflow limiter:** Not active (Stage 3 not yet applied)

### Monitoring Summary

| Metric | Baseline | Peak | Final |
|--------|----------|------|-------|
| Container Memory % | 23.83% | **99.95%** | 50.95% |
| Container Memory | 1.55 GiB | **6.497 GiB** | 3.31 GiB |
| Host Available | 5513 MB | **517 MB** | 5368 MB |
| Host Free | - | **130 MB** | - |
| CPU | 0.70% | 199.34% | 0.73% |
| PIDs | 41 | 157 | 62 |

### Timeline of Peak Memory Usage

```
Poll 1  (baseline):  1.549 GiB (23.83%)  - idle
Poll 2  (+15s):      3.07 GiB  (47.23%)  - workflows starting
Poll 4  (+30s):      4.955 GiB (76.23%)  - approaching old 5 GB limit
Poll 7  (+50s):      5.411 GiB (83.25%)  - WOULD HAVE OOM'D WITH OLD LIMIT
Poll 9  (+65s):      6.033 GiB (92.81%)  - still running
Poll 12 (+85s):      6.418 GiB (98.74%)  - near new limit
Poll 13 (+90s):      6.497 GiB (99.95%)  - PEAK - at container limit
Poll 14-18:          ~6.49 GiB (99.9%)   - sustained at limit
Poll 20 (+130s):     5.922 GiB (91.11%)  - workflows completing
Poll 32 (+210s):     3.311 GiB (50.94%)  - idle, all workflows done
```

### Key Findings

1. **Stage 1 memory increase was essential:**
   - Peak usage: 6.497 GiB (99.95% of 6.5 GiB limit)
   - Old limit was 5 GiB - workflows would have OOM'd at Poll 7 (~5.4 GiB)
   - The extra 1.5 GB headroom allowed all 5 workflows to complete

2. **No adverse side effects observed:**
   - All 5 workflows completed successfully
   - No OOM errors or process termination
   - Container remained healthy throughout
   - System recovered to idle state normally

3. **System was at the edge - Stage 2 is necessary:**
   - Hit 99.95% of container limit (only 3 MB headroom)
   - Host had only 130 MB free RAM at peak
   - No swap = no safety buffer if any workflow used slightly more memory
   - A 6th concurrent workflow would likely cause OOM

4. **Memory fluctuated rapidly:**
   - Dropped from 99% to 73% in one poll, then spiked back to 94%
   - CloudWatch (60s sampling) cannot capture these rapid fluctuations
   - Explains why CloudWatch shows ~80% when OOM actually occurs

### Conclusion

**Stage 1 PASSED** - The memory increase from 5 GB to 6.5 GB successfully prevents OOM for 5 concurrent workflows under the current test conditions. However, operating at 99.95% capacity with no swap is precarious. **Stage 2 (swap configuration) is necessary** to provide a safety buffer for:
- Workflows that may use slightly more memory
- Memory spikes during garbage collection or intermediate calculations
- Any scenario where 6+ workflows might run concurrently

---

## Stage 2 Verification Results (2026-01-22)

### Test Configuration
- **5 concurrent workflows** triggered simultaneously by 5 different users
- **Container memory limit:** 6656 MB (6.5 GiB) - Stage 1 applied
- **Swap:** 32 GB configured on host, container maxSwap=32768 MiB, swappiness=20
- **Workflow limiter:** Not active (Stage 3 not yet applied)

### Monitoring Summary

| Metric | Baseline | Peak | Final |
|--------|----------|------|-------|
| Container Memory % | 24.60% | **82.29%** | 64.35% |
| Container Memory | 1.60 GiB | **5.35 GiB** | 4.18 GiB |
| Host Available | 5594 MB | **2971 MB** | 5562 MB |
| Swap Used | 0 MB | **3287 MB** | 925 MB |
| CPU | 0.80% | 198% | 0.73% |
| PIDs | 38 | 153 | 89 |

### Comparison: Stage 1 vs Stage 2

| Metric | Stage 1 (no swap) | Stage 2 (32GB swap) | Improvement |
|--------|-------------------|---------------------|-------------|
| Peak Container Memory | 99.95% | **82.29%** | 17.66% lower |
| Peak Host Free RAM | 130 MB | **196 MB** | 50% more headroom |
| Swap Buffer | 0 (none) | **~3.3 GB used** | Safety net active |
| Near OOM? | YES (0.05% margin) | NO (~18% margin) | Much safer |

### Timeline of Memory Usage with Swap

```
Poll 1  (baseline):  1.60 GiB (24.60%)  - idle, swap: 0 MB
Poll 2  (+15s):      2.43 GiB (37.35%)  - workflows starting, swap: 0 MB
Poll 3  (+25s):      3.11 GiB (47.88%)  - swap activating: 22 MB
Poll 4  (+35s):      2.68 GiB (41.18%)  - swap absorbing: 1857 MB
Poll 9  (+75s):      3.60 GiB (55.37%)  - swap: 3287 MB (peak swap)
Poll 14 (+115s):     5.35 GiB (82.29%)  - PEAK MEMORY, swap: 1987 MB
Poll 17 (+140s):     4.46 GiB (68.61%)  - workflows completing
Poll 21 (+180s):     5.16 GiB (79.34%)  - still processing
Final   (+240s):     4.18 GiB (64.35%)  - idle, swap: 925 MB
```

### Key Findings

1. **Swap successfully absorbed memory pressure:**
   - Peak swap usage: 3.3 GB during high memory demand
   - This prevented container memory from hitting 100%
   - Swap usage correlated with memory spikes

2. **Container never approached critical levels:**
   - Peak: 82.29% vs 99.95% in Stage 1
   - ~18% headroom vs 0.05% in Stage 1
   - No risk of OOM during normal operation

3. **Swap persisted after workflows (expected behavior):**
   - 925 MB remained in swap at idle
   - This is normal - inactive pages stay in swap until needed
   - Does not indicate a problem

4. **Performance remained acceptable:**
   - All 5 workflows completed successfully
   - CPU usage patterns similar to Stage 1
   - No noticeable slowdown from swap usage

### Conclusion

**Stage 2 PASSED** - The 32 GB swap configuration with swappiness=20 provides an effective safety buffer:

- Reduces peak container memory usage by ~18%
- Provides graceful degradation under memory pressure instead of OOM
- Swap activates appropriately during spikes (swappiness=20 working as intended)
- No adverse performance impact observed

**Stage 3 (workflow limiter) is now optional** for the current workload, but still recommended for:
- Scenarios with 6+ concurrent workflows
- Workflows that may use more memory than test cases
- Additional safety margin

---

## Verification Steps

### After Stage 1 (Memory Increases)

1. Verify new cgroup limit:
   ```bash
   aws ssm send-command \
     --instance-ids <INSTANCE_ID> \
     --document-name AWS-RunShellScript \
     --parameters 'commands=["sudo docker exec $(sudo docker ps --filter \"name=ecs-subscr-optinist-cloud-taskdef\" --format \"{{.ID}}\" | head -1) cat /sys/fs/cgroup/memory/memory.limit_in_bytes"]' \
     --region ap-northeast-1
   ```
   Expected: 6,979,321,856 bytes (6656 MB)

2. Run 5 concurrent workflow test
3. Monitor `memory.failcnt` - should not spike

### After Stage 2 (Swap Configuration)

1. Verify swap is active:
   ```bash
   aws ssm send-command \
     --instance-ids <INSTANCE_ID> \
     --document-name AWS-RunShellScript \
     --parameters 'commands=["free -m | grep Swap && sysctl vm.swappiness"]' \
     --region ap-northeast-1
   ```
   Expected:
   ```
   Swap:         32768           0       32768
   vm.swappiness = 10
   ```

2. Under memory pressure, swap usage should increase (check with `free -m`)
3. OOM killer should NOT activate unless swap is also exhausted

### After Stage 3 (Workflow Limiter)

1. Check logs for workflow slot messages:
   ```
   "Acquired workflow slot: 1/3 slots now in use"
   "Waiting for workflow slot: 3/3 slots in use"
   ```

2. Verify queuing behavior with 5+ concurrent requests

3. Check `/tmp/optinist_workflow_counter` on container

---

## Files Modified

| File | Change | Stage |
|------|--------|-------|
| `infrastructure/terraform/compute.tf` | Memory limits + MAX_CONCURRENT_WORKFLOWS env var | 1 |
| `infrastructure/scripts/ecs-user-data.sh` | Added 32 GB swap with host swappiness=20 | 2 |
| `infrastructure/terraform/compute.tf` | Added linuxParameters.swappiness=20 to containers | 2 |
| `studio/app/common/core/workflow/workflow_limiter.py` | **NEW** - Cross-process workflow semaphore | 3 |
| `studio/app/common/core/snakemake/snakemake_executor.py` | Integrated workflow slot acquire/release | 3 |
| `cloud-startup.sh` | Reset workflow counter at startup | 3 |

---

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_CONCURRENT_WORKFLOWS` | 3 | Maximum workflows running simultaneously |
| `WORKFLOW_SLOT_TIMEOUT` | 300 | Seconds to wait for a slot before error |
| `UVICORN_WORKERS` | 5 | Number of Uvicorn worker processes |

### Memory Architecture (After Fix)

```
┌─────────────────────────────────────────────────────────────┐
│  EC2 Instance (t3.large)                                    │
│  RAM: 7857 MB + Swap: 32768 MB (32 GB)                      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  ECS Task                                             │  │
│  │  Reserved: 7168 MB                                    │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  Container (cgroup limit)                       │  │  │
│  │  │  Hard Limit: 6656 MB                            │  │  │
│  │  │                                                 │  │  │
│  │  │  Base App:     ~1800 MB                         │  │  │
│  │  │  Workflow 1:   ~1000 MB                         │  │  │
│  │  │  Workflow 2:   ~1000 MB                         │  │  │
│  │  │  Workflow 3:   ~1000 MB                         │  │  │
│  │  │  ─────────────────────                          │  │  │
│  │  │  Total:        ~4800 MB (72% of limit)          │  │  │
│  │  │  Headroom:     ~1856 MB                         │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
│  OS/System: ~689 MB                                         │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Swap (safety net): 32768 MB (32 GB)                  │  │
│  │  swappiness=20 (only used under pressure)             │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Defense-in-Depth Layers

```
Layer 1: Memory Limits     ─── Container has 6656 MB (up from 5120 MB)
              │
              ▼ (if exceeded)
Layer 2: Swap Buffer       ─── 32 GB swap absorbs temporary spikes
              │
              ▼ (if swap filling)
Layer 3: Workflow Limiter  ─── Max 3 concurrent workflows queued
              │
              ▼ (absolute last resort)
         OOM Killer         ─── Only if all defenses exhausted
```

---

## Appendix: Related Configuration

### CloudWatch Scaling Alarms (monitoring.tf)

| Alarm | Threshold | Action |
|-------|-----------|--------|
| Memory High | 80% | Scale Up |
| Memory Low | 10% | Scale Down |

**Note:** These alarms use EC2 instance memory %, not container cgroup %. Consider adding container-level monitoring.

### ASG Configuration

| Parameter | Value |
|-----------|-------|
| Min Size | 1 |
| Max Size | 3 |
| Scale Up | +1 instance |
| Scale Down | -1 instance |
| Cooldown | 300 seconds |

---

## Future Considerations

- Add container-level memory monitoring (cgroup metrics to CloudWatch)
- Implement per-workflow memory profiling
- Consider memory-optimized instance types (r5.large) if workflows grow
- Add workflow queue visibility to UI (position in queue, ETA)

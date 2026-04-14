# Agent Recovery: Stale ECS Agent Detection and Self-Healing

## Executive Summary

- **On-host watchdog** detects the stale-ECS-agent failure mode by grepping the ECS agent log for `InvalidInstanceException` / `Missing container instance arn` and re-running the documented manual recovery sequence
- **On-host health probe** flips the instance to `Unhealthy` in the ASG when the ECS agent introspection endpoint reports `AgentConnected=false` for >5 minutes, so the ASG terminates and replaces it
- **Out-of-band alarms** (EventBridge `agentConnected=false`, watchdog heartbeat silence, ASG↔ECS reconciliation Lambda) page humans when the on-host watchdog itself is broken
- **ASG health-check type is `EC2`** because dynamic-port ECS targets never register with the ALB target group, so ELB health checks would silently treat stranded hosts as healthy
- **AMI is pinned** because the watchdog parses the ECS agent log line format, which is AMI-version-specific — bumping the AMI requires the smoke test in this document

---

## Key Architectural Principles

1. **Recovery on the host, alerting in the cloud**
   - Recovery actions (`systemctl stop ecs`, `docker rm -f ecs-agent`, wipe `agent.db`, `systemctl start ecs`) run on the affected EC2 instance
   - CloudWatch alarms only page humans — they never call `set-instance-health` or `terminate`
   - Keeps recovery fast (no Lambda cold start, no IAM round-trip) and avoids cross-account/cross-region failure modes

2. **Defence in depth, not single-point detection**
   - Three independent detection paths: log-grep watchdog (host), introspection probe (host), EventBridge state-change events (control plane)
   - Plus a reconciliation Lambda that compares ASG `InService` instances against `ListContainerInstances` every 5 min
   - Any single path can fail without the system going blind

3. **Alarm-only Lambda, never destructive**
   - The reconciliation Lambda emits a count metric and that's it
   - The only resource that *terminates* an instance is the ASG itself, in response to the on-host probe flipping health to `Unhealthy`
   - Centralises destructive authority on the host, where the lifecycle guard via IMDS prevents racing the capacity provider

4. **AMI pinning over `most_recent = true`**
   - Watchdog log parsing depends on the agent log format, which has shifted between AMI releases
   - Pinning forces a deliberate bump + smoke test, instead of silently breaking detection on a Tuesday morning ASG refresh

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────┐
│ EC2 instance (ECS-optimized AMI, pinned)                       │
│                                                                │
│  ┌────────────────────────┐   ┌────────────────────────────┐   │
│  │ agent-recovery.timer   │   │ agent-health-probe.timer   │   │
│  │   (every 5 min)        │   │   (every 5 min)            │   │
│  │   → watchdog.sh        │   │   → health-probe.sh        │   │
│  │                        │   │                            │   │
│  │ greps ecs-agent.log*   │   │ curls localhost:51678      │   │
│  │ for known stale-agent  │   │ /v1/metadata; if           │   │
│  │ error strings within   │   │ AgentConnected=false       │   │
│  │ last 5 min; if found:  │   │ for >5 min:                │   │
│  │   stop ecs             │   │   set-instance-health      │   │
│  │   docker rm ecs-agent  │   │   --status Unhealthy       │   │
│  │   rm agent.db          │   │   (ASG terminates host)    │   │
│  │   start ecs            │   │                            │   │
│  └─────────┬──────────────┘   └─────────────┬──────────────┘   │
│            │                                │                  │
│            ↓ PutLogEvents (heartbeat + actions)                 │
└────────────┼────────────────────────────────┼──────────────────┘
             │                                │
             ↓                                ↓
   ┌───────────────────────────┐    ┌────────────────────────┐
   │ CloudWatch Logs           │    │ Auto Scaling Group     │
   │ /ecs/<env>-agent-recovery │    │ health_check_type=EC2  │
   └─────────┬─────────────────┘    │ (replaces unhealthy)   │
             │                      └────────────────────────┘
             ↓
   ┌────────────────────────────────────────────────────┐
   │ Out-of-band alarms (alert-only)                    │
   │  • Heartbeat-missing alarm (30 min fleet silence)  │
   │  • EventBridge agentConnected=false → metric       │
   │  • Reconciliation Lambda (ASG vs ECS, every 5 min) │
   └────────────────────────────────────────────────────┘
```

| Responsibility                          | On-host watchdog | On-host probe | Reconciliation Lambda | EventBridge rule |
|-----------------------------------------|------------------|---------------|-----------------------|------------------|
| Detect stale-agent error in agent log   | Yes — exclusive  | No            | No                    | No               |
| Detect prolonged AgentConnected=false   | No               | Yes — primary | Yes — secondary       | Yes — primary    |
| Run recovery sequence (stop/rm/start)   | Yes — exclusive  | No            | No                    | No               |
| Mark instance Unhealthy in ASG          | No               | Yes — exclusive | No                  | No               |
| Page humans                             | No               | No            | Yes (via alarm)       | Yes (via alarm)  |

---

## Implementation Details

### Watchdog flow

The watchdog (`infrastructure/scripts/ecs-user-data.sh`, embedded as a heredoc that writes `/opt/agent-recovery/watchdog.sh`) runs every 5 minutes via `agent-recovery.timer`:

1. Emit a `watchdog tick` heartbeat to `/ecs/<env>-agent-recovery` so the heartbeat-missing alarm stays quiet
2. Check ASG target lifecycle state via IMDS — exit if `Pending:*` or `Terminating:*` to avoid racing the capacity provider
3. Glob `/var/log/ecs/ecs-agent.log*` (rotation-safe) and grep for `InvalidInstanceException` or `Missing container instance arn`
4. Parse the leading whitespace-separated token of each match as an ISO-8601 timestamp; ignore matches older than 5 minutes
5. Honour the per-instance rate limit (max 1 recovery / hour, sentinel in tmpfs `/var/run`)
6. Run the recovery sequence in this exact order: `systemctl stop ecs` → `docker rm -f ecs-agent` → `rm -f /var/lib/ecs/data/agent.db` → `systemctl start ecs`
7. Touch the sentinel and emit a `recovery complete` log line

### Health probe flow

The probe (`/opt/agent-recovery/health-probe.sh`, also written by user-data) runs every 5 minutes via `agent-health-probe.timer`:

1. Honour the same lifecycle guard as the watchdog
2. `curl http://localhost:51678/v1/metadata` and look for `"AgentConnected": true`
3. If connected, clear the disconnect-since state file and exit
4. If disconnected, write the current epoch to the state file (or read the existing one)
5. If `now - since >= 300` seconds, call `aws autoscaling set-instance-health --health-status Unhealthy` so the ASG replaces the host

### Reconciliation Lambda

`infrastructure/terraform/agent_recovery_lambda.py` runs every 5 minutes via EventBridge schedule:

### `handler()`

**File:** `infrastructure/terraform/agent_recovery_lambda.py`
**Purpose:** Count ASG `InService` instances that are missing from (or `agentConnected=false` in) the ECS control plane and emit the count as a CloudWatch metric
**Input:** Scheduled event from EventBridge; reads `CLUSTERS` and `ASG_NAMES` env vars (comma-separated)
**Output:** `{"unregistered": int, "details": list[str]}`; publishes `OptiNiSt/AgentRecovery::EcsAsgInstanceUnregisteredCount`
**Calls:** `_registered_ec2_ids()` → `_asg_instance_ids()` → `cloudwatch.put_metric_data()`

The Lambda is alarm-only — it never calls `set-instance-health` or `terminate`. `_asg_instance_ids()` filters to `LifecycleState == "InService"` so newly-launching instances inside the 15-minute health-check grace period are not counted as missing.

---

## Edge Case Handling

### 1. Watchdog log-format drift after AMI bump

**Problem:** The watchdog parses the leading token of each ECS agent log line as an ISO-8601 timestamp. ECS-optimized AMI releases have shifted this format historically (e.g. from `2024-01-15T10:30:45Z [INFO] ...` to `level=info time="..." msg=...`). If the format changes, `awk '{print $1}'` returns a non-timestamp, `date -d` fails, every match is silently dropped, and the watchdog never fires.

**Solution:**
- AMI is pinned via `var.ecs_optimized_ami_name` in `infrastructure/terraform/compute.tf`
- Bumping the pin requires running the [smoke test](#testing) below
- The on-host probe and the EventBridge rule provide independent coverage if the watchdog goes blind

### 2. Stranded host with no running task

**Problem:** With dynamic-port ECS task registration, a host with zero running tasks is never registered in the ALB target group. An `health_check_type = "ELB"` ASG would treat the unused target as healthy and let the host live forever.

**Solution:** ASG uses `health_check_type = "EC2"`. The on-host probe is what makes plain EC2 health checks meaningful — it flips the instance to `Unhealthy` once the ECS agent has been disconnected for 5+ minutes.

### 3. Recovery races ASG capacity provider drain

**Problem:** Capacity provider scale-in or instance refresh can put a host into `Terminating:Wait` while the watchdog is mid-recovery. Restarting the agent on a host the ASG is already terminating wastes work and can confuse the drain.

**Solution:** Both scripts read `target-lifecycle-state` from IMDS and exit early on `Terminating:*` or `Pending:*`. The IMDS path requires IMDSv1 (currently allowed by the launch template); if IMDSv2 is ever enforced, the curl calls in `lifecycle-state.sh` need to fetch a token first.

### 4. Watchdog recovers in a tight loop

**Problem:** A persistent stale-agent state could trigger the watchdog every 5 minutes, hammering the recovery sequence and the agent state file.

**Solution:** Per-instance rate limit of 1 recovery per hour, enforced via a sentinel file in `/var/run/agent-recovery/`. The sentinel lives on tmpfs, so a reboot clears it — by design, since rebooted hosts deserve a fresh attempt.

### 5. False positive during scale-out

**Problem:** A newly-launched instance takes a minute or two to register with ECS. A naive reconciliation Lambda would count it as "missing" and page on every scale-out.

**Solution:** `_asg_instance_ids()` filters to `LifecycleState == "InService"`, and the alarm requires 2 evaluation periods of 5 minutes (10 min total) above 0. Combined with `health_check_grace_period = 900`, this comfortably absorbs normal registration delay.

---

## Monitoring and Metrics

| Metric Name                          | Description                                                              | Unit  | Trigger                                       |
|--------------------------------------|--------------------------------------------------------------------------|-------|-----------------------------------------------|
| `AgentDisconnectedCount`             | Count of `agentConnected=false` events from EventBridge                  | Count | ECS control plane state change                |
| `AgentRecoveryHeartbeatCount`        | Count of `watchdog tick` lines in `/ecs/<env>-agent-recovery`                  | Count | Every watchdog run on every host              |
| `EcsAsgInstanceUnregisteredCount`    | ASG `InService` instances missing from ECS or with `agentConnected=false`| Count | Reconciliation Lambda (every 5 min)           |

| Alarm Name                                       | Trigger condition                                              | Treat missing |
|--------------------------------------------------|----------------------------------------------------------------|---------------|
| `${env}-ecs-agent-disconnected`                  | `AgentDisconnectedCount >= 1` over 5 min                       | notBreaching  |
| `${env}-agent-recovery-heartbeat-missing`        | `AgentRecoveryHeartbeatCount < 1` over 30 min (fleet-wide sum) | breaching     |
| `${env}-ecs-asg-instance-unregistered`           | `EcsAsgInstanceUnregisteredCount > 0` over 2 × 5 min           | notBreaching  |
| `${env}-free-tier-running-task-count-zero`       | Free-tier `RunningTaskCount < 1` over 5 min                    | breaching     |
| `${env}-free-tier-alb-no-healthy-targets`        | Free-tier ALB `HealthyHostCount < 1` over 2 × 60 sec           | breaching     |

The heartbeat-missing alarm aggregates across the whole fleet (no instance dimension) — by design, so a single stuck host doesn't fire it. Per-host coverage comes from the EventBridge rule and the on-host probe.

---

## Configuration

| Variable                       | Purpose                                                         | Default                                       |
|--------------------------------|-----------------------------------------------------------------|-----------------------------------------------|
| `ecs_optimized_ami_name`       | Pinned ECS-optimized AMI; bump triggers smoke test              | `amzn2-ami-ecs-hvm-2.0.20251015-x86_64-ebs`   |
| `CLUSTERS`                     | Comma-separated ECS cluster names for the reconciliation Lambda | `aws_ecs_cluster.main.name`                   |
| `ASG_NAMES`                    | Comma-separated ASG names for the reconciliation Lambda         | `aws_autoscaling_group.main.name`             |
| `RATE_LIMIT_SECONDS`           | Min seconds between watchdog recoveries per instance            | `3600` (in `watchdog.sh`)                     |
| `DISCONNECT_THRESHOLD_SECONDS` | Seconds of `AgentConnected=false` before probe marks Unhealthy  | `300` (in `health-probe.sh`)                  |

---

## Testing

### AMI bump smoke test

**Run this every time `ecs_optimized_ami_name` is bumped in `infrastructure/terraform/compute.tf`.** The watchdog's detection logic and recovery sequence make assumptions about the AMI that can silently break across releases. A passing smoke test confirms detection still fires *and* recovery still cleans up.

Deploy the new AMI to a non-production ASG, then on one fresh host:

1. **Verify log format is still parseable.**
   ```bash
   sudo tail -n 5 /var/log/ecs/ecs-agent.log
   # Confirm each line starts with an ISO-8601 timestamp.
   FIRST_TOKEN=$(sudo head -n 1 /var/log/ecs/ecs-agent.log | awk '{print $1}' | tr -d '"')
   date -d "$FIRST_TOKEN" -u +%s
   # Expected: a numeric epoch, not "date: invalid date".
   ```
   If `date -d` fails, **stop** — the watchdog will silently never fire on this AMI. Adjust the parsing in `watchdog.sh` before promoting.

2. **Inject a synthetic match and run the watchdog manually.**
   ```bash
   echo "$(date -u +%FT%TZ) [ERROR] InvalidInstanceException: smoke test" \
     | sudo tee -a /var/log/ecs/ecs-agent.log
   sudo rm -f /var/run/agent-recovery/last-recovery   # clear rate limit
   sudo /opt/agent-recovery/watchdog.sh
   ```
   Expected: no errors. In particular, no `Unit ecs.service not found`, no `No such container: ecs-agent`, no `cannot remove '/var/lib/ecs/data/agent.db': No such file`. If any of those appear, the recovery sequence assumes a path or unit name that no longer exists on this AMI — fix `watchdog.sh` before promoting.

3. **Confirm the host re-registers.**
   ```bash
   sleep 30
   curl -s http://localhost:51678/v1/metadata | python3 -m json.tool
   # Expected: AgentConnected: true, ContainerInstanceArn populated.
   ```

4. **Confirm heartbeats reach CloudWatch.**
   ```bash
   INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
   aws logs tail /ecs/<env>-agent-recovery --since 10m --log-stream-names "$INSTANCE_ID"
   # Expected: at least one "watchdog tick" line in the last 10 min.
   ```

5. **Confirm the health probe works.**
   ```bash
   sudo /opt/agent-recovery/health-probe.sh; echo "exit=$?"
   # Expected: exit=0 with no errors when the agent is connected.
   ```

If any step fails, do **not** promote the AMI to production. Revert the pin and fix the watchdog or recovery logic against the new AMI first.

---

## Key Functions Reference

| Function / script                          | Purpose                                                                                    |
|--------------------------------------------|--------------------------------------------------------------------------------------------|
| `/opt/agent-recovery/watchdog.sh`          | Detects stale-agent log entries and runs the documented manual recovery sequence           |
| `/opt/agent-recovery/health-probe.sh`      | Marks the instance Unhealthy in the ASG when the agent is disconnected for 5+ minutes      |
| `/opt/agent-recovery/lifecycle-state.sh`   | Reads ASG target lifecycle state via IMDS; shared by watchdog and probe                    |
| `handler()` (Lambda)                       | Counts ASG-vs-ECS reconciliation gaps and emits a CloudWatch metric                        |
| `_registered_ec2_ids()`                    | Returns the set of EC2 IDs registered as container instances with `agentConnected=true`    |
| `_asg_instance_ids()`                      | Returns the set of `InService` EC2 IDs in a given ASG                                      |

---

## AWS Resources

| Resource type                       | Name                                                  | Purpose                                              |
|-------------------------------------|-------------------------------------------------------|------------------------------------------------------|
| `aws_cloudwatch_log_group`          | `/ecs/<env>-agent-recovery`                                 | Heartbeats, recovery actions, EventBridge events     |
| `aws_cloudwatch_event_rule`         | `${env}-ecs-container-instance-state-change`          | Captures ECS container instance state-change events  |
| `aws_cloudwatch_log_metric_filter`  | `${env}-agent-disconnected`                           | `agentConnected=false` → metric                      |
| `aws_cloudwatch_log_metric_filter`  | `${env}-agent-recovery-heartbeat`                     | `watchdog tick` → metric                             |
| `aws_cloudwatch_metric_alarm`       | `${env}-ecs-agent-disconnected`                       | Pages on disconnect events                           |
| `aws_cloudwatch_metric_alarm`       | `${env}-agent-recovery-heartbeat-missing`             | Pages when on-host watchdog has gone silent          |
| `aws_cloudwatch_metric_alarm`       | `${env}-ecs-asg-instance-unregistered`                | Pages when reconciliation finds gaps                 |
| `aws_lambda_function`               | `${env}-agent-recovery-reconciliation`                | ASG vs ECS reconciliation, alarm-only                |
| `aws_iam_role_policy` addition      | `autoscaling:SetInstanceHealth` on ECS instance role  | Lets the on-host probe mark itself Unhealthy         |

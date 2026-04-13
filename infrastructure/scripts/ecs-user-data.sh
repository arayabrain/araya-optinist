#!/bin/bash
set -e
exec > /var/log/ecs-setup.log 2>&1

echo "$(date): Starting ECS setup with OptiNiSt configuration"

# ECS Configuration
echo ECS_CLUSTER=${cluster_name} >> /etc/ecs/ecs.config
echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config
echo ECS_INSTANCE_ATTRIBUTES='{"tier":"${tier}"}' >> /etc/ecs/ecs.config
# Must be >= task-level stopTimeout (see compute.tf).
echo ECS_CONTAINER_STOP_TIMEOUT=120s >> /etc/ecs/ecs.config

# Premium: clear agent.db on boot so restarted instances re-register cleanly.
# premium_manager handles deregistering the old container instance before stop.
# Free/background: skip — ASG replacement is the recovery path.
if [ "${tier}" = "premium" ]; then
cat > /etc/systemd/system/ecs-clear-checkpoint.service << 'UNIT_EOF'
[Unit]
Description=Clear stale ECS agent checkpoint before startup
Before=ecs.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=true
ExecStart=/bin/rm -f /var/lib/ecs/data/agent.db

[Install]
WantedBy=multi-user.target
UNIT_EOF

systemctl daemon-reload
systemctl enable ecs-clear-checkpoint.service
fi

# =====================================================================
# Stale ECS agent watchdog + on-instance health probe
# ---------------------------------------------------------------------
# Two systemd timers run every 5 minutes on the ECS host:
#
#   agent-recovery.timer:
#     Greps /var/log/ecs/ecs-agent.log* for InvalidInstanceException /
#     "Missing container instance arn" within the last 5 minutes. If
#     matched, performs the recovery sequence:
#     `systemctl stop ecs` -> `docker rm -f ecs-agent` -> rm agent.db
#     -> `systemctl start ecs`. Rate-limited to 1 recovery / hour /
#     instance via a sentinel in /var/run (tmpfs — auto-clears on boot).
#     Logs every action to CloudWatch Logs `/ecs/agent-recovery`, which
#     also serves as the source for the watchdog heartbeat alarm.
#
#   agent-health-probe.timer:
#     Calls the agent introspection endpoint at
#     http://localhost:51678/v1/metadata. If AgentConnected has been
#     `false` for more than 5 minutes, calls
#     `aws autoscaling set-instance-health --health-status Unhealthy`
#     so the ASG (now using EC2 health-checks) terminates and replaces
#     the host. This is what makes plain EC2 health-checks meaningful:
#     they would otherwise only catch hardware/OS failure.
#
# Both timers honour the ASG lifecycle state via IMDS
# (`autoscaling/target-lifecycle-state`) and skip when the instance is
# in `Terminating:*` or `Pending:*` so they cannot race the capacity
# provider's managed drain or first-boot ECS registration.
# IMDSv1 is currently allowed (no metadata_options on the launch
# template). If IMDSv2 is ever enforced, switch the IMDS calls below
# to fetch a token first.
# =====================================================================

mkdir -p /opt/agent-recovery /var/run/agent-recovery /etc/agent-recovery

# Region pinned from Terraform; sourced by the agent-recovery scripts.
cat > /etc/agent-recovery/env << ENV_EOF
AWS_REGION=${aws_region}
INSTANCE_TIER=${tier}
ENV_EOF
chmod 0644 /etc/agent-recovery/env

# Recovery scripts are sourced from infrastructure/scripts/agent-recovery/
# and inlined at terraform plan time via templatefile(). Edit them there,
# not here. The 'EOF' quoting on each heredoc prevents bash from
# interpreting any $-references in the inlined content at instance-run
# time (the substitution itself happens earlier, in Terraform).
cat > /opt/agent-recovery/lifecycle-state.sh << 'LIFECYCLE_EOF'
${agent_recovery_lifecycle_sh}
LIFECYCLE_EOF
chmod +x /opt/agent-recovery/lifecycle-state.sh

cat > /opt/agent-recovery/watchdog.sh << 'WATCHDOG_EOF'
${agent_recovery_watchdog_sh}
WATCHDOG_EOF
chmod +x /opt/agent-recovery/watchdog.sh

cat > /opt/agent-recovery/health-probe.sh << 'PROBE_EOF'
${agent_recovery_health_probe_sh}
PROBE_EOF
chmod +x /opt/agent-recovery/health-probe.sh

# systemd units
cat > /etc/systemd/system/agent-recovery.service << 'UNIT_EOF'
[Unit]
Description=Stale ECS agent checkpoint watchdog
After=ecs.service

[Service]
Type=oneshot
ExecStart=/opt/agent-recovery/watchdog.sh
UNIT_EOF

cat > /etc/systemd/system/agent-recovery.timer << 'UNIT_EOF'
[Unit]
Description=Run agent-recovery watchdog every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Unit=agent-recovery.service

[Install]
WantedBy=timers.target
UNIT_EOF

cat > /etc/systemd/system/agent-health-probe.service << 'UNIT_EOF'
[Unit]
Description=ECS agent health probe (marks instance Unhealthy on disconnect)
After=ecs.service

[Service]
Type=oneshot
ExecStart=/opt/agent-recovery/health-probe.sh
UNIT_EOF

cat > /etc/systemd/system/agent-health-probe.timer << 'UNIT_EOF'
[Unit]
Description=Run agent-health-probe every 5 minutes

[Timer]
OnBootSec=10min
OnUnitActiveSec=5min
Unit=agent-health-probe.service

[Install]
WantedBy=timers.target
UNIT_EOF

systemctl daemon-reload
systemctl enable --now agent-recovery.timer
systemctl enable --now agent-health-probe.timer

# Install packages
yum update -y
yum install -y amazon-ssm-agent mysql amazon-efs-utils nc mysql-client git docker amazon-cloudwatch-agent awscli

# Setup swap as memory safety net (defense-in-depth for OOM prevention)
# This provides a buffer before OOM killer activates, giving workflows
# a chance to complete during temporary memory spikes
SWAP_SIZE_MB=${swap_size_mb}  # Configurable per instance type (0 to skip)
SWAP_FILE=/swapfile
if [ "$SWAP_SIZE_MB" -gt 0 ]; then
    echo "$(date): Setting up swap space ($${SWAP_SIZE_MB}MB)"
    if [ ! -f "$SWAP_FILE" ]; then
        dd if=/dev/zero of=$SWAP_FILE bs=1M count=$SWAP_SIZE_MB status=progress
        chmod 600 $SWAP_FILE
        mkswap $SWAP_FILE
        swapon $SWAP_FILE
        echo "$SWAP_FILE swap swap defaults 0 0" >> /etc/fstab
        # Set low swappiness - only use swap under real memory pressure
        echo "vm.swappiness=20" >> /etc/sysctl.conf
        sysctl vm.swappiness=20
        echo "$(date): Swap setup complete ($${SWAP_SIZE_MB}MB, swappiness=20)"
    else
        echo "$(date): Swap file already exists"
    fi
else
    echo "$(date): Skipping swap setup (swap_size_mb=0)"
fi

# Start SSM agent
if ! systemctl is-active --quiet amazon-ssm-agent; then
    systemctl enable amazon-ssm-agent
    systemctl start amazon-ssm-agent
fi

# Create CloudWatch agent config
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'CW_CONFIG'
{
    "metrics": {
        "namespace": "CWAgent",
        "metrics_collected": {
            "mem": {
                "measurement": [
                    "mem_used_percent"
                ]
            },
            "cpu": {
                "measurement": [
                    "cpu_usage_idle",
                    "cpu_usage_iowait"
                ],
                "totalcpu": true
            }
        }
    },
    "logs": {
        "logs_collected": {
            "files": {
                "collect_list": [
                    {
                        "file_path": "/proc/loadavg",
                        "log_group_name": "/aws/ec2/loadavg",
                        "log_stream_name": "{instance_id}",
                        "timezone": "UTC"
                    }
                ]
            }
        }
    }
}
CW_CONFIG

# Start CloudWatch agent
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config -m ec2 -s \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

# Start Docker (using same safe approach)
if ! systemctl is-active --quiet docker; then
    systemctl enable docker || echo "$(date): Docker enable failed"
    systemctl start docker || echo "$(date): Docker start failed"
fi
for user in ec2-user ssm-user; do
  if id "$user" &>/dev/null; then
      usermod -a -G docker "$user" && echo "$(date): Added $user to docker group"
      break
  fi
done

# Clone and build OptiNiSt
echo "$(date): Cloning OptiNiSt repository"
cd /opt
    git clone -b ${git_branch} ${git_repo} optinist-for-cloud || {
    echo "$(date): ERROR: Git clone failed!"
    exit 1
}
if [ ! -d "optinist-for-cloud" ]; then
    echo "$(date): ERROR: Repository directory not created"
    exit 1
fi
cd optinist-for-cloud

# Create Firebase configuration files on the host
echo "$(date): Creating Firebase configuration files"
mkdir -p /opt/optinist-for-cloud/studio/config/auth

# Create firebase_config.json
cat > /opt/optinist-for-cloud/studio/config/auth/firebase_config.json << 'FIREBASE_CONFIG'
${firebase_config_json}
FIREBASE_CONFIG

# Create firebase_private.json
cat > /opt/optinist-for-cloud/studio/config/auth/firebase_private.json << 'FIREBASE_PRIVATE'
${firebase_private_json}
FIREBASE_PRIVATE

# Set proper permissions
chmod 644 /opt/optinist-for-cloud/studio/config/auth/firebase_*.json

# Add AWS Batch plugins to Dockerfile
echo "$(date): Adding AWS Batch plugins to Dockerfile"
# Build the Docker image
echo "$(date): Building OptiNiSt Docker image"
if [ ! -f "studio/config/docker/Dockerfile" ]; then
    echo "ERROR: Dockerfile not found in repository"
    ls -la
    exit 1
fi

# ECR login and pull pre-built image
echo "$(date): Logging into ECR and pulling pre-built image"
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin ${ecr_registry}
echo "$(date): Pulling OptiNiSt Docker image from ECR"
docker pull "${ecr_repository_url}:latest" || {
    echo "ERROR: Docker pull failed!"
    exit 1
}

# EFS setup
mkdir -p /mnt/efs
echo "${efs_id}.efs.ap-northeast-1.amazonaws.com:/ /mnt/efs efs tls,_netdev" >> /etc/fstab
mount -a || echo "EFS will retry"

# Test DB connection (non-blocking)
nc -z ${db_host} 3306 && echo "DB accessible" || echo "DB will be available"

#!/bin/bash
set -e
exec > /var/log/ecs-setup.log 2>&1

echo "$(date): Starting ECS setup with OptiNiSt configuration"

# ECS Configuration
echo ECS_CLUSTER=${cluster_name} >> /etc/ecs/ecs.config
echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config
echo ECS_INSTANCE_ATTRIBUTES='{"tier":"${tier}"}' >> /etc/ecs/ecs.config

# Systemd service to clear stale ECS agent checkpoint on every boot.
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
docker pull "${ecr_repository_url}:${docker_image_tag}" || {
    echo "ERROR: Docker pull failed!"
    exit 1
}

# EFS setup
mkdir -p /mnt/efs
echo "${efs_id}.efs.ap-northeast-1.amazonaws.com:/ /mnt/efs efs tls,_netdev" >> /etc/fstab
mount -a || echo "EFS will retry"

# Test DB connection (non-blocking)
nc -z ${db_host} 3306 && echo "DB accessible" || echo "DB will be available"

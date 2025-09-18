#!/bin/bash

# Premium User Instance User Data Script
# Optimized for dedicated premium user workloads

echo "Starting premium user instance setup..."

# Configure ECS agent for premium tier
echo ECS_CLUSTER=${cluster_name} >> /etc/ecs/ecs.config
echo ECS_INSTANCE_ATTRIBUTES='{"tier":"premium","dedicated":"true"}' >> /etc/ecs/ecs.config

# Optimize instance for single premium user
echo ECS_RESERVED_MEMORY=512 >> /etc/ecs/ecs.config
echo ECS_AVAILABLE_LOGGING_DRIVERS='["json-file","awslogs"]' >> /etc/ecs/ecs.config

# Premium performance optimizations
echo 'vm.swappiness=10' >> /etc/sysctl.conf
echo 'vm.dirty_ratio=15' >> /etc/sysctl.conf
echo 'vm.dirty_background_ratio=5' >> /etc/sysctl.conf

# Apply sysctl changes
sysctl -p

# Install CloudWatch agent for premium monitoring
yum update -y
yum install -y amazon-cloudwatch-agent

# Start ECS agent
service ecs start
chkconfig ecs on

# Send success signal
yum install -y aws-cfn-bootstrap

echo "Premium user instance setup complete"

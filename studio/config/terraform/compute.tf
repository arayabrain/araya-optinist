
# =========================
# Application Load Balancer
# =========================
resource "aws_lb" "autoscaling" {
  name               = "subscr-optinist-lb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id, aws_security_group.ecs.id]
  subnets            = [aws_subnet.public1.id, aws_subnet.public2.id]

  enable_deletion_protection = false
  idle_timeout               = 180 # Increased to accommodate premium instance cold starts

  # Enable access logs for detailed monitoring
  access_logs {
    bucket  = aws_s3_bucket.app_storage.id
    prefix  = "alb-logs"
    enabled = true
  }

  depends_on = [
    aws_s3_bucket_policy.app_storage
  ]

  tags = {
    Name = "subscr-optinist-load-balancer"
  }
}

# Load Balancer Listener - HTTP to HTTPS Redirect
resource "aws_lb_listener" "autoscaling" {
  load_balancer_arn = aws_lb.autoscaling.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}


# HTTPS listener for autoscaling ALB
resource "aws_lb_listener" "autoscaling_https" {
  load_balancer_arn = aws_lb.autoscaling.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.main.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn
  }

  depends_on = [aws_acm_certificate_validation.main]
}

# Target Group for ALB
resource "aws_lb_target_group" "autoscaling" {
  name        = "subscr-optinist-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 5
    interval            = 60
    matcher             = "200"
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 30
  }

  stickiness {
    type            = "lb_cookie"
    cookie_duration = 86400
    enabled         = true
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "subscr-optinist-cloud-target-group"
  }
}

# ======================================
# Launch Template for Auto Scaling Group
# ======================================

# Get the latest ECS-optimized AMI
data "aws_ami" "ecs_optimized" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-ecs-hvm-*-x86_64-ebs"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_launch_template" "ecs" {
  name_prefix   = "subscr-optinist-ecs-"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = "t3.large"
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 30
      volume_type = "gp3"
      encrypted   = true
    }
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    set -e
    exec > /var/log/ecs-setup.log 2>&1

    echo "$(date): Starting ECS setup with OptiNiSt configuration"

    # ECS Configuration
    echo ECS_CLUSTER=${aws_ecs_cluster.main.name} >> /etc/ecs/ecs.config
    echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
    echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config
    echo ECS_INSTANCE_ATTRIBUTES='{"tier":"free"}' >> /etc/ecs/ecs.config

    # Install packages
    yum update -y
    yum install -y amazon-ssm-agent mysql amazon-efs-utils nc mysql-client git docker amazon-cloudwatch-agent awscli

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
        git clone -b ${var.git_branch} ${var.git_repo} optinist-for-cloud || {
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
    ${var.firebase_config_json}
    FIREBASE_CONFIG

    # Create firebase_private.json
    cat > /opt/optinist-for-cloud/studio/config/auth/firebase_private.json << 'FIREBASE_PRIVATE'
    ${var.firebase_private_json}
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
    aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin ${split("/", var.ecr_repository_url)[0]}
    echo "$(date): Pulling OptiNiSt Docker image from ECR"
    docker pull "${var.ecr_repository_url}:latest" || {
        echo "ERROR: Docker pull failed!"
        exit 1
    }

    # EFS setup
    mkdir -p /mnt/efs
    echo "${aws_efs_file_system.snmk.id}.efs.ap-northeast-1.amazonaws.com:/ /mnt/efs efs tls,_netdev" >> /etc/fstab
    mount -a || echo "EFS will retry"

    # Test DB connection (non-blocking)
    nc -z ${replace(aws_db_instance.main.endpoint, ":3306", "")} 3306 && echo "DB accessible" || echo "DB will be available"
    EOF
  )
  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "subscr-optinist-asg-instance"
      Type    = "ECS-ASG"
      Service = "autoscaling"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ==================
# Auto Scaling Group
# ==================
resource "aws_autoscaling_group" "main" {
  name                      = "subscr-optinist-asg"
  vpc_zone_identifier       = [aws_subnet.private1.id, aws_subnet.private2.id]
  target_group_arns         = [aws_lb_target_group.autoscaling.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 900
  default_cooldown          = 300

  min_size         = var.asg_min_size
  max_size         = var.asg_max_size
  desired_capacity = var.asg_desired_capacity

  launch_template {
    id      = aws_launch_template.ecs.id
    version = "$Latest"
  }

  force_delete              = true
  termination_policies      = ["OldestInstance"]
  wait_for_capacity_timeout = "0"

  # Enable instance scale-in protection
  protect_from_scale_in = false

  # Enable detailed monitoring
  enabled_metrics = [
    "GroupMinSize",
    "GroupMaxSize",
    "GroupDesiredCapacity",
    "GroupInServiceInstances",
    "GroupTotalInstances",
    "GroupPendingInstances",
    "GroupStandbyInstances",
    "GroupTerminatingInstances"
  ]

  tag {
    key                 = "Name"
    value               = "subscr-optinist-asg-instance"
    propagate_at_launch = true
  }

  tag {
    key                 = "Service"
    value               = "autoscaling"
    propagate_at_launch = true
  }

  tag {
    key                 = "Type"
    value               = "ASG-ECS"
    propagate_at_launch = true
  }

  tag {
    key                 = "LaunchTemplateVersion"
    value               = aws_launch_template.ecs.latest_version
    propagate_at_launch = true
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      instance_warmup        = 300
      min_healthy_percentage = 0
    }
  }

  lifecycle {
    ignore_changes = [desired_capacity]
  }

  timeouts {
    delete = "30m"
  }

  # Lifecycle hooks for logging
  initial_lifecycle_hook {
    name                 = "subscr-optinist-launch-hook"
    default_result       = "CONTINUE"
    heartbeat_timeout    = 300
    lifecycle_transition = "autoscaling:EC2_INSTANCE_LAUNCHING"
  }

  initial_lifecycle_hook {
    name                 = "subscr-optinist-terminate-hook"
    default_result       = "CONTINUE"
    heartbeat_timeout    = 300
    lifecycle_transition = "autoscaling:EC2_INSTANCE_TERMINATING"
  }
}

# Auto Scaling Policies
resource "aws_autoscaling_policy" "scale_up" {
  name                   = "subscr-optinist-scale-up"
  scaling_adjustment     = 1
  adjustment_type        = "ChangeInCapacity"
  cooldown               = 300
  autoscaling_group_name = aws_autoscaling_group.main.name
}

resource "aws_autoscaling_policy" "scale_down" {
  name                   = "subscr-optinist-scale-down"
  scaling_adjustment     = -1
  adjustment_type        = "ChangeInCapacity"
  cooldown               = 300
  autoscaling_group_name = aws_autoscaling_group.main.name
}

# =============
# PREMIUM TIER
# ============

# Premium ECS Service for pre-warmed containers
resource "aws_ecs_service" "premium" {
  name                               = "subscr-premium-optinist-cloud-service"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.premium.arn
  desired_count                      = 1
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 50
  launch_type                        = "EC2"

  enable_execute_command = true

  # Target premium spot fleet instances only
  placement_constraints {
    type       = "memberOf"
    expression = "attribute:tier == premium"
  }


  depends_on = [
    aws_instance.premium
  ]
}

# Premium Launch Template - Optimized for dedicated premium users
resource "aws_launch_template" "premium" {
  name_prefix   = "subscr-optinist-premium-"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = "t3.large" # Will be overridden by spot fleet instance types
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 30
      volume_type = "gp3"
      encrypted   = true
    }
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    set -e
    exec > /var/log/ecs-setup.log 2>&1

    echo "$(date): Starting ECS setup with OptiNiSt configuration"

    # ECS Configuration
    echo ECS_CLUSTER=${aws_ecs_cluster.main.name} >> /etc/ecs/ecs.config
    echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
    echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config
    echo ECS_INSTANCE_ATTRIBUTES='{"tier":"premium"}' >> /etc/ecs/ecs.config

    # Install packages
    yum update -y
    yum install -y amazon-ssm-agent mysql amazon-efs-utils nc mysql-client git docker amazon-cloudwatch-agent awscli

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
        git clone -b ${var.git_branch} ${var.git_repo} optinist-for-cloud || {
        echo "$(date): ERROR: Git clone failed!"
        exit 1
    }
    if [ ! -d "optinist-for-cloud" ]; then
        echo "$(date): ERROR: Repository directory not created"
        exit 1
    }
    cd optinist-for-cloud

    # Create Firebase configuration files on the host
    echo "$(date): Creating Firebase configuration files"
    mkdir -p /opt/optinist-for-cloud/studio/config/auth

    # Create firebase_config.json
    cat > /opt/optinist-for-cloud/studio/config/auth/firebase_config.json << 'FIREBASE_CONFIG'
    ${var.firebase_config_json}
    FIREBASE_CONFIG

    # Create firebase_private.json
    cat > /opt/optinist-for-cloud/studio/config/auth/firebase_private.json << 'FIREBASE_PRIVATE'
    ${var.firebase_private_json}
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
    }

    # ECR login and pull pre-built image
    echo "$(date): Logging into ECR and pulling pre-built image"
    aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin ${split("/", var.ecr_repository_url)[0]}
    echo "$(date): Pulling OptiNiSt Docker image from ECR"
    docker pull "${var.ecr_repository_url}:latest" || {
        echo "ERROR: Docker pull failed!"
        exit 1
    }

    # EFS setup
    mkdir -p /mnt/efs
    echo "${aws_efs_file_system.snmk.id}.efs.ap-northeast-1.amazonaws.com:/ /mnt/efs efs tls,_netdev" >> /etc/fstab
    mount -a || echo "EFS will retry"

    # Test DB connection (non-blocking)
    nc -z ${replace(aws_db_instance.main.endpoint, ":3306", "")} 3306 && echo "DB accessible" || echo "DB will be available"
    EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "subscr-optinist-premium-instance"
      Type    = "ECS-Premium"
      Tier    = "premium"
      Service = "premium-spot-fleet"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# =======
# SSL/TLS
# =======
# Reference to existing Route53 hosted zone
data "aws_route53_zone" "main" {
  name         = var.frontend_domain
  private_zone = false
}

# SSL/TLS certificate for HTTPS support
resource "aws_acm_certificate" "main" {
  domain_name       = var.frontend_domain
  validation_method = "DNS"

  subject_alternative_names = [
    "*.${var.frontend_domain}" # Support subdomains
  ]

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.frontend_domain} certificate"
  }
}

# DNS validation record for ACM certificate
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.main.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = data.aws_route53_zone.main.zone_id
}

# Wait for certificate validation to complete
resource "aws_acm_certificate_validation" "main" {
  certificate_arn         = aws_acm_certificate.main.arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}


# Route53 A record pointing to ALB
resource "aws_route53_record" "main" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.frontend_domain
  type    = "A"

  alias {
    name                   = aws_lb.autoscaling.dns_name
    zone_id                = aws_lb.autoscaling.zone_id
    evaluate_target_health = true
  }
}

# Route53 A record for www subdomain (redirects to main domain)
resource "aws_route53_record" "www" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "www.${var.frontend_domain}"
  type    = "A"

  alias {
    name                   = aws_lb.autoscaling.dns_name
    zone_id                = aws_lb.autoscaling.zone_id
    evaluate_target_health = true
  }
}

# ===========
# ECS Cluster
# ===========
resource "aws_ecs_cluster" "main" {
  name = "subscr-optinist-cloud-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  service_connect_defaults {
    namespace = aws_service_discovery_private_dns_namespace.main.arn
  }

  tags = {
    Name = "subscr-optinist-cloud-cluster"
  }
}

# Service Discovery
resource "aws_service_discovery_private_dns_namespace" "main" {
  name = "subscr.optinist.local"
  vpc  = aws_vpc.main.id
}

# ECS Capacity Provider
resource "aws_ecs_capacity_provider" "main" {
  name = "subscr-optinist-capacity-provider"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.main.arn
    managed_termination_protection = "DISABLED"

    managed_scaling {
      maximum_scaling_step_size = 1
      minimum_scaling_step_size = 1
      status                    = "DISABLED"
      target_capacity           = 90
      instance_warmup_period    = 300
    }
  }

  depends_on = [
    aws_autoscaling_group.main,
    aws_launch_template.ecs
  ]

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "subscr-optinist-capacity-provider"
  }
}

# ECS Cluster Capacity Providers
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = [aws_ecs_capacity_provider.main.name]

  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.main.name
    weight            = 1
    base              = 0
  }

  depends_on = [
    aws_ecs_capacity_provider.main,
    aws_autoscaling_group.main,
    aws_ecs_cluster.main,
    aws_launch_template.ecs
  ]

  lifecycle {
    create_before_destroy = false
    prevent_destroy       = false
    ignore_changes        = [capacity_providers]
  }
}

resource "aws_instance" "premium" {
  count = 1 # Start with 1 premium instance as base capacity

  launch_template {
    id      = aws_launch_template.premium.id
    version = "$Latest"
  }

  instance_type = "t3.large"
  subnet_id     = aws_subnet.private1.id

  # On shutdown, stop instance instead of terminating
  instance_initiated_shutdown_behavior = "stop"

  # Prevent accidental termination
  disable_api_termination = false

  tags = {
    Name          = "subscr-premium-${count.index + 1}"
    Type          = "Premium-Instance"
    Service       = "premium-tier"
    Tier          = "premium"
    InstanceIndex = count.index + 1
  }

  # Stop instance on creation to reduce costs when not in use
  # Lambda will start instances when users request premium access
  provisioner "local-exec" {
    command = "aws ec2 stop-instances --instance-ids ${self.id} --region ${var.aws_region} || true"
  }


  lifecycle {
    create_before_destroy = true
  }
}

# ===================
# ECS Task Definition
# ===================
resource "aws_ecs_task_definition" "autoscaling" {
  family                   = "subscr-optinist-cloud-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = 2048
  memory                   = 6144
  task_role_arn            = aws_iam_role.ecs_task.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name              = "subscr-optinist-cloud-container"
      image             = "${var.ecr_repository_url}:latest"
      cpu               = 1536
      memory            = 5120
      memoryReservation = 3072
      essential         = true
      workingDirectory  = "/app"
      entryPoint        = ["/bin/sh", "-c"]
      command           = ["./cloud-startup.sh"]

      portMappings = [
        {
          name          = "subscr-optinist-cloud-container-port-8000"
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "CLOUDWATCH_LOG_GROUP"
          value = "/ecs/subscr-optinist-cloud-taskdef"
        },
        {
          name  = "PYTHONPATH"
          value = "/app/"
        },
        {
          name  = "TZ"
          value = "Asia/Tokyo"
        },
        {
          name  = "DB_HOST"
          value = split(":", aws_db_instance.main.endpoint)[0]
        },
        {
          name  = "DB_PORT"
          value = split(":", aws_db_instance.main.endpoint)[1]
        },
        {
          name  = "DB_USER"
          value = var.mysql_user
        },
        {
          name  = "DB_NAME"
          value = var.mysql_database
        },
        {
          name  = "DB_PASSWORD"
          value = var.mysql_password
        },
        {
          name  = "BACKEND_HOST"
          value = "0.0.0.0"
        },
        {
          name  = "BACKEND_PORT"
          value = "8000"
        },
        {
          name  = "FRONTEND_SERVER_HOST"
          value = var.frontend_domain
        },
        {
          name  = "FRONTEND_SERVER_PORT"
          value = var.frontend_port
        },
        {
          name  = "FRONTEND_SERVER_PROTO"
          value = var.frontend_protocol
        },
        {
          name  = "INITIAL_FIREBASE_UID"
          value = var.optinist_admin_uid
        },
        {
          name  = "INITIAL_USER_NAME"
          value = var.optinist_admin_name
        },
        {
          name  = "INITIAL_USER_EMAIL"
          value = var.optinist_admin_email
        },
        {
          name  = "ADMIN_STORAGE_QUOTA_BYTES"
          value = "107374182400"
        },
        {
          name  = "SECRET_KEY"
          value = var.optinist_secret_key
        },
        {
          name  = "S3_DEFAULT_BUCKET_NAME"
          value = aws_s3_bucket.app_storage.id
        },
        {
          name  = "REMOTE_STORAGE_TYPE"
          value = "2"
        },
        {
          name  = "USE_AWS_BATCH"
          value = "false"
        },
        {
          name  = "LOG_LEVEL"
          value = "DEBUG"
        },
        {
          name  = "UVICORN_ACCESS_LOG"
          value = "1"
        },
        {
          name  = "CORS_ORIGINS"
          value = "*"
        },
        {
          name  = "PYTHONUNBUFFERED"
          value = "1"
        },
        {
          name  = "OPTINIST_DIR"
          value = "/app/studio_data"
        },
        {
          name  = "TEST_USERS_CONFIG"
          value = jsonencode(var.test_users)
        },
        {
          name  = "STRIPE_CALLBACK_URL"
          value = "${var.frontend_protocol}://${var.frontend_domain}"
        },
        {
          name  = "STRIPE_WEBHOOK_SECRET"
          value = var.stripe_webhook_secret
        },
      ]
      secrets = [
        {
          name      = "AWS_ACCESS_KEY_ID"
          valueFrom = "${aws_secretsmanager_secret.aws_credentials.arn}:AWS_ACCESS_KEY_ID::"
        },
        {
          name      = "AWS_SECRET_ACCESS_KEY"
          valueFrom = "${aws_secretsmanager_secret.aws_credentials.arn}:AWS_SECRET_ACCESS_KEY::"
        },
      ]

      mountPoints = [
        {
          sourceVolume  = "subscr-optinist-cloud-snmk-volume"
          containerPath = "/app/.snakemake"
          readOnly      = false
        },
        {
          sourceVolume  = "subscr-optinist-cloud-studio-data-volume"
          containerPath = "/app/studio_data"
          readOnly      = false
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -v http://127.0.0.1:8000/health"]
        interval    = 300
        timeout     = 5
        retries     = 3
        startPeriod = 300
      }

      dockerLabels = {
        "health.check.enabled" = "true"
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"             = "/ecs/subscr-optinist-cloud-taskdef"
          "mode"                      = "non-blocking"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"           = "25m"
          "awslogs-region"            = "ap-northeast-1"
          "awslogs-create-group"      = "true"
          "awslogs-stream-prefix"     = "ecs"
          "mode"                      = "non-blocking"
        }
      }
    }
  ])

  volume {
    name = "subscr-optinist-cloud-studio-data-volume"
  }

  volume {
    name = "subscr-optinist-cloud-snmk-volume"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.snmk.id
      root_directory     = "/"
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.snmk.id
        iam             = "DISABLED"
      }
    }
  }

  tags = {
    Name = "subscr-optinist-cloud-taskdef"
  }
}


# Premium ECS Task Definition - Pre-warmed containers for instant access
resource "aws_ecs_task_definition" "premium" {
  family                   = "subscr-premium-optinist-cloud-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = 2048
  memory                   = 6144
  task_role_arn            = aws_iam_role.ecs_task.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name              = "subscr-premium-optinist-cloud-container"
      image             = "${var.ecr_repository_url}:latest"
      cpu               = 1536
      memory            = 5120
      memoryReservation = 3072
      essential         = true
      workingDirectory  = "/app"
      entryPoint        = ["/bin/sh", "-c"]
      command           = ["./cloud-startup.sh"]

      portMappings = [
        {
          name          = "subscr-premium-optinist-cloud-container-port-8000"
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "CLOUDWATCH_LOG_GROUP"
          value = "/ecs/subscr-premium-optinist-cloud-taskdef"
        },
        {
          name  = "PYTHONPATH"
          value = "/app/"
        },
        {
          name  = "USER_TIER"
          value = "premium"
        },
        {
          name  = "TZ"
          value = "Asia/Tokyo"
        },
        {
          name  = "DB_HOST"
          value = split(":", aws_db_instance.main.endpoint)[0]
        },
        {
          name  = "DB_PORT"
          value = split(":", aws_db_instance.main.endpoint)[1]
        },
        {
          name  = "DB_USER"
          value = var.mysql_user
        },
        {
          name  = "DB_NAME"
          value = var.mysql_database
        },
        {
          name  = "DB_PASSWORD"
          value = var.mysql_password
        },
        {
          name  = "BACKEND_HOST"
          value = "0.0.0.0"
        },
        {
          name  = "BACKEND_PORT"
          value = "8000"
        },
        {
          name  = "FRONTEND_SERVER_HOST"
          value = var.frontend_domain
        },
        {
          name  = "FRONTEND_SERVER_PORT"
          value = var.frontend_port
        },
        {
          name  = "FRONTEND_SERVER_PROTO"
          value = var.frontend_protocol
        },
        {
          name  = "INITIAL_FIREBASE_UID"
          value = var.optinist_admin_uid
        },
        {
          name  = "INITIAL_USER_NAME"
          value = var.optinist_admin_name
        },
        {
          name  = "INITIAL_USER_EMAIL"
          value = var.optinist_admin_email
        },
        {
          name  = "ADMIN_STORAGE_QUOTA_BYTES"
          value = "107374182400"
        },
        {
          name  = "SECRET_KEY"
          value = var.optinist_secret_key
        },
        {
          name  = "S3_DEFAULT_BUCKET_NAME"
          value = aws_s3_bucket.app_storage.id
        },
        {
          name  = "REMOTE_STORAGE_TYPE"
          value = "2"
        },
        {
          name  = "USE_AWS_BATCH"
          value = "false"
        },
        {
          name  = "LOG_LEVEL"
          value = "DEBUG"
        },
        {
          name  = "UVICORN_ACCESS_LOG"
          value = "1"
        },
        {
          name  = "CORS_ORIGINS"
          value = "*"
        },
        {
          name  = "PYTHONUNBUFFERED"
          value = "1"
        },
        {
          name  = "OPTINIST_DIR"
          value = "/app/studio_data"
        },
        {
          name  = "STRIPE_CALLBACK_URL"
          value = "${var.frontend_protocol}://${var.frontend_domain}"
        },
        {
          name  = "STRIPE_WEBHOOK_SECRET"
          value = var.stripe_webhook_secret
        }
      ]

      mountPoints = [
        {
          sourceVolume  = "subscr-premium-optinist-cloud-studio-data-volume"
          containerPath = "/opt/studio/dataset"
          readOnly      = false
        },
        {
          sourceVolume  = "subscr-premium-optinist-cloud-snmk-volume"
          containerPath = "/efs"
          readOnly      = false
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"             = "/ecs/subscr-premium-optinist-cloud-taskdef"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"           = "25m"
          "awslogs-region"            = "ap-northeast-1"
          "awslogs-create-group"      = "true"
          "awslogs-stream-prefix"     = "ecs"
          "mode"                      = "non-blocking"
        }
      }
    }
  ])

  volume {
    name = "subscr-premium-optinist-cloud-studio-data-volume"
  }

  volume {
    name = "subscr-premium-optinist-cloud-snmk-volume"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.snmk.id
      root_directory     = "/"
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.snmk.id
        iam             = "DISABLED"
      }
    }
  }

  tags = {
    Name = "subscr-premium-optinist-cloud-taskdef"
    Tier = "premium"
  }
}

# ===========
# ECS Service
# ===========
resource "aws_ecs_service" "autoscaling" {
  name                               = "subscr-optinist-cloud-service"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.autoscaling.arn
  desired_count                      = 1
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 0

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.main.name
    weight            = 1
    base              = 0
  }

  enable_execute_command = true

  load_balancer {
    target_group_arn = aws_lb_target_group.autoscaling.arn
    container_name   = "subscr-optinist-cloud-container"
    container_port   = 8000
  }

  depends_on = [
    aws_autoscaling_group.main,
    aws_db_instance.main,
    aws_lb.autoscaling,
    aws_lb_listener.autoscaling
  ]

  placement_constraints {
    type = "distinctInstance" # Force different instances
  }

  health_check_grace_period_seconds = 900

  tags = {
    Name = "subscr-optinist-cloud-service"
  }
}

# ===========================
# ECS Service Auto Scaling
# ===========================
resource "aws_appautoscaling_target" "autoscaling_ecs" {
  max_capacity       = 3
  min_capacity       = 1
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.autoscaling.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"

  depends_on = [aws_ecs_service.autoscaling]
}

# CPU-based scaling policy
resource "aws_appautoscaling_policy" "autoscaling_ecs_cpu" {
  name               = "subscr-optinist-ecs-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.autoscaling_ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.autoscaling_ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.autoscaling_ecs.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 60.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

# Memory-based scaling policy
resource "aws_appautoscaling_policy" "autoscaling_ecs_memory" {
  name               = "subscr-optinist-ecs-memory-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.autoscaling_ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.autoscaling_ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.autoscaling_ecs.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    target_value       = 80.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

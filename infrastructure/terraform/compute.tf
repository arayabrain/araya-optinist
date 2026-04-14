
# =========================
# Application Load Balancer
# =========================
resource "aws_lb" "autoscaling" {
  name               = "${local.env_prefix}-lb"
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
    Name = "${local.env_prefix}-load-balancer"
  }
}

# Load Balancer Listener - HTTP port 80 (redirects to main listener)
# With custom domain: redirect HTTP -> HTTPS (443)
# Without custom domain (dev): redirect to port 8080
# Note: Port 8080 must be open in the ALB security group for dev to work.
# The redirect ensures the browser loads the frontend from the main listener
# port, so all API calls go through the correct port with premium routing rules.
resource "aws_lb_listener" "autoscaling" {
  load_balancer_arn = aws_lb.autoscaling.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = var.enable_custom_domain ? "443" : "8080"
      protocol    = var.enable_custom_domain ? "HTTPS" : "HTTP"
      status_code = "HTTP_301"
    }
  }
}


# HTTPS listener (or HTTP on 8080 for dev without custom domain)
resource "aws_lb_listener" "autoscaling_https" {
  load_balancer_arn = aws_lb.autoscaling.arn
  port              = var.enable_custom_domain ? "443" : "8080"
  protocol          = var.enable_custom_domain ? "HTTPS" : "HTTP"
  ssl_policy        = var.enable_custom_domain ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  certificate_arn   = var.enable_custom_domain ? aws_acm_certificate_validation.main[0].certificate_arn : null

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn
  }
}

# Target Group for ALB
resource "aws_lb_target_group" "autoscaling" {
  name        = "${local.env_prefix}-tg"
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
    cookie_duration = 300 # 5 minutes (matches Lambda check interval for fast rebalancing)
    enabled         = true
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${local.env_prefix}-cloud-target-group"
  }
}

# ======================================
# Launch Template for Auto Scaling Group
# ======================================

# Agent-recovery scripts inlined into the launch template user-data via
# templatefile(). Kept as standalone files (not heredocs) so they can be
# shellcheck'd, edited, and smoke-tested as real files. See
# AGENT_RECOVERY_ARCHITECTURE.md for the smoke test procedure.
locals {
  agent_recovery_lifecycle_sh    = file("${path.module}/../scripts/agent-recovery/lifecycle-state.sh")
  agent_recovery_watchdog_sh     = file("${path.module}/../scripts/agent-recovery/watchdog.sh")
  agent_recovery_health_probe_sh = file("${path.module}/../scripts/agent-recovery/health-probe.sh")
}

# ECS-optimized AMI — pinned. The stale-agent watchdog in
# scripts/agent-recovery/watchdog.sh greps the ECS agent logs for specific
# error strings whose format can shift between AMI releases, so we pin
# rather than track `recommended`. Re-run the watchdog smoke test
# (AGENT_RECOVERY_ARCHITECTURE.md) whenever this is bumped.
variable "ecs_optimized_ami_name" {
  description = "Pinned ECS-optimized AMI name. Bumping requires re-running the agent-recovery watchdog smoke test."
  type        = string
  default     = "amzn2-ami-ecs-hvm-2.0.20251015-x86_64-ebs"
}

data "aws_ami" "ecs_optimized" {
  owners = ["amazon"]

  filter {
    name   = "name"
    values = [var.ecs_optimized_ami_name]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_launch_template" "ecs" {
  name_prefix   = "${local.env_prefix}-ecs-"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = var.free_instance_type
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 120
      volume_type = "gp3"
      encrypted   = true
    }
  }

  monitoring {
    enabled = true
  }

  # base64gzip (not base64encode): inlined agent-recovery scripts push raw
  # user-data past EC2's 16 KB limit. cloud-init transparently decompresses.
  user_data = base64gzip(templatefile("${path.module}/../scripts/ecs-user-data.sh", {
    tier                           = "free"
    cluster_name                   = aws_ecs_cluster.main.name
    git_branch                     = var.git_branch
    git_repo                       = var.git_repo
    firebase_config_json           = var.firebase_config_json
    firebase_private_json          = var.firebase_private_json
    ecr_registry                   = split("/", local.ecr_repository_url)[0]
    ecr_repository_url             = local.ecr_repository_url
    efs_id                         = aws_efs_file_system.snmk.id
    db_host                        = replace(aws_db_instance.main.endpoint, ":3306", "")
    swap_size_mb                   = 32768 # 32GB swap for workflow memory spikes
    aws_region                     = var.aws_region
    agent_recovery_lifecycle_sh    = local.agent_recovery_lifecycle_sh
    agent_recovery_watchdog_sh     = local.agent_recovery_watchdog_sh
    agent_recovery_health_probe_sh = local.agent_recovery_health_probe_sh
    agent_recovery_log_group       = aws_cloudwatch_log_group.agent_recovery.name
  }))
  tag_specifications {
    resource_type = "instance"
    tags = {
      Name        = "${local.env_prefix}-asg-instance"
      Type        = "ECS-ASG"
      Service     = "autoscaling"
      Environment = local.environment_label
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name        = "${local.env_prefix}-asg-vol"
      Environment = local.environment_label
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
  name                = "${local.env_prefix}-asg"
  vpc_zone_identifier = [aws_subnet.private1.id, aws_subnet.private2.id]
  target_group_arns   = [aws_lb_target_group.autoscaling.arn]
  # Use EC2 health checks rather than ELB. With dynamic-port ECS registration,
  # an instance with no running task is never registered in the target group,
  # so the ELB reports it as `unused` and an "ELB" health check treats that as
  # healthy — letting a stranded host live forever. The on-instance probe in
  # ecs-user-data.sh marks the instance Unhealthy when the ECS agent has been
  # disconnected for >5 min, which is what makes plain EC2 health checks
  # meaningful here.
  health_check_type         = "EC2"
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
    value               = "${local.env_prefix}-asg-instance"
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

  tag {
    key                 = "Environment"
    value               = local.environment_label
    propagate_at_launch = true
  }

  tag {
    key                 = "ManagedBy"
    value               = "terraform"
    propagate_at_launch = true
  }

  tag {
    key                 = "Project"
    value               = "optinist-cloud"
    propagate_at_launch = true
  }

  # Warmup matches health_check_grace_period to clear the agent-recovery
  # boot window before a refreshed host is considered healthy.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      instance_warmup        = 900
      min_healthy_percentage = 50
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
    name                 = "${local.env_prefix}-launch-hook"
    default_result       = "CONTINUE"
    heartbeat_timeout    = 300
    lifecycle_transition = "autoscaling:EC2_INSTANCE_LAUNCHING"
  }

  initial_lifecycle_hook {
    name                 = "${local.env_prefix}-terminate-hook"
    default_result       = "CONTINUE"
    heartbeat_timeout    = 300
    lifecycle_transition = "autoscaling:EC2_INSTANCE_TERMINATING"
  }
}

# Auto Scaling Policies
resource "aws_autoscaling_policy" "scale_up" {
  name                   = "${local.env_prefix}-scale-up"
  scaling_adjustment     = 1
  adjustment_type        = "ChangeInCapacity"
  cooldown               = 300
  autoscaling_group_name = aws_autoscaling_group.main.name
}

resource "aws_autoscaling_policy" "scale_down" {
  name                   = "${local.env_prefix}-scale-down"
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
  name                               = "${var.environment}-premium-optinist-cloud-service"
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
  name_prefix   = "${local.env_prefix}-premium-"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = var.premium_instance_type
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 80
      volume_type = "gp3"
      encrypted   = true
    }
  }

  monitoring {
    enabled = true
  }

  # base64gzip (not base64encode): inlined agent-recovery scripts push raw
  # user-data past EC2's 16 KB limit. cloud-init transparently decompresses.
  user_data = base64gzip(templatefile("${path.module}/../scripts/ecs-user-data.sh", {
    tier                           = "premium"
    cluster_name                   = aws_ecs_cluster.main.name
    git_branch                     = var.git_branch
    git_repo                       = var.git_repo
    firebase_config_json           = var.firebase_config_json
    firebase_private_json          = var.firebase_private_json
    ecr_registry                   = split("/", local.ecr_repository_url)[0]
    ecr_repository_url             = local.ecr_repository_url
    efs_id                         = aws_efs_file_system.snmk.id
    db_host                        = replace(aws_db_instance.main.endpoint, ":3306", "")
    swap_size_mb                   = 32768 # 32GB swap for workflow memory spikes
    aws_region                     = var.aws_region
    agent_recovery_lifecycle_sh    = local.agent_recovery_lifecycle_sh
    agent_recovery_watchdog_sh     = local.agent_recovery_watchdog_sh
    agent_recovery_health_probe_sh = local.agent_recovery_health_probe_sh
    agent_recovery_log_group       = aws_cloudwatch_log_group.agent_recovery.name
  }))

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name        = "${local.env_prefix}-premium-instance"
      Type        = "ECS-Premium"
      Tier        = "premium"
      Service     = "premium-spot-fleet"
      Environment = local.environment_label
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name        = "${local.env_prefix}-premium-vol"
      Environment = local.environment_label
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ===========
# ECS Cluster
# ===========
resource "aws_ecs_cluster" "main" {
  name = "${local.env_prefix}-cloud-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  service_connect_defaults {
    namespace = aws_service_discovery_private_dns_namespace.main.arn
  }

  tags = {
    Name = "${local.env_prefix}-cloud-cluster"
  }
}

# Service Discovery
resource "aws_service_discovery_private_dns_namespace" "main" {
  name = "${var.environment}.optinist.local"
  vpc  = aws_vpc.main.id
}

# ECS Capacity Provider
resource "aws_ecs_capacity_provider" "main" {
  name = "${local.env_prefix}-capacity-provider"

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
    Name = "${local.env_prefix}-capacity-provider"
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

  instance_type = var.premium_instance_type
  subnet_id     = aws_subnet.private1.id

  # On shutdown, stop instance instead of terminating
  instance_initiated_shutdown_behavior = "stop"

  # Prevent accidental termination
  disable_api_termination = false

  tags = {
    Name          = "${var.environment}-premium-${count.index + 1}"
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
  family                   = "${local.env_prefix}-cloud-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = 2048
  memory                   = 7168
  task_role_arn            = aws_iam_role.ecs_task.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name              = "${local.env_prefix}-cloud-container"
      image             = "${local.ecr_repository_url}:latest"
      cpu               = 1536
      memory            = 6656
      memoryReservation = 4096
      essential         = true
      workingDirectory  = "/app"
      entryPoint        = ["/bin/sh", "-c"]
      command           = ["./cloud-startup.sh"]

      stopTimeout = 120 # see ECS_CONTAINER_STOP_TIMEOUT in ecs-user-data.sh

      linuxParameters = {
        maxSwap    = 32768 # Max swap in MiB (matches 32GB host swap on EBS)
        swappiness = 20    # Only swap under memory pressure (host also set to 20)
      }

      portMappings = [
        {
          name          = "${local.env_prefix}-cloud-container-port-8000"
          containerPort = 8000
          hostPort      = 0 # ephemeral
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "ENV_PREFIX"
          value = var.environment
        },
        {
          name  = "CLOUDWATCH_LOG_GROUP"
          value = "/ecs/${local.env_prefix}-cloud-taskdef"
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
          value = aws_db_proxy.main.endpoint
        },
        {
          name  = "DB_PORT"
          value = "3306"
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
          name  = "MYSQL_SSL_MODE"
          value = "REQUIRED"
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
          value = local.effective_frontend_domain
        },
        {
          name  = "FRONTEND_SERVER_PORT"
          value = local.effective_frontend_port
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
          name  = "S3_USER_BUCKET_PREFIX"
          value = var.s3_user_bucket_prefix
        },
        {
          name  = "S3_USER_BUCKET_SECRET"
          value = var.s3_user_bucket_secret
        },
        {
          name  = "REMOTE_STORAGE_TYPE"
          value = "2"
        },
        {
          name  = "LOG_LEVEL"
          value = "INFO"
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
          name  = "SUBSCRIPTION_PLANS_CONFIG"
          value = jsonencode(var.subscription_plans)
        },
        {
          name  = "STRIPE_CALLBACK_URL"
          value = "${var.frontend_protocol}://${local.effective_frontend_domain}"
        },
        {
          name  = "STRIPE_SECRET_KEY"
          value = var.stripe_secret_key
        },
        {
          name  = "STRIPE_WEBHOOK_SECRET"
          value = var.stripe_webhook_secret
        },
        {
          name  = "ROUTING_SECRET_KEY"
          value = var.routing_secret_key
        },
        {
          name  = "SKIP_STORAGE_CHECKS"
          value = "false"
        },
        {
          name  = "INTERNAL_API_SECRET"
          value = random_password.internal_api_secret.result
        },
        # Disable scheduler - background jobs run in dedicated background service
        {
          name  = "DISABLE_BACKGROUND_SCHEDULER"
          value = "1"
        },
        {
          name  = "PREMIUM_MANAGER_FUNCTION_NAME"
          value = "${var.environment}-premium-manager"
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
          sourceVolume  = "${local.env_prefix}-cloud-snmk-volume"
          containerPath = "/app/.snakemake"
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
          "awslogs-group"             = "/ecs/${local.env_prefix}-cloud-taskdef"
          "mode"                      = "non-blocking"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"           = "25m"
          "awslogs-region"            = var.aws_region
          "awslogs-create-group"      = "true"
          "awslogs-stream-prefix"     = "ecs"
          "mode"                      = "non-blocking"
        }
      }
    }
  ])

  volume {
    name = "${local.env_prefix}-cloud-snmk-volume"
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
    Name = "${local.env_prefix}-cloud-taskdef"
  }
}


# Premium ECS Task Definition - Pre-warmed containers for instant access
resource "aws_ecs_task_definition" "premium" {
  family                   = "${var.environment}-premium-optinist-cloud-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = 2048
  memory                   = 7168
  task_role_arn            = aws_iam_role.ecs_task.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name              = "${var.environment}-premium-optinist-cloud-container"
      image             = "${local.ecr_repository_url}:latest"
      cpu               = 1536
      memory            = 6656
      memoryReservation = 4096
      essential         = true
      workingDirectory  = "/app"
      entryPoint        = ["/bin/sh", "-c"]
      command           = ["./cloud-startup.sh"]

      stopTimeout = 120 # see ECS_CONTAINER_STOP_TIMEOUT in ecs-user-data.sh

      # linuxParameters = {
      #   maxSwap    = 32768  # Max swap in MiB (matches 32GB host swap on EBS)
      #   swappiness = 20     # Only swap under memory pressure (host also set to 20)
      # }
      # NOTE: Uncomment after Stage 2 (swap enabled on instances)

      portMappings = [
        {
          name          = "${var.environment}-premium-optinist-cloud-container-port-8000"
          containerPort = 8000
          hostPort      = 0 # ephemeral; resolved by premium_manager Lambda
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "ENV_PREFIX"
          value = var.environment
        },
        {
          name  = "CLOUDWATCH_LOG_GROUP"
          value = "/ecs/${var.environment}-premium-optinist-cloud-taskdef"
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
          value = aws_db_proxy.main.endpoint
        },
        {
          name  = "DB_PORT"
          value = "3306"
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
          name  = "MYSQL_SSL_MODE"
          value = "REQUIRED"
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
          value = local.effective_frontend_domain
        },
        {
          name  = "FRONTEND_SERVER_PORT"
          value = local.effective_frontend_port
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
          name  = "S3_USER_BUCKET_PREFIX"
          value = var.s3_user_bucket_prefix
        },
        {
          name  = "S3_USER_BUCKET_SECRET"
          value = var.s3_user_bucket_secret
        },
        {
          name  = "REMOTE_STORAGE_TYPE"
          value = "2"
        },
        {
          name  = "LOG_LEVEL"
          value = "INFO"
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
          name  = "SUBSCRIPTION_PLANS_CONFIG"
          value = jsonencode(var.subscription_plans)
        },
        {
          name  = "STRIPE_CALLBACK_URL"
          value = "${var.frontend_protocol}://${local.effective_frontend_domain}"
        },
        {
          name  = "STRIPE_SECRET_KEY"
          value = var.stripe_secret_key
        },
        {
          name  = "STRIPE_WEBHOOK_SECRET"
          value = var.stripe_webhook_secret
        },
        {
          name  = "ROUTING_SECRET_KEY"
          value = var.routing_secret_key
        },
        {
          name  = "SKIP_STORAGE_CHECKS"
          value = "false"
        },
        {
          name  = "INTERNAL_API_SECRET"
          value = random_password.internal_api_secret.result
        },
        # Disable scheduler - background jobs run in dedicated background service
        {
          name  = "DISABLE_BACKGROUND_SCHEDULER"
          value = "1"
        },
      ]

      mountPoints = [
        {
          sourceVolume  = "${var.environment}-premium-optinist-cloud-snmk-volume"
          containerPath = "/app/.snakemake"
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
          "awslogs-group"             = "/ecs/${var.environment}-premium-optinist-cloud-taskdef"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"           = "25m"
          "awslogs-region"            = var.aws_region
          "awslogs-create-group"      = "true"
          "awslogs-stream-prefix"     = "ecs"
          "mode"                      = "non-blocking"
        }
      }
    }
  ])

  volume {
    name = "${var.environment}-premium-optinist-cloud-snmk-volume"
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
    Name = "${var.environment}-premium-optinist-cloud-taskdef"
    Tier = "premium"
  }
}

# ===========
# ECS Service
# ===========
resource "aws_ecs_service" "autoscaling" {
  name                               = "${local.env_prefix}-cloud-service"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.autoscaling.arn
  desired_count                      = 1
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 0

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.main.name
    weight            = 1
    base              = 0
  }

  enable_execute_command = true

  load_balancer {
    target_group_arn = aws_lb_target_group.autoscaling.arn
    container_name   = "${local.env_prefix}-cloud-container"
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
    Name = "${local.env_prefix}-cloud-service"
  }
}

# ===========================
# ECS Service Auto Scaling
# ===========================
# DISABLED: Scaling is managed by free_manager Lambda to handle slow startup times
# and user-count based scaling logic. ECS Application Auto Scaling conflicts with
# Lambda-driven scaling and causes race conditions.
#
# resource "aws_appautoscaling_target" "autoscaling_ecs" {
#   max_capacity       = 3
#   min_capacity       = 1
#   resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.autoscaling.name}"
#   scalable_dimension = "ecs:service:DesiredCount"
#   service_namespace  = "ecs"
#
#   depends_on = [aws_ecs_service.autoscaling]
# }
#
# # CPU-based scaling policy
# resource "aws_appautoscaling_policy" "autoscaling_ecs_cpu" {
#   name               = "subscr-optinist-ecs-cpu-scaling"
#   policy_type        = "TargetTrackingScaling"
#   resource_id        = aws_appautoscaling_target.autoscaling_ecs.resource_id
#   scalable_dimension = aws_appautoscaling_target.autoscaling_ecs.scalable_dimension
#   service_namespace  = aws_appautoscaling_target.autoscaling_ecs.service_namespace
#
#   target_tracking_scaling_policy_configuration {
#     predefined_metric_specification {
#       predefined_metric_type = "ECSServiceAverageCPUUtilization"
#     }
#     target_value       = 60.0
#     scale_in_cooldown  = 300
#     scale_out_cooldown = 60
#   }
# }
#
# ===========================================================
# Free-tier outage detection alarms
# ===========================================================
# Mirrors the background_service.tf pattern. Pages when the free-tier
# ECS service has no running task, or when the ALB target group has no
# healthy targets (defence-in-depth against ContainerInsights pipeline lag).

resource "aws_cloudwatch_metric_alarm" "free_tier_running_task_count_zero" {
  alarm_name          = "${local.env_prefix}-free-tier-running-task-count-zero"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "RunningTaskCount"
  namespace           = "ECS/ContainerInsights"
  period              = "300"
  statistic           = "Average"
  threshold           = "1"
  alarm_description   = "Free-tier service has no running task — covers stale ECS agent and ASG replacement gaps."
  alarm_actions       = local.critical_alerts_actions
  ok_actions          = local.critical_alerts_actions
  treat_missing_data  = "breaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.autoscaling.name
  }

  tags = {
    Name = "Free Tier RunningTaskCount Zero Alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "free_tier_alb_no_healthy_targets" {
  alarm_name          = "${local.env_prefix}-free-tier-alb-no-healthy-targets"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "HealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = "60"
  statistic           = "Average"
  threshold           = "1"
  alarm_description   = "Free-tier ALB target group has no healthy targets — defence-in-depth against Container Insights pipeline lag."
  alarm_actions       = local.critical_alerts_actions
  ok_actions          = local.critical_alerts_actions
  treat_missing_data  = "breaching"

  dimensions = {
    LoadBalancer = aws_lb.autoscaling.arn_suffix
    TargetGroup  = aws_lb_target_group.autoscaling.arn_suffix
  }

  tags = {
    Name = "Free Tier ALB No Healthy Targets Alarm"
  }
}

# # Memory-based scaling policy
# resource "aws_appautoscaling_policy" "autoscaling_ecs_memory" {
#   name               = "subscr-optinist-ecs-memory-scaling"
#   policy_type        = "TargetTrackingScaling"
#   resource_id        = aws_appautoscaling_target.autoscaling_ecs.resource_id
#   scalable_dimension = aws_appautoscaling_target.autoscaling_ecs.scalable_dimension
#   service_namespace  = aws_appautoscaling_target.autoscaling_ecs.service_namespace
#
#   target_tracking_scaling_policy_configuration {
#     predefined_metric_specification {
#       predefined_metric_type = "ECSServiceAverageMemoryUtilization"
#     }
#     target_value       = 80.0
#     scale_in_cooldown  = 300
#     scale_out_cooldown = 60
#   }
# }

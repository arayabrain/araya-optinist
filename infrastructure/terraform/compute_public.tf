# ===============================================
# Public Site ECS Infrastructure
# ===============================================
# Dedicated ECS service for public-facing pages:
# - Landing page, login, registration, public data repository
# - Isolated from free tier workloads to ensure public page availability
#
# Routing strategy: JWT header-based
# - Requests WITH Authorization header → Free Tier (via listener rule)
# - Requests WITHOUT Authorization header → Public Site (via default action)
# - Premium users → Premium instances (via existing X-Routing-ID rules)

# ===========================
# Public Site Target Group
# ===========================
resource "aws_lb_target_group" "public" {
  name        = "${local.env_prefix}-public-tg"
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

  # No sticky sessions needed — public pages are stateless
  stickiness {
    type    = "lb_cookie"
    enabled = false
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${local.env_prefix}-public-target-group"
  }
}

# ===========================
# ALB Listener Rules
# ===========================
# Route authenticated requests (with JWT) to Free Tier Target Group.
# Unauthenticated requests fall through to default action → Public Site TG.

resource "aws_lb_listener_rule" "authenticated_to_free_tier" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 50 # After Premium rules (dynamically created, higher priority)

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn
  }

  condition {
    http_header {
      http_header_name = "Authorization"
      values           = ["Bearer *"]
    }
  }

  tags = {
    Name = "${local.env_prefix}-authenticated-rule"
  }
}

# Dev environment HTTP listener rule (only when custom domain is disabled)
resource "aws_lb_listener_rule" "authenticated_to_free_tier_http" {
  count        = var.enable_custom_domain ? 0 : 1
  listener_arn = aws_lb_listener.autoscaling.arn
  priority     = 50

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn
  }

  condition {
    http_header {
      http_header_name = "Authorization"
      values           = ["Bearer *"]
    }
  }

  tags = {
    Name = "${local.env_prefix}-authenticated-rule-http"
  }
}

# ===========================
# Public Site Launch Template
# ===========================
resource "aws_launch_template" "public" {
  name_prefix   = "${local.env_prefix}-public-"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = var.public_instance_type
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 40 # Smaller — public site has minimal local storage needs
      volume_type = "gp3"
      encrypted   = true
    }
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(templatefile("${path.module}/../scripts/ecs-user-data.sh", {
    tier                  = "public"
    cluster_name          = aws_ecs_cluster.main.name
    git_branch            = var.git_branch
    git_repo              = var.git_repo
    firebase_config_json  = var.firebase_config_json
    firebase_private_json = var.firebase_private_json
    ecr_registry          = split("/", var.ecr_repository_url)[0]
    ecr_repository_url    = var.ecr_repository_url
    efs_id                = aws_efs_file_system.snmk.id
    db_host               = replace(aws_db_instance.main.endpoint, ":3306", "")
    swap_size_mb          = 4096 # 4GB swap to compensate for t3.small 2GB RAM
  }))

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "${local.env_prefix}-public-instance"
      Type    = "ECS-Public"
      Tier    = "public"
      Service = "public"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ===========================
# Public Site Auto Scaling Group
# ===========================
resource "aws_autoscaling_group" "public" {
  name                      = "${local.env_prefix}-public-asg"
  vpc_zone_identifier       = [aws_subnet.private1.id, aws_subnet.private2.id]
  target_group_arns         = [aws_lb_target_group.public.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 900
  default_cooldown          = 300

  min_size         = var.public_asg_min_size
  max_size         = var.public_asg_max_size
  desired_capacity = var.public_asg_desired_capacity

  launch_template {
    id      = aws_launch_template.public.id
    version = "$Latest"
  }

  force_delete              = true
  termination_policies      = ["OldestInstance"]
  wait_for_capacity_timeout = "0"
  protect_from_scale_in     = false

  enabled_metrics = [
    "GroupMinSize",
    "GroupMaxSize",
    "GroupDesiredCapacity",
    "GroupInServiceInstances",
    "GroupTotalInstances",
  ]

  tag {
    key                 = "Name"
    value               = "${local.env_prefix}-public-asg-instance"
    propagate_at_launch = true
  }

  tag {
    key                 = "Service"
    value               = "public"
    propagate_at_launch = true
  }

  tag {
    key                 = "Tier"
    value               = "public"
    propagate_at_launch = true
  }

  tag {
    key                 = "LaunchTemplateVersion"
    value               = aws_launch_template.public.latest_version
    propagate_at_launch = true
  }

  tag {
    key                 = "Environment"
    value               = var.environment
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
}

# ===========================
# Public Site Capacity Provider
# ===========================
resource "aws_ecs_capacity_provider" "public" {
  name = "${local.env_prefix}-public-capacity-provider"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.public.arn
    managed_termination_protection = "DISABLED"

    managed_scaling {
      status                    = "DISABLED"
      maximum_scaling_step_size = 1
      minimum_scaling_step_size = 1
      target_capacity           = 90
      instance_warmup_period    = 300
    }
  }

  depends_on = [
    aws_autoscaling_group.public,
    aws_launch_template.public
  ]

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${local.env_prefix}-public-capacity-provider"
  }
}

# ===========================
# Public Site Task Definition (Lightweight)
# ===========================
# Dedicated lightweight task definition for t3.small (2GB RAM).
# Public site only serves SPA pages and lightweight public APIs.
resource "aws_ecs_task_definition" "public" {
  family                   = "${local.env_prefix}-public-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = 1024
  memory                   = 1536
  task_role_arn            = aws_iam_role.ecs_task.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name              = "${local.env_prefix}-public-container"
      image             = "${var.ecr_repository_url}:latest"
      cpu               = 896
      memory            = 1536
      memoryReservation = 1024
      essential         = true
      workingDirectory  = "/app"
      entryPoint        = ["/bin/sh", "-c"]
      command           = ["./cloud-startup.sh"]

      linuxParameters = {
        maxSwap    = 4096 # 4GB swap on t3.small
        swappiness = 20
      }

      portMappings = [
        {
          name          = "${local.env_prefix}-public-container-port-8000"
          containerPort = 8000
          hostPort      = 8000
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
          value = "/ecs/${local.env_prefix}-public-taskdef"
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
          name  = "S3_USER_BUCKET_PREFIX"
          value = var.s3_user_bucket_prefix
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
          value = "${var.frontend_protocol}://${var.frontend_domain}"
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
          sourceVolume  = "${local.env_prefix}-public-snmk-volume"
          containerPath = "/app/.snakemake"
          readOnly      = false
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://127.0.0.1:8000/health || exit 1"]
        interval    = 300
        timeout     = 5
        retries     = 3
        startPeriod = 300
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"             = "/ecs/${local.env_prefix}-public-taskdef"
          "mode"                      = "non-blocking"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"           = "25m"
          "awslogs-region"            = var.aws_region
          "awslogs-create-group"      = "true"
          "awslogs-stream-prefix"     = "ecs"
        }
      }
    }
  ])

  volume {
    name = "${local.env_prefix}-public-snmk-volume"
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
    Name = "${local.env_prefix}-public-taskdef"
    Tier = "public"
  }
}

# ===========================
# Public Site ECS Service
# ===========================
resource "aws_ecs_service" "public" {
  name                               = "${local.env_prefix}-public-service"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.public.arn
  desired_count                      = 1
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 0

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.public.name
    weight            = 1
    base              = 0
  }

  enable_execute_command = true

  load_balancer {
    target_group_arn = aws_lb_target_group.public.arn
    container_name   = "${local.env_prefix}-public-container"
    container_port   = 8000
  }

  # Target public tier instances only
  placement_constraints {
    type       = "memberOf"
    expression = "attribute:tier == public"
  }

  health_check_grace_period_seconds = 900

  depends_on = [
    aws_autoscaling_group.public,
    aws_db_instance.main,
    aws_lb.autoscaling,
    aws_lb_listener.autoscaling
  ]

  tags = {
    Name = "${local.env_prefix}-public-service"
    Tier = "public"
  }
}

# ===========================
# CloudWatch Log Group
# ===========================
resource "aws_cloudwatch_log_group" "public_logs" {
  name              = "/ecs/${local.env_prefix}-public-taskdef"
  retention_in_days = 14

  tags = {
    Name = "Public Site Logs"
    Type = "Public-CloudWatch"
  }
}

# ===========================
# CloudWatch Alarms
# ===========================

# Alarm for public site task stopped (service count drops to 0)
resource "aws_cloudwatch_metric_alarm" "public_task_stopped" {
  alarm_name          = "${var.environment}-public-task-stopped"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "RunningTaskCount"
  namespace           = "ECS/ContainerInsights"
  period              = "300" # 5 minutes
  statistic           = "Average"
  threshold           = "1"
  alarm_description   = "Public site service task count dropped below 1 - public pages unavailable"
  alarm_actions       = local.critical_alerts_actions
  ok_actions          = local.critical_alerts_actions

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.public.name
  }

  tags = {
    Name = "Public Site Task Stopped Alarm"
    Type = "Public-CloudWatch"
  }
}

# ===========================
# Outputs
# ===========================
output "public_service_name" {
  description = "Name of the public site ECS service"
  value       = aws_ecs_service.public.name
}

output "public_asg_name" {
  description = "Name of the public site Auto Scaling Group"
  value       = aws_autoscaling_group.public.name
}

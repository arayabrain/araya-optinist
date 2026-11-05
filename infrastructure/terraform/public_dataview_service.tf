# Public DataView tier: the public task (INSTANCE_MODE=public) on its own
# instances and target group. The listener sends the public DataView API here
# (p280, p300), so those reads do not share a process with sign-in (p305,
# p306). The page shell and static files stay on the public tier. Mirrors
# public_service.tf; keep the two in step.

resource "aws_lb_target_group" "public_dataview" {
  # Target group names cap at 32 characters
  name        = "${local.env_prefix}-pubdv-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  # Match the listener idle_timeout so streaming responses survive deregistration.
  deregistration_delay = 600

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = "/health"
    matcher             = "200"
    port                = "traffic-port"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${local.env_prefix}-public-dataview-target-group"
    Tier = "public-dataview"
  }
}

resource "aws_launch_template" "public_dataview" {
  name_prefix   = "${local.env_prefix}-public-dataview-"
  image_id      = local.effective_ami_id
  instance_type = var.public_dataview_instance_type
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  # Root volume sized for OS + image only; the published-data cache lives on EFS.
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

  user_data = base64encode(templatefile("${path.module}/../scripts/ecs-user-data.sh", {
    tier               = "public-dataview"
    cluster_name       = aws_ecs_cluster.main.name
    git_branch         = var.git_branch
    git_repo           = var.git_repo
    ecr_registry       = split("/", local.ecr_repository_url)[0]
    ecr_repository_url = local.ecr_repository_url
    efs_id             = ""
    db_host            = replace(aws_db_instance.main.endpoint, ":3306", "")
    swap_size_mb       = 0
    swap_device_name   = ""
  }))

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name        = "${local.env_prefix}-public-dataview-instance"
      Type        = "ECS-Public-DataView"
      Tier        = "public-dataview"
      Service     = "public-dataview"
      Environment = local.environment_label
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name        = "${local.env_prefix}-public-dataview-vol"
      Environment = local.environment_label
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "public_dataview" {
  name                      = "${local.env_prefix}-public-dataview-asg"
  vpc_zone_identifier       = [aws_subnet.private1.id, aws_subnet.private2.id]
  target_group_arns         = [aws_lb_target_group.public_dataview.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 900
  default_cooldown          = 300

  min_size         = var.public_dataview_asg_min_size
  max_size         = var.public_dataview_asg_max_size
  desired_capacity = var.public_dataview_asg_desired_capacity

  launch_template {
    id      = aws_launch_template.public_dataview.id
    version = "$Latest"
  }

  force_delete              = false
  termination_policies      = ["OldestInstance"]
  wait_for_capacity_timeout = "0"

  tag {
    key                 = "Name"
    value               = "${local.env_prefix}-public-dataview-asg-instance"
    propagate_at_launch = true
  }

  tag {
    key                 = "Tier"
    value               = "public-dataview"
    propagate_at_launch = true
  }

  lifecycle {
    create_before_destroy = true
    ignore_changes        = [desired_capacity]
  }
}

resource "aws_ecs_task_definition" "public_dataview" {
  family                   = "${var.environment}-public-dataview-optinist-cloud-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = 1024
  memory                   = 1280
  task_role_arn            = aws_iam_role.ecs_task.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      # Name must match aws_ecs_service.public_dataview.load_balancer.container_name.
      name              = "${local.env_prefix}-cloud-container"
      image             = "${local.ecr_repository_url}:latest"
      cpu               = 1024
      memory            = 1280
      memoryReservation = 1024
      essential         = true
      workingDirectory  = "/app"
      entryPoint        = ["/bin/sh", "-c"]
      command           = ["./cloud-startup.sh"]

      portMappings = [
        {
          name          = "${local.env_prefix}-public-dataview-container-port-8000"
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]

      # The public task's environment, with this tier's own log group
      environment = [
        for entry in local.public_task_environment :
        entry.name == "CLOUDWATCH_LOG_GROUP"
        ? { name = entry.name, value = aws_cloudwatch_log_group.public_dataview_optinist.name }
        : entry
      ]

      # The public tier's EFS caches, so both tiers serve one published copy
      mountPoints = [
        {
          sourceVolume  = "${local.env_prefix}-public-published-data-volume"
          containerPath = "/app/studio_data/output"
          readOnly      = false
        },
        {
          sourceVolume  = "${local.env_prefix}-public-input-cache-volume"
          containerPath = "/app/studio_data/input"
          readOnly      = false
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://127.0.0.1:8000/health || exit 1"]
        interval    = 60
        timeout     = 5
        retries     = 3
        startPeriod = 180
      }

      dockerLabels = {
        "health.check.enabled" = "true"
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"             = aws_cloudwatch_log_group.public_dataview_optinist.name
          "awslogs-multiline-pattern" = "^\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
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
    name = "${local.env_prefix}-public-published-data-volume"
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

  volume {
    name = "${local.env_prefix}-public-input-cache-volume"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.published_data.id
      root_directory     = "/"
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.published_data_input.id
        iam             = "DISABLED"
      }
    }
  }

  tags = {
    Name = "${var.environment}-public-dataview-optinist-cloud-taskdef"
    Tier = "public-dataview"
  }
}

resource "aws_ecs_service" "public_dataview" {
  name            = "${var.environment}-public-dataview-optinist-cloud-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.public_dataview.arn
  desired_count   = var.public_dataview_asg_desired_capacity
  # An instance holds one task (host port 8000), so a lone task is replaced
  # stop-first: its target drains (deregistration_delay) before the new task
  # can start. With two or more, one keeps serving as on the public service
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = var.public_dataview_asg_desired_capacity > 1 ? 50 : 0

  launch_type = "EC2"

  enable_execute_command = true

  placement_constraints {
    type       = "memberOf"
    expression = "attribute:tier == public-dataview"
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.public_dataview.arn
    container_name   = "${local.env_prefix}-cloud-container"
    container_port   = 8000
  }

  health_check_grace_period_seconds = 900

  # The listener rules: CreateService refuses a target group that no rule or
  # listener forwards to, and the rules reference the target group, not this
  depends_on = [
    aws_autoscaling_group.public_dataview,
    aws_db_instance.main,
    aws_lb_listener_rule.visualizations_public_header,
    aws_lb_listener_rule.public_dataview_api,
  ]

  tags = {
    Name = "${var.environment}-public-dataview-optinist-cloud-service"
    Tier = "public-dataview"
  }
}

resource "aws_cloudwatch_log_group" "public_dataview_optinist" {
  name              = "/ecs/${var.environment}-public-dataview-optinist-cloud-taskdef"
  retention_in_days = 30

  tags = {
    Name = "Public DataView Service Logs"
    Type = "Public-DataView-CloudWatch"
  }
}

output "public_dataview_service_name" {
  description = "Name of the public DataView ECS service"
  value       = aws_ecs_service.public_dataview.name
}

# wait_for_capacity_timeout=0 means `terraform apply` returns before targets
# are healthy; until they are, public DataView answers 503.
output "public_dataview_tg_health_check_command" {
  description = "Run this to confirm public DataView targets are healthy"
  value       = "aws elbv2 describe-target-health --target-group-arn ${aws_lb_target_group.public_dataview.arn} --query 'TargetHealthDescriptions[*].[Target.Id,TargetHealth.State]' --output table"
}

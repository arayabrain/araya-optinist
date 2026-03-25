# ===============================================
# Development Environment Scheduled Start/Stop
# ===============================================
# Saves ~46% on compute costs by running dev resources
# only during business hours (08:00-22:00 JST, Mon-Fri).
#
# Resources managed:
#   - Free tier ASG (scale 0 <-> 1)
#   - Background service EC2 instance (stop/start)
#   - Premium EC2 instances (stop/start)
#   - NAT instance (stop/start)
#   - RDS instance (destroy with snapshot / restore from snapshot)
#   - Lambda schedule rules (disable/enable)
#   - CloudWatch alarm actions (disable/enable)
#
# Manual override (skip next scheduled stop):
#   aws ssm put-parameter \
#     --name /<env>/optinist/schedule-override \
#     --value on --type String --overwrite
#
# Manual start (after-hours / weekends):
#   aws lambda invoke --function-name <env>-dev-scheduler \
#     --payload '{"action":"start"}' /dev/stdout
#
# Only created when var.enable_dev_schedule = true

# ===========================
# SSM Parameter for Override
# ===========================
resource "aws_ssm_parameter" "dev_schedule_override" {
  count = var.enable_dev_schedule ? 1 : 0

  name  = "/${var.environment}/optinist/schedule-override"
  type  = "String"
  value = "off"

  tags = {
    Name    = "Dev Schedule Override"
    Service = "dev-scheduler"
  }

  lifecycle {
    ignore_changes = [value]
  }
}

# ===========================
# Lambda Package
# ===========================
data "archive_file" "dev_scheduler_zip" {
  count = var.enable_dev_schedule ? 1 : 0

  type        = "zip"
  source_dir  = "${path.module}/dev_scheduler_package"
  output_path = "${path.module}/dev_scheduler.py.zip"
}

# ===========================
# Lambda Function
# ===========================
resource "aws_lambda_function" "dev_scheduler" {
  count = var.enable_dev_schedule ? 1 : 0

  filename                       = "${path.module}/dev_scheduler.py.zip"
  function_name                  = "${var.environment}-dev-scheduler"
  role                           = aws_iam_role.dev_scheduler[0].arn
  handler                        = "dev_scheduler.handler"
  runtime                        = "python3.11"
  timeout                        = 900
  reserved_concurrent_executions = 1
  source_code_hash               = data.archive_file.dev_scheduler_zip[0].output_base64sha256

  environment {
    variables = {
      RDS_INSTANCE_ID          = aws_db_instance.main.identifier
      RDS_SNAPSHOT_ID          = "${aws_db_instance.main.identifier}-dev-scheduler"
      RDS_INSTANCE_CLASS       = aws_db_instance.main.instance_class
      RDS_SUBNET_GROUP_NAME    = aws_db_subnet_group.main.name
      RDS_SECURITY_GROUP_IDS   = aws_security_group.rds.id
      RDS_PARAMETER_GROUP_NAME = aws_db_parameter_group.main.name
      NAT_INSTANCE_ID          = aws_instance.nat.id
      BACKGROUND_INSTANCE_ID = aws_instance.background.id
      PREMIUM_INSTANCE_IDS   = join(",", aws_instance.premium[*].id)
      ASG_NAME               = aws_autoscaling_group.main.name
      ASG_MIN_SIZE           = tostring(var.asg_min_size)
      ASG_MAX_SIZE           = tostring(var.asg_max_size)
      ASG_DESIRED_CAPACITY   = tostring(var.asg_desired_capacity)
      CLUSTER_NAME           = aws_ecs_cluster.main.name
      OVERRIDE_PARAM_NAME           = "/${var.environment}/optinist/schedule-override"
      ALARM_PREFIX                  = "${var.environment}-"
      PREMIUM_MANAGER_FUNCTION_NAME = aws_lambda_function.premium_manager.function_name
      DEFAULT_STOP_MODE             = var.dev_schedule_stop_mode

      # Rules enabled immediately on start
      SCHEDULE_RULE_NAMES = jsonencode([
        aws_cloudwatch_event_rule.free_manager_schedule.name,
        aws_cloudwatch_event_rule.free_manager_asg_events.name,
        aws_cloudwatch_event_rule.cost_tracker_schedule.name,
      ])

      # Rules enabled only on verify-start (+15 min) to let instances boot
      # before premium_manager starts monitoring
      DELAYED_RULE_NAMES = jsonencode([
        aws_cloudwatch_event_rule.premium_manager_schedule.name,
        aws_cloudwatch_event_rule.premium_cleanup_schedule.name,
      ])

      ECS_SERVICE_NAMES = jsonencode([
        aws_ecs_service.autoscaling.name,
        aws_ecs_service.premium.name,
        aws_ecs_service.background.name,
      ])
    }
  }

  tags = {
    Name    = "Dev Scheduler Lambda"
    Service = "dev-scheduler"
  }

  depends_on = [
    aws_iam_role_policy.dev_scheduler_permissions,
    aws_cloudwatch_log_group.dev_scheduler_logs[0],
    data.archive_file.dev_scheduler_zip[0],
  ]
}

# ===========================
# CloudWatch Log Group
# ===========================
resource "aws_cloudwatch_log_group" "dev_scheduler_logs" {
  count = var.enable_dev_schedule ? 1 : 0

  name              = "/aws/lambda/${var.environment}-dev-scheduler"
  retention_in_days = 14

  tags = {
    Name    = "Dev Scheduler Logs"
    Service = "dev-scheduler"
  }
}

# ===========================
# IAM Role
# ===========================
resource "aws_iam_role" "dev_scheduler" {
  count = var.enable_dev_schedule ? 1 : 0

  name = "${var.environment}-dev-scheduler-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })

  tags = {
    Name    = "Dev Scheduler Lambda Role"
    Service = "dev-scheduler"
  }
}

resource "aws_iam_role_policy_attachment" "dev_scheduler_basic" {
  count = var.enable_dev_schedule ? 1 : 0

  role       = aws_iam_role.dev_scheduler[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "dev_scheduler_permissions" {
  count = var.enable_dev_schedule ? 1 : 0

  name = "${var.environment}-dev-scheduler-permissions"
  role = aws_iam_role.dev_scheduler[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # RDS stop/destroy/restore (scoped to specific instance)
      {
        Effect = "Allow"
        Action = [
          "rds:DeleteDBInstance",
          "rds:DescribeDBInstances",
          "rds:RestoreDBInstanceFromDBSnapshot",
          "rds:AddTagsToResource",
          "rds:StartDBInstance",
          "rds:StopDBInstance",
        ]
        Resource = aws_db_instance.main.arn
      },
      # RDS snapshot management (scoped to environment snapshots)
      {
        Effect = "Allow"
        Action = [
          "rds:DeleteDBSnapshot",
          "rds:DescribeDBSnapshots",
        ]
        Resource = "arn:aws:rds:${var.aws_region}:${data.aws_caller_identity.current.account_id}:snapshot:${aws_db_instance.main.identifier}-dev-scheduler"
      },
      # RDS read-only describe for subnet/parameter groups (requires wildcard)
      {
        Effect = "Allow"
        Action = [
          "rds:DescribeDBSubnetGroups",
          "rds:DescribeDBParameterGroups",
        ]
        Resource = "*"
      },
      # EC2 stop/start for NAT and background instances
      {
        Effect = "Allow"
        Action = [
          "ec2:StartInstances",
          "ec2:StopInstances",
        ]
        Resource = [
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${aws_instance.nat.id}",
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${aws_instance.background.id}",
        ]
      },
      # EC2 stop/start for premium instances (by tag)
      {
        Effect = "Allow"
        Action = [
          "ec2:StartInstances",
          "ec2:StopInstances",
        ]
        Resource = "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/Service" = "premium-tier"
          }
        }
      },
      # EC2 describe (read-only, requires wildcard)
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus",
        ]
        Resource = "*"
      },
      # ASG management (scoped to specific ASG)
      {
        Effect = "Allow"
        Action = [
          "autoscaling:UpdateAutoScalingGroup",
        ]
        Resource = aws_autoscaling_group.main.arn
      },
      # EventBridge rule enable/disable (scoped to environment prefix)
      {
        Effect = "Allow"
        Action = [
          "events:EnableRule",
          "events:DisableRule",
        ]
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/${var.environment}-*"
      },
      # CloudWatch alarm actions (requires wildcard)
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:DescribeAlarms",
          "cloudwatch:DisableAlarmActions",
          "cloudwatch:EnableAlarmActions",
        ]
        Resource = "*"
      },
      # SSM parameter for override
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:PutParameter",
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/${var.environment}/optinist/schedule-override"
      },
      # Invoke premium_manager to clean up dynamic instances before stop
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.premium_manager.arn
      },
      # ECS service scaling (set desired_count 0 on stop, 1 on start)
      {
        Effect = "Allow"
        Action = "ecs:UpdateService"
        Resource = [
          aws_ecs_service.autoscaling.id,
          aws_ecs_service.premium.id,
          aws_ecs_service.background.id,
        ]
      },
    ]
  })
}

# ===========================
# EventBridge: Morning Start
# ===========================
# 08:00 JST Mon-Fri = 23:00 UTC Sun-Thu
resource "aws_cloudwatch_event_rule" "dev_schedule_start" {
  count = var.enable_dev_schedule ? 1 : 0

  name                = "${var.environment}-dev-schedule-start"
  description         = "Start dev environment at 08:00 JST Mon-Fri"
  schedule_expression = "cron(0 23 ? * SUN-THU *)"
  state               = "ENABLED"

  tags = {
    Name    = "Dev Schedule Start"
    Service = "dev-scheduler"
  }
}

resource "aws_cloudwatch_event_target" "dev_schedule_start" {
  count = var.enable_dev_schedule ? 1 : 0

  rule      = aws_cloudwatch_event_rule.dev_schedule_start[0].name
  target_id = "DevSchedulerStart"
  arn       = aws_lambda_function.dev_scheduler[0].arn

  input = jsonencode({
    action = "start"
  })
}

resource "aws_lambda_permission" "allow_eventbridge_dev_start" {
  count = var.enable_dev_schedule ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridgeStart"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dev_scheduler[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.dev_schedule_start[0].arn
}

# ===========================
# EventBridge: Evening Stop
# ===========================
# 22:00 JST Mon-Fri = 13:00 UTC Mon-Fri
resource "aws_cloudwatch_event_rule" "dev_schedule_stop" {
  count = var.enable_dev_schedule ? 1 : 0

  name                = "${var.environment}-dev-schedule-stop"
  description         = "Stop dev environment at 22:00 JST Mon-Fri"
  schedule_expression = "cron(0 13 ? * MON-FRI *)"
  state               = "ENABLED"

  tags = {
    Name    = "Dev Schedule Stop"
    Service = "dev-scheduler"
  }
}

resource "aws_cloudwatch_event_target" "dev_schedule_stop" {
  count = var.enable_dev_schedule ? 1 : 0

  rule      = aws_cloudwatch_event_rule.dev_schedule_stop[0].name
  target_id = "DevSchedulerStop"
  arn       = aws_lambda_function.dev_scheduler[0].arn

  input = jsonencode({
    action    = "stop"
    stop_mode = var.dev_schedule_stop_mode
  })
}

resource "aws_lambda_permission" "allow_eventbridge_dev_stop" {
  count = var.enable_dev_schedule ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridgeStop"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dev_scheduler[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.dev_schedule_stop[0].arn
}

# ===========================
# EventBridge: Verify Start
# ===========================
# 08:15 JST Mon-Fri = 23:15 UTC Sun-Thu
# Re-invokes start 15 min after the scheduled start to catch timeouts/crashes.
# All start operations are idempotent, so re-running is safe.
resource "aws_cloudwatch_event_rule" "dev_schedule_verify_start" {
  count = var.enable_dev_schedule ? 1 : 0

  name                = "${var.environment}-dev-schedule-verify-start"
  description         = "Verify dev environment start completed at 08:15 JST Mon-Fri"
  schedule_expression = "cron(15 23 ? * SUN-THU *)"
  state               = "ENABLED"

  tags = {
    Name    = "Dev Schedule Verify Start"
    Service = "dev-scheduler"
  }
}

resource "aws_cloudwatch_event_target" "dev_schedule_verify_start" {
  count = var.enable_dev_schedule ? 1 : 0

  rule      = aws_cloudwatch_event_rule.dev_schedule_verify_start[0].name
  target_id = "DevSchedulerVerifyStart"
  arn       = aws_lambda_function.dev_scheduler[0].arn

  input = jsonencode({
    action = "start"
  })
}

resource "aws_lambda_permission" "allow_eventbridge_dev_verify_start" {
  count = var.enable_dev_schedule ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridgeVerifyStart"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dev_scheduler[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.dev_schedule_verify_start[0].arn
}

# ===========================
# EventBridge: Verify Stop
# ===========================
# 22:15 JST Mon-Fri = 13:15 UTC Mon-Fri
# Re-invokes stop 15 min after the scheduled stop to catch timeouts/crashes.
# All stop operations are idempotent, so re-running is safe.
resource "aws_cloudwatch_event_rule" "dev_schedule_verify_stop" {
  count = var.enable_dev_schedule ? 1 : 0

  name                = "${var.environment}-dev-schedule-verify-stop"
  description         = "Verify dev environment stop completed at 22:15 JST Mon-Fri"
  schedule_expression = "cron(15 13 ? * MON-FRI *)"
  state               = "ENABLED"

  tags = {
    Name    = "Dev Schedule Verify Stop"
    Service = "dev-scheduler"
  }
}

resource "aws_cloudwatch_event_target" "dev_schedule_verify_stop" {
  count = var.enable_dev_schedule ? 1 : 0

  rule      = aws_cloudwatch_event_rule.dev_schedule_verify_stop[0].name
  target_id = "DevSchedulerVerifyStop"
  arn       = aws_lambda_function.dev_scheduler[0].arn

  input = jsonencode({
    action    = "stop"
    stop_mode = var.dev_schedule_stop_mode
  })
}

resource "aws_lambda_permission" "allow_eventbridge_dev_verify_stop" {
  count = var.enable_dev_schedule ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridgeVerifyStop"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dev_scheduler[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.dev_schedule_verify_stop[0].arn
}

# ===========================
# Outputs
# ===========================
output "dev_scheduler_lambda_name" {
  description = "Name of the dev scheduler Lambda (use for manual invocation)"
  value       = var.enable_dev_schedule ? aws_lambda_function.dev_scheduler[0].function_name : null
}

output "dev_schedule_override_param" {
  description = "SSM parameter name for manual override"
  value       = var.enable_dev_schedule ? aws_ssm_parameter.dev_schedule_override[0].name : null
}

output "dev_schedule_info" {
  description = "Dev schedule configuration"
  value = var.enable_dev_schedule ? {
    start_time     = "08:00 JST (23:00 UTC) Mon-Fri"
    stop_time      = "22:00 JST (13:00 UTC) Mon-Fri"
    stop_mode      = var.dev_schedule_stop_mode
    weekends       = "Stopped (Fri 22:00 -> Mon 08:00 JST)"
    override       = "aws ssm put-parameter --name /${var.environment}/optinist/schedule-override --value on --type String --overwrite"
    manual_start   = "aws lambda invoke --function-name ${var.environment}-dev-scheduler --payload '{\"action\":\"start\"}' /dev/stdout"
    manual_stop    = "aws lambda invoke --function-name ${var.environment}-dev-scheduler --payload '{\"action\":\"stop\",\"stop_mode\":\"stop\"}' /dev/stdout"
    manual_destroy = "aws lambda invoke --function-name ${var.environment}-dev-scheduler --payload '{\"action\":\"stop\",\"stop_mode\":\"destroy\"}' /dev/stdout"
  } : null
}

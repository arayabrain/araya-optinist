# =============================================================================
# Public cleanup Lambda
# Scheduled cleanup tasks for the public tier. Currently wipes the on-demand
# raw-input cache on EFS (pure cache, re-synced on access) to bound EFS growth
# without touching the output cache (mounts only the /input-cache access point).
# =============================================================================

data "archive_file" "public_cleanup_zip" {
  type        = "zip"
  source_file = "${path.module}/public_cleanup.py"
  output_path = "${path.module}/public_cleanup.py.zip"
}

resource "aws_lambda_function" "public_cleanup" {
  filename         = data.archive_file.public_cleanup_zip.output_path
  function_name    = "${var.environment}-public-cleanup"
  role             = aws_iam_role.public_cleanup_lambda.arn
  handler          = "public_cleanup.handler"
  runtime          = "python3.11"
  source_code_hash = data.archive_file.public_cleanup_zip.output_base64sha256

  # Deletes are one EFS round-trip per file, so a large day's cache can outrun a
  # short timeout; use the Lambda max for headroom.
  timeout = 900

  environment {
    variables = {
      INPUT_CACHE_PATH = "/mnt/input"
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  file_system_config {
    arn              = aws_efs_access_point.published_data_input.arn
    local_mount_path = "/mnt/input"
  }

  tags = {
    Name    = "Public Cleanup Lambda"
    Type    = "Public-Lambda"
    Service = "public"
  }

  depends_on = [
    aws_iam_role_policy_attachment.public_cleanup_vpc,
    aws_iam_role_policy.public_cleanup_efs,
    aws_cloudwatch_log_group.public_cleanup_logs,
    aws_efs_mount_target.published_data_private1,
    aws_efs_mount_target.published_data_private2,
  ]
}

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------
resource "aws_iam_role" "public_cleanup_lambda" {
  name = "${var.environment}-public-cleanup-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
      }
    ]
  })

  tags = {
    Name = "Public Cleanup Lambda Role"
    Type = "Public-IAM"
  }
}

# VPC ENI management + CloudWatch Logs.
resource "aws_iam_role_policy_attachment" "public_cleanup_vpc" {
  role       = aws_iam_role.public_cleanup_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# EFS access, scoped to the input access point only.
resource "aws_iam_role_policy" "public_cleanup_efs" {
  name = "${var.environment}-public-cleanup-efs"
  role = aws_iam_role.public_cleanup_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
        ]
        Resource = aws_efs_file_system.published_data.arn
        Condition = {
          StringEquals = {
            "elasticfilesystem:AccessPointArn" = aws_efs_access_point.published_data_input.arn
          }
        }
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "public_cleanup_logs" {
  name              = "/aws/lambda/${var.environment}-public-cleanup"
  retention_in_days = 30

  tags = {
    Name = "Public Cleanup Logs"
    Type = "Public-CloudWatch"
  }
}

# Fires on a failed run: Lambda crash/timeout, or the handler raising on a
# non-zero delete-error count. notBreaching covers the gaps between daily runs.
resource "aws_cloudwatch_metric_alarm" "public_cleanup_errors" {
  alarm_name          = "${var.environment}-public-cleanup-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = "86400"
  statistic           = "Sum"
  threshold           = "0"
  alarm_description   = "Public cleanup Lambda reported errors on its last run"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.critical_alerts_actions
  ok_actions          = local.critical_alerts_actions

  dimensions = {
    FunctionName = aws_lambda_function.public_cleanup.function_name
  }

  tags = {
    Name    = "Public Cleanup Errors Alarm"
    Type    = "Public-CloudWatch"
    Service = "public"
  }
}

# ---------------------------------------------------------------------------
# Schedule: 19:00 UTC = 04:00 JST, daily.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "public_cleanup_schedule" {
  name                = "${var.environment}-public-cleanup-schedule"
  description         = "Daily 04:00 JST public-tier cleanup (input cache wipe)"
  schedule_expression = "cron(0 19 * * ? *)"
  state               = "ENABLED"

  tags = {
    Name    = "Public Cleanup Schedule"
    Type    = "Public-CloudWatch"
    Service = "public"
  }
}

resource "aws_cloudwatch_event_target" "public_cleanup_target" {
  rule      = aws_cloudwatch_event_rule.public_cleanup_schedule.name
  target_id = "PublicCleanupTarget"
  arn       = aws_lambda_function.public_cleanup.arn
}

resource "aws_lambda_permission" "allow_cloudwatch_public_cleanup" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.public_cleanup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.public_cleanup_schedule.arn
}

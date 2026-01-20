# ===============================================
# Storage Reconciliation Lambda Infrastructure
# ===============================================
# This file contains all infrastructure for the Storage Reconciliation Lambda:
# - Lambda function for periodic storage reconciliation
# - Reconciles incremental tracking with actual S3 storage
# - CloudWatch Events for hourly execution
# - IAM roles and permissions
# - CloudWatch monitoring

# ===========================
# Storage Reconciliation Lambda Package
# ===========================

# Install dependencies
resource "null_resource" "install_storage_reconciliation_dependencies" {
  provisioner "local-exec" {
    command = <<-EOT
      mkdir -p ${path.module}/storage_reconciliation_package
      /usr/bin/python3 -m pip install pymysql boto3 -t ${path.module}/storage_reconciliation_package/ --no-cache-dir
    EOT
  }

  triggers = {
    code_changes = filesha256("${path.module}/storage_reconciliation_package/storage_reconciliation.py")
  }
}

# Create ZIP using archive_file
data "archive_file" "storage_reconciliation_zip" {
  type        = "zip"
  source_dir  = "${path.module}/storage_reconciliation_package"
  output_path = "${path.module}/storage_reconciliation.py.zip"

  depends_on = [null_resource.install_storage_reconciliation_dependencies]
}

# ===========================
# Storage Reconciliation Lambda Function
# ===========================

resource "aws_lambda_function" "storage_reconciliation" {
  filename      = "${path.module}/storage_reconciliation.py.zip"
  function_name = "subscr-storage-reconciliation"
  role          = aws_iam_role.storage_reconciliation_lambda.arn
  handler       = "storage_reconciliation.handler"
  runtime       = "python3.9"
  timeout       = 900 # 15 minutes - enough time for large batches
  layers        = [aws_lambda_layer_version.aws_constants.arn]

  source_code_hash = data.archive_file.storage_reconciliation_zip.output_base64sha256

  environment {
    variables = {
      # Database configuration
      RDS_HOST     = aws_db_proxy.main.endpoint
      RDS_USER     = var.mysql_user
      RDS_PASSWORD = var.mysql_password
      RDS_DATABASE = var.mysql_database

      # S3 configuration
      S3_DEFAULT_BUCKET_NAME = aws_s3_bucket.app_storage.id
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  tags = {
    Name    = "Storage Reconciliation Lambda"
    Type    = "Storage-Lambda"
    Service = "storage-reconciliation"
  }

  depends_on = [
    aws_iam_role_policy_attachment.storage_reconciliation_lambda_basic,
    aws_cloudwatch_log_group.storage_reconciliation_logs,
    data.archive_file.storage_reconciliation_zip
  ]
}

# ===========================
# IAM Role for Storage Reconciliation Lambda
# ===========================

resource "aws_iam_role" "storage_reconciliation_lambda" {
  name = "subscr-storage-reconciliation-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "Storage Reconciliation Lambda Role"
    Type = "Storage-IAM"
  }
}

# Basic Lambda execution policy (includes VPC access)
resource "aws_iam_role_policy_attachment" "storage_reconciliation_lambda_basic" {
  role       = aws_iam_role.storage_reconciliation_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Custom policy for S3 and CloudWatch access
resource "aws_iam_role_policy" "storage_reconciliation_lambda_policy" {
  name = "subscr-storage-reconciliation-lambda-policy"
  role = aws_iam_role.storage_reconciliation_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # S3 read access for storage scanning
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:ListBucketVersions"
        ]
        Resource = [
          aws_s3_bucket.app_storage.arn,
          "${aws_s3_bucket.app_storage.arn}/*"
        ]
      },
      # CloudWatch metrics (requires wildcard)
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
          "cloudwatch:GetMetricData",
          "cloudwatch:GetMetricStatistics"
        ]
        Resource = "*"
      }
    ]
  })
}

# ===========================
# CloudWatch Log Group
# ===========================

resource "aws_cloudwatch_log_group" "storage_reconciliation_logs" {
  name              = "/aws/lambda/subscr-storage-reconciliation"
  retention_in_days = 14

  tags = {
    Name = "Storage Reconciliation Logs"
    Type = "Storage-CloudWatch"
  }
}

# ===========================
# CloudWatch Events (Hourly Execution)
# ===========================

# CloudWatch Events Rule (every 1 hour)
resource "aws_cloudwatch_event_rule" "storage_reconciliation_schedule" {
  name                = "subscr-storage-reconciliation-schedule"
  description         = "Trigger storage reconciliation every hour to sync incremental tracking with S3"
  schedule_expression = "rate(1 hour)"
  state               = "ENABLED"

  tags = {
    Name    = "Storage Reconciliation Schedule"
    Type    = "Storage-CloudWatch"
    Service = "storage-reconciliation"
  }
}

# CloudWatch Events Target
resource "aws_cloudwatch_event_target" "storage_reconciliation_target" {
  rule      = aws_cloudwatch_event_rule.storage_reconciliation_schedule.name
  target_id = "StorageReconciliationTarget"
  arn       = aws_lambda_function.storage_reconciliation.arn

  input = jsonencode({
    source      = "aws.events"
    detail-type = "Scheduled Event"
    detail = {
      action = "reconcile_storage"
    }
  })
}

# Lambda Permission for CloudWatch Events
resource "aws_lambda_permission" "allow_cloudwatch_storage_reconciliation" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.storage_reconciliation.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.storage_reconciliation_schedule.arn
}

# ===========================
# CloudWatch Alarms
# ===========================

# Alarm for Lambda errors
resource "aws_cloudwatch_metric_alarm" "storage_reconciliation_errors" {
  alarm_name          = "subscr-storage-reconciliation-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = "3600" # 1 hour
  statistic           = "Sum"
  threshold           = "0"
  alarm_description   = "Storage reconciliation Lambda errors detected"

  dimensions = {
    FunctionName = aws_lambda_function.storage_reconciliation.function_name
  }

  tags = {
    Name = "Storage Reconciliation Errors"
    Type = "Storage-CloudWatch"
  }
}

# Alarm for Lambda duration (warn if taking too long)
resource "aws_cloudwatch_metric_alarm" "storage_reconciliation_duration" {
  alarm_name          = "subscr-storage-reconciliation-duration-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = "3600" # 1 hour
  statistic           = "Maximum"
  threshold           = "600000" # 10 minutes (warn before 15 min timeout)
  alarm_description   = "Storage reconciliation taking longer than expected"

  dimensions = {
    FunctionName = aws_lambda_function.storage_reconciliation.function_name
  }

  tags = {
    Name = "Storage Reconciliation Duration"
    Type = "Storage-CloudWatch"
  }
}

# ===========================
# Outputs
# ===========================

output "storage_reconciliation_lambda_name" {
  description = "Name of the storage reconciliation Lambda function"
  value       = aws_lambda_function.storage_reconciliation.function_name
}

output "storage_reconciliation_lambda_arn" {
  description = "ARN of the storage reconciliation Lambda function"
  value       = aws_lambda_function.storage_reconciliation.arn
}

output "storage_reconciliation_triggers" {
  description = "Storage reconciliation Lambda trigger configuration"
  value = {
    scheduled_rule = aws_cloudwatch_event_rule.storage_reconciliation_schedule.name
    schedule       = aws_cloudwatch_event_rule.storage_reconciliation_schedule.schedule_expression
  }
}

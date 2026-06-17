# ===============================================
# Common User Manager Lambda Infrastructure
# ===============================================
# This file contains all infrastructure for the Common User Manager Lambda system:
# - Lambda function for shared user lifecycle operations
# - Heartbeat-based inactivity logout (both free and premium users)
# - Workflow crash recovery
# - CloudWatch Events for periodic execution
# - IAM roles and permissions
# - CloudWatch monitoring

# ===========================
# Common User Manager Lambda Package
# ===========================

resource "null_resource" "install_common_user_manager_dependencies" {
  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/common_user_manager && \
      mkdir -p ${path.module}/.build/common_user_manager && \
      cp -p ${path.module}/common_user_manager_package/*.py ${path.module}/.build/common_user_manager/ && \
      /usr/bin/python3 -m pip install -r ${path.module}/common_user_manager_package/requirements.txt -t ${path.module}/.build/common_user_manager --no-cache-dir && \
      touch ${path.module}/.build/common_user_manager/.installed
    EOT
  }

  triggers = {
    code_changes = sha256(join("", [
      for f in fileset("${path.module}/common_user_manager_package", "*.py") :
      filesha256("${path.module}/common_user_manager_package/${f}")
    ]))
    requirements_changes = filesha256("${path.module}/common_user_manager_package/requirements.txt")
    installed_marker     = fileexists("${path.module}/.build/common_user_manager/.installed") ? "present" : "missing"
  }
}

# Create ZIP using archive_file
data "archive_file" "common_user_manager_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/common_user_manager"
  output_path = "${path.module}/common_user_manager.py.zip"

  depends_on = [null_resource.install_common_user_manager_dependencies]
}

# ===========================
# Common User Manager Lambda Function
# ===========================

resource "aws_lambda_function" "common_user_manager" {
  filename      = "${path.module}/common_user_manager.py.zip"
  function_name = "${var.environment}-common-user-manager"
  role          = aws_iam_role.common_user_manager_lambda.arn
  handler       = "common_user_manager.handler"
  runtime       = "python3.11"
  timeout       = 900 # 15 minutes
  layers        = [aws_lambda_layer_version.aws_constants.arn]

  source_code_hash = data.archive_file.common_user_manager_zip.output_base64sha256

  environment {
    variables = {
      # Database configuration
      RDS_HOST     = aws_db_proxy.main.endpoint
      RDS_USER     = var.mysql_user
      RDS_PASSWORD = var.mysql_password
      RDS_DATABASE = var.mysql_database

      # User inactivity timeout configuration
      FREE_IDLE_TIMEOUT_HOURS    = "2" # Hours before free users are logged out
      PREMIUM_IDLE_TIMEOUT_HOURS = "2" # Hours before premium users are logged out

      # Ghost reaper: deregister container instances whose EC2 is terminated/gone
      CLUSTER_NAME = aws_ecs_cluster.main.name
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  tags = {
    Name    = "Common User Manager Lambda"
    Type    = "Common-Lambda"
    Service = "user-lifecycle"
  }

  depends_on = [
    aws_iam_role_policy_attachment.common_user_manager_lambda_basic,
    aws_cloudwatch_log_group.common_user_manager_logs,
    data.archive_file.common_user_manager_zip
  ]
}

# ===========================
# IAM Role for Common User Manager Lambda
# ===========================

resource "aws_iam_role" "common_user_manager_lambda" {
  name = "${var.environment}-common-user-manager-lambda-role"

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
    Name = "Common User Manager Lambda Role"
    Type = "Common-IAM"
  }
}

# Basic Lambda execution policy
resource "aws_iam_role_policy_attachment" "common_user_manager_lambda_basic" {
  role       = aws_iam_role.common_user_manager_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Custom policy for RDS access
resource "aws_iam_role_policy" "common_user_manager_lambda_policy" {
  name = "${var.environment}-common-user-manager-lambda-policy"
  role = aws_iam_role.common_user_manager_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # CloudWatch metrics (requires wildcard)
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
          "cloudwatch:GetMetricData",
          "cloudwatch:GetMetricStatistics"
        ]
        Resource = "*"
      },
      # Ghost reaper: list/deregister container instances (cluster passed as param)
      {
        Effect = "Allow"
        Action = [
          "ecs:ListContainerInstances",
          "ecs:DeregisterContainerInstance"
        ]
        Resource = "*"
      },
      # Ghost reaper: describe container instances, scoped to this cluster
      {
        Effect   = "Allow"
        Action   = "ecs:DescribeContainerInstances"
        Resource = "*"
        Condition = {
          ArnEquals = {
            "ecs:cluster" = aws_ecs_cluster.main.arn
          }
        }
      },
      # Ghost reaper: resolve backing EC2 state (no resource-level support)
      {
        Effect   = "Allow"
        Action   = "ec2:DescribeInstances"
        Resource = "*"
      }
    ]
  })
}

# ===========================
# CloudWatch Log Group
# ===========================

resource "aws_cloudwatch_log_group" "common_user_manager_logs" {
  name              = "/aws/lambda/${var.environment}-common-user-manager"
  retention_in_days = 30

  tags = {
    Name = "Common User Manager Logs"
    Type = "Common-CloudWatch"
  }
}

# ===========================
# CloudWatch Events (Periodic Execution)
# ===========================

# CloudWatch Events Rule (every 10 minutes)
resource "aws_cloudwatch_event_rule" "common_user_manager_schedule" {
  name                = "${var.environment}-common-user-manager-schedule"
  description         = "Trigger common user manager every 10 minutes for user lifecycle management"
  schedule_expression = "rate(10 minutes)"
  state               = "ENABLED"

  tags = {
    Name    = "Common User Manager Schedule"
    Type    = "Common-CloudWatch"
    Service = "user-lifecycle"
  }
}

# CloudWatch Events Target
resource "aws_cloudwatch_event_target" "common_user_manager_target" {
  rule      = aws_cloudwatch_event_rule.common_user_manager_schedule.name
  target_id = "CommonUserManagerTarget"
  arn       = aws_lambda_function.common_user_manager.arn

  input = jsonencode({
    source      = "aws.events"
    detail-type = "Scheduled Event"
    detail = {
      action = "manage_users"
    }
  })
}

# Lambda Permission for CloudWatch Events
resource "aws_lambda_permission" "allow_cloudwatch_common_user_manager" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.common_user_manager.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.common_user_manager_schedule.arn
}

# ===========================
# Outputs
# ===========================

output "common_user_manager_lambda_name" {
  description = "Name of the common user manager Lambda function"
  value       = aws_lambda_function.common_user_manager.function_name
}

output "common_user_manager_lambda_arn" {
  description = "ARN of the common user manager Lambda function"
  value       = aws_lambda_function.common_user_manager.arn
}

output "common_user_manager_triggers" {
  description = "Common user manager Lambda trigger configuration"
  value = {
    scheduled_rule = aws_cloudwatch_event_rule.common_user_manager_schedule.name
  }
}

# ===============================================
# Free Manager Lambda Infrastructure
# ===============================================
# This file contains all infrastructure for the Free Manager Lambda system:
# - Lambda function for monitoring and load balancing free tier users
# - CloudWatch Events for periodic execution
# - CloudWatch Alarms for threshold-based scaling
# - IAM roles and permissions
# - CloudWatch metrics and monitoring

# ===========================
# Free Manager Lambda Package
# ===========================

# Install dependencies
resource "null_resource" "install_free_manager_dependencies" {
  provisioner "local-exec" {
    command = <<-EOT
      mkdir -p ${path.module}/free_manager_package
      /usr/bin/python3 -m pip install pymysql boto3 -t ${path.module}/free_manager_package/ --no-cache-dir
    EOT
  }

  triggers = {
    code_changes = md5(join("", [
      filesha256("${path.module}/free_manager_package/free_manager.py"),
      filesha256("${path.module}/free_manager_package/free_user_utils.py")
    ]))
  }
}

# Create ZIP using archive_file
data "archive_file" "free_manager_zip" {
  type        = "zip"
  source_dir  = "${path.module}/free_manager_package"
  output_path = "${path.module}/free_manager.py.zip"

  depends_on = [null_resource.install_free_manager_dependencies]
}

# ===========================
# Free Manager Lambda Function
# ===========================

resource "aws_lambda_function" "free_manager" {
  filename      = "${path.module}/free_manager.py.zip"
  function_name = "${var.environment}-free-manager"
  role          = aws_iam_role.free_manager_lambda.arn
  handler       = "free_manager.handler"
  runtime       = "python3.11"
  timeout       = 900 # 15 minutes # Max timeout
  layers        = [aws_lambda_layer_version.aws_constants.arn]

  source_code_hash = data.archive_file.free_manager_zip.output_base64sha256

  environment {
    variables = {
      # Database configuration
      RDS_HOST     = aws_db_proxy.main.endpoint
      RDS_USER     = var.mysql_user
      RDS_PASSWORD = var.mysql_password
      RDS_DATABASE = var.mysql_database

      # ECS configuration
      CLUSTER_NAME      = aws_ecs_cluster.main.name
      FREE_SERVICE_NAME = aws_ecs_service.autoscaling.name

      # Autoscaling configuration
      ASG_NAME = aws_autoscaling_group.main.name

      # Environment prefix for scoped CloudWatch namespaces
      ENV_PREFIX = var.environment

      # Free tier configuration
      FREE_USER_THRESHOLD         = "5"  # Trigger scaling at 5 active users
      FREE_IDLE_THRESHOLD_MINUTES = "5"  # Consider user idle after 5 minutes (reduced from 10)
      MAX_FREE_INSTANCES          = "10" # Maximum number of free tier instances

      # Internal API configuration for experiment sync after migration
      ALB_DNS_NAME        = aws_lb.autoscaling.dns_name
      INTERNAL_API_SECRET = random_password.internal_api_secret.result
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  tags = {
    Name    = "Free Manager Lambda"
    Type    = "Free-Lambda"
    Service = "free-tier"
  }

  depends_on = [
    aws_iam_role_policy_attachment.free_manager_lambda_basic,
    aws_cloudwatch_log_group.free_manager_logs,
    data.archive_file.free_manager_zip
  ]
}

# ===========================
# IAM Role for Free Manager Lambda
# ===========================

resource "aws_iam_role" "free_manager_lambda" {
  name = "${var.environment}-free-manager-lambda-role"

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
    Name = "Free Manager Lambda Role"
    Type = "Free-IAM"
  }
}

# Basic Lambda execution policy
resource "aws_iam_role_policy_attachment" "free_manager_lambda_basic" {
  role       = aws_iam_role.free_manager_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Custom policy for ECS, CloudWatch, and EC2 access
resource "aws_iam_role_policy" "free_manager_lambda_policy" {
  name = "${var.environment}-free-manager-lambda-policy"
  role = aws_iam_role.free_manager_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ECS Service actions (scoped to free tier service)
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:UpdateService"
        ]
        Resource = aws_ecs_service.autoscaling.id
      },
      # ECS Cluster-level actions
      {
        Effect = "Allow"
        Action = [
          "ecs:ListTasks",
          "ecs:ListContainerInstances"
        ]
        Resource = "*"
      },
      # ECS Task/Instance describe (scoped to cluster)
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeTasks",
          "ecs:ListContainerInstances",
          "ecs:DescribeContainerInstances"
        ]
        Resource = "*"
      },
      # ASG Describe (read-only)
      {
        Effect   = "Allow"
        Action   = "autoscaling:DescribeAutoScalingGroups"
        Resource = "*"
      },
      # ASG Management (scoped to free tier ASG)
      {
        Effect = "Allow"
        Action = [
          "autoscaling:SetDesiredCapacity",
          "autoscaling:UpdateAutoScalingGroup"
        ]
        Resource = aws_autoscaling_group.main.arn
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
      },
      # EC2 Describe (read-only, requires wildcard)
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus"
        ]
        Resource = "*"
      }
    ]
  })
}

# ===========================
# CloudWatch Log Group
# ===========================

resource "aws_cloudwatch_log_group" "free_manager_logs" {
  name              = "/aws/lambda/${var.environment}-free-manager"
  retention_in_days = 30

  tags = {
    Name = "Free Manager Logs"
    Type = "Free-CloudWatch"
  }
}

# ===========================
# CloudWatch Events (Periodic Execution)
# ===========================

# CloudWatch Events Rule (every 5 minutes)
resource "aws_cloudwatch_event_rule" "free_manager_schedule" {
  name                = "${var.environment}-free-manager-schedule"
  description         = "Trigger free manager every 5 minutes for monitoring"
  schedule_expression = "rate(5 minutes)"
  state               = "ENABLED"

  tags = {
    Name    = "Free Manager Schedule"
    Type    = "Free-CloudWatch"
    Service = "free-tier"
  }
}

# CloudWatch Events Target
resource "aws_cloudwatch_event_target" "free_manager_target" {
  rule      = aws_cloudwatch_event_rule.free_manager_schedule.name
  target_id = "FreeManagerTarget"
  arn       = aws_lambda_function.free_manager.arn

  input = jsonencode({
    source      = "aws.events"
    detail-type = "Scheduled Event"
    detail = {
      action = "monitor"
    }
  })
}

# Lambda Permission for CloudWatch Events
resource "aws_lambda_permission" "allow_cloudwatch_free_manager" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.free_manager.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.free_manager_schedule.arn
}

# ===========================
# ASG Event Triggers
# ===========================

# EventBridge rule for ASG scaling events
resource "aws_cloudwatch_event_rule" "free_manager_asg_events" {
  name        = "${var.environment}-free-manager-asg-events"
  description = "Trigger free manager on ASG lifecycle events for immediate ECS sync"

  event_pattern = jsonencode({
    source = ["aws.autoscaling"]
    detail-type = [
      "EC2 Instance Launch Successful",
      "EC2 Instance Terminate Successful",
      "EC2 Instance Launch Unsuccessful",
      "EC2 Instance-launch Lifecycle Action",
      "EC2 Instance-terminate Lifecycle Action"
    ]
    detail = {
      AutoScalingGroupName = [aws_autoscaling_group.main.name]
    }
  })

  tags = {
    Name    = "Free Manager ASG Events"
    Type    = "Free-CloudWatch"
    Service = "free-tier"
  }
}

# EventBridge target to invoke free_manager on ASG events
resource "aws_cloudwatch_event_target" "free_manager_asg_target" {
  rule      = aws_cloudwatch_event_rule.free_manager_asg_events.name
  target_id = "FreeManagerASGTarget"
  arn       = aws_lambda_function.free_manager.arn
}

# Lambda Permission for ASG events
resource "aws_lambda_permission" "allow_asg_events_free_manager" {
  statement_id  = "AllowExecutionFromASGEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.free_manager.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.free_manager_asg_events.arn
}

# ===========================
# Free Cleanup Lambda Package
# ===========================
# This Lambda is used by test scripts to manage test data
# without requiring direct database access from outside VPC.

# Install dependencies for free cleanup Lambda
resource "null_resource" "install_free_cleanup_dependencies" {
  provisioner "local-exec" {
    command = <<-EOT
      /usr/bin/python3 -m pip install pymysql -t ${path.module}/free_cleanup_package/ --no-cache-dir
    EOT
  }

  triggers = {
    code_changes = filesha256("${path.module}/free_cleanup_package/free_cleanup.py")
  }
}

# Create ZIP file for free cleanup Lambda
data "archive_file" "free_cleanup_zip" {
  type        = "zip"
  source_dir  = "${path.module}/free_cleanup_package"
  output_path = "${path.module}/free_cleanup.py.zip"

  depends_on = [null_resource.install_free_cleanup_dependencies]
}

# ===========================
# Free Cleanup Lambda Function
# ===========================

resource "aws_lambda_function" "free_cleanup" {
  filename      = "${path.module}/free_cleanup.py.zip"
  function_name = "${var.environment}-free-cleanup"
  role          = aws_iam_role.free_manager_lambda.arn
  handler       = "free_cleanup.handler"
  runtime       = "python3.11"
  timeout       = 300 # 5 minutes
  layers        = [aws_lambda_layer_version.aws_constants.arn]

  source_code_hash = data.archive_file.free_cleanup_zip.output_base64sha256

  environment {
    variables = {
      # Database configuration
      RDS_HOST     = aws_db_proxy.main.endpoint
      RDS_USER     = var.mysql_user
      RDS_PASSWORD = var.mysql_password
      RDS_DATABASE = var.mysql_database
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  tags = {
    Name    = "Free Cleanup Lambda"
    Type    = "Free-Lambda"
    Service = "free-tier"
  }

  depends_on = [
    aws_iam_role_policy_attachment.free_manager_lambda_basic,
    aws_cloudwatch_log_group.free_cleanup_logs,
    data.archive_file.free_cleanup_zip
  ]
}

# CloudWatch Log Group for Free Cleanup
resource "aws_cloudwatch_log_group" "free_cleanup_logs" {
  name              = "/aws/lambda/${var.environment}-free-cleanup"
  retention_in_days = 30

  tags = {
    Name = "Free Cleanup Lambda Logs"
    Type = "Free-CloudWatch"
  }
}

# ===========================
# Outputs
# ===========================

output "free_manager_lambda_name" {
  description = "Name of the free manager Lambda function"
  value       = aws_lambda_function.free_manager.function_name
}

output "free_cleanup_lambda_name" {
  description = "Name of the free cleanup Lambda function (for test scripts)"
  value       = aws_lambda_function.free_cleanup.function_name
}

output "free_manager_triggers" {
  description = "Free manager Lambda trigger configuration"
  value = {
    scheduled_rule = aws_cloudwatch_event_rule.free_manager_schedule.name
    asg_event_rule = aws_cloudwatch_event_rule.free_manager_asg_events.name
  }
}

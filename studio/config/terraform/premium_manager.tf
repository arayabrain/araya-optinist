# =============================
# PREMIUM TIER LAMBDA FUNCTIONS
# =============================

# Premium Manager Lambda Function
resource "aws_lambda_function" "premium_manager" {
  filename      = "${path.module}/premium_manager.py.zip"
  function_name = "subscr-premium-manager"
  role          = aws_iam_role.premium_manager_lambda.arn
  handler       = "premium_manager.handler"
  runtime       = "python3.9"
  timeout       = 600

  source_code_hash = data.archive_file.premium_manager_zip.output_base64sha256

  environment {
    variables = {
      VPC_ID                       = aws_vpc.main.id
      SUBNET_IDS                   = "${aws_subnet.private1.id},${aws_subnet.private2.id}"
      SECURITY_GROUP_ID            = aws_security_group.ecs.id
      ALB_ARN                      = aws_lb.autoscaling.arn
      ALB_LISTENER_ARN             = aws_lb_listener.autoscaling_https.arn
      AUTOSCALING_TARGET_GROUP_ARN = aws_lb_target_group.autoscaling.arn
      PREMIUM_INSTANCE_IDS         = join(",", aws_instance.premium[*].id)
      PREMIUM_LAUNCH_TEMPLATE_ID   = aws_launch_template.premium.id
      CLUSTER_NAME                 = aws_ecs_cluster.main.name
      PREMIUM_SERVICE_NAME         = aws_ecs_service.premium.name
      RDS_HOST                     = aws_db_proxy.main.endpoint
      RDS_USER                     = var.mysql_user
      RDS_PASSWORD                 = var.mysql_password
      RDS_DATABASE                 = var.mysql_database
      # Dynamic capacity settings (use existing ABSOLUTE_MAX + minimal new ones)
      PREMIUM_SAFETY_BUFFER      = "1" # Extra instances for quick response
      PREMIUM_STANDBY_POOL_SIZE  = "1" # Number of stopped instances to maintain
      PREMIUM_IDLE_TIMEOUT_HOURS = "3" # Hours before idle instances are converted to standby
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  tags = {
    Name    = "Premium Manager Lambda"
    Type    = "Premium-Lambda"
    Service = "premium-tier"
  }

  depends_on = [
    aws_iam_role_policy_attachment.premium_manager_lambda_basic,
    aws_cloudwatch_log_group.premium_manager_logs,
    data.archive_file.premium_manager_zip
  ]
}

# Migration Queue Processing Lambda Function

# CloudWatch Log Groups

# CloudWatch Events Rule for Migration Queue Processing (every 2 minutes)

# CloudWatch Events Rule for Premium Cleanup (every hour)
resource "aws_cloudwatch_event_rule" "premium_cleanup_schedule" {
  name                = "subscr-premium-cleanup-schedule"
  description         = "Trigger premium assignment cleanup every hour"
  schedule_expression = "rate(1 hour)"
  state               = "ENABLED"

  tags = {
    Name    = "Premium Cleanup Schedule"
    Type    = "Premium-CloudWatch"
    Service = "premium-tier"
  }
}

# CloudWatch Events Target for Cleanup
resource "aws_cloudwatch_event_target" "premium_cleanup_target" {
  rule      = aws_cloudwatch_event_rule.premium_cleanup_schedule.name
  target_id = "PremiumCleanupTarget"
  arn       = aws_lambda_function.premium_cleanup.arn

  input = jsonencode({
    source      = "aws.events"
    detail-type = "Scheduled Event"
    detail = {
      action = "cleanup"
    }
  })
}

# =======
# Lambda
# =======

# Lambda Permission for Cleanup CloudWatch Events
resource "aws_lambda_permission" "allow_cloudwatch_cleanup" {
  statement_id  = "AllowExecutionFromCloudWatchCleanup"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.premium_cleanup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.premium_cleanup_schedule.arn
}

# Create ZIP file for premium manager Lambda with dependencies
# Install dependencies first
resource "null_resource" "install_dependencies" {
  provisioner "local-exec" {
    command = <<-EOT
      mkdir -p ${path.module}/premium_manager_package
      /usr/bin/python3 -m pip install pymysql -t ${path.module}/premium_manager_package/ --no-cache-dir
    EOT
  }

  triggers = {
    code_changes = md5(join("", [
      filesha256("${path.module}/premium_manager_package/premium_manager.py"),
      filesha256("${path.module}/../../app/common/core/premium/premium_assignment_service.py")
    ]))
  }
}

# Create ZIP using archive_file
data "archive_file" "premium_manager_zip" {
  type        = "zip"
  source_dir  = "${path.module}/premium_manager_package"
  output_path = "${path.module}/premium_manager.py.zip"

  depends_on = [null_resource.install_dependencies]
}

# CloudWatch Log Group for Premium Manager
resource "aws_cloudwatch_log_group" "premium_manager_logs" {
  name              = "/aws/lambda/subscr-premium-manager"
  retention_in_days = 14

  tags = {
    Name = "Premium Manager Logs"
    Type = "Premium-CloudWatch"
  }
}

# Premium Cleanup Lambda Function
resource "aws_lambda_function" "premium_cleanup" {
  filename      = "${path.module}/premium_cleanup.py.zip"
  function_name = "subscr-premium-cleanup"
  role          = aws_iam_role.premium_manager_lambda.arn
  handler       = "premium_cleanup.handler"
  runtime       = "python3.9"
  timeout       = 300

  source_code_hash = data.archive_file.premium_cleanup_zip.output_base64sha256

  environment {
    variables = {
      VPC_ID                     = aws_vpc.main.id
      SUBNET_IDS                 = "${aws_subnet.private1.id},${aws_subnet.private2.id}"
      SECURITY_GROUP_ID          = aws_security_group.ecs.id
      ALB_ARN                    = aws_lb.autoscaling.arn
      ALB_LISTENER_ARN           = aws_lb_listener.autoscaling_https.arn
      PREMIUM_INSTANCE_IDS       = join(",", aws_instance.premium[*].id)
      PREMIUM_LAUNCH_TEMPLATE_ID = aws_launch_template.premium.id
      CLUSTER_NAME               = aws_ecs_cluster.main.name
      PREMIUM_SERVICE_NAME       = aws_ecs_service.premium.name
      RDS_HOST                   = aws_db_proxy.main.endpoint
      RDS_USER                   = var.mysql_user
      RDS_PASSWORD               = var.mysql_password
      RDS_DATABASE               = var.mysql_database
      # Cleanup-specific settings
      PREMIUM_IDLE_TIMEOUT_HOURS = "3"
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  tags = {
    Name    = "Premium Cleanup Lambda"
    Type    = "Premium-Lambda"
    Service = "premium-tier"
  }

  depends_on = [
    aws_iam_role_policy_attachment.premium_manager_lambda_basic,
    aws_cloudwatch_log_group.premium_cleanup_logs,
    data.archive_file.premium_cleanup_zip
  ]
}

# Install dependencies for premium cleanup Lambda
resource "null_resource" "install_cleanup_dependencies" {
  provisioner "local-exec" {
    command = <<-EOT
      /usr/bin/python3 -m pip install pymysql -t ${path.module}/premium_cleanup_package/ --no-cache-dir
    EOT
  }

  triggers = {
    code_changes = filesha256("${path.module}/premium_cleanup_package/premium_cleanup.py")
  }
}

# Create ZIP file for premium cleanup Lambda
data "archive_file" "premium_cleanup_zip" {
  type        = "zip"
  source_dir  = "${path.module}/premium_cleanup_package"
  output_path = "${path.module}/premium_cleanup.py.zip"

  depends_on = [null_resource.install_cleanup_dependencies]
}

# CloudWatch Log Group for Premium Cleanup
resource "aws_cloudwatch_log_group" "premium_cleanup_logs" {
  name              = "/aws/lambda/subscr-premium-cleanup"
  retention_in_days = 14

  tags = {
    Name = "Premium Cleanup Lambda Logs"
    Type = "Premium-CloudWatch"
  }
}

# ======================
# PREMIUM TIER IAM ROLES
# ======================


# Premium Manager Lambda Role
resource "aws_iam_role" "premium_manager_lambda" {
  name = "subscr-premium-manager-lambda-role"

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
}

resource "aws_iam_role_policy_attachment" "premium_manager_lambda_basic" {
  role       = aws_iam_role.premium_manager_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "premium_manager_lambda_vpc" {
  role       = aws_iam_role.premium_manager_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Premium Manager Lambda Permissions
resource "aws_iam_role_policy" "premium_manager_permissions" {
  name = "subscr-premium-manager-permissions"
  role = aws_iam_role.premium_manager_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # EC2 Describe actions (read-only, need wildcard)
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus",
          "ec2:DescribeSpotFleetInstances",
          "ec2:DescribeSpotFleetRequests"
        ]
        Resource = "*"
      },
      # EC2 Management actions (scoped to premium instances by tag)
      {
        Effect = "Allow"
        Action = [
          "ec2:StopInstances",
          "ec2:StartInstances",
          "ec2:TerminateInstances"
        ]
        Resource = "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "ec2:ResourceTag/Service" = "premium-tier"
          }
        }
      },
      # EC2 CreateTags (unrestricted to allow tagging new instances)
      {
        Effect = "Allow"
        Action = "ec2:CreateTags"
        Resource = "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*"
      },
      # EC2 RunInstances (requires multiple resource types)
      {
        Effect = "Allow"
        Action = "ec2:RunInstances"
        Resource = [
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*",
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:volume/*",
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:network-interface/*",
          "arn:aws:ec2:${var.aws_region}::image/*",
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:subnet/*",
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:security-group/*",
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:key-pair/*",
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:launch-template/*"
        ]
      },
      # ECS Cluster-level actions
      # Note: ListContainerInstances requires Resource="*" without conditions
      # because the cluster context is passed as a parameter, not evaluated as a condition
      {
        Effect = "Allow"
        Action = [
          "ecs:ListTasks",
          "ecs:ListContainerInstances"
        ]
        Resource = "*"
      },
      # ECS Service actions (scoped to specific services)
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:UpdateService"
        ]
        Resource = [
          aws_ecs_service.premium.id,
          aws_ecs_service.autoscaling.id
        ]
      },
      # ECS Task actions (scoped to cluster)
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeTasks",
          "ecs:DescribeContainerInstances"
        ]
        Resource = "*"
        Condition = {
          ArnEquals = {
            "ecs:cluster" = aws_ecs_cluster.main.arn
          }
        }
      },
      # ELB Describe actions (read-only)
      {
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeRules"
        ]
        Resource = "*"
      },
      # ELB Management actions (scoped to this ALB)
      {
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:CreateTargetGroup",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:CreateRule",
          "elasticloadbalancing:DeleteRule",
          "elasticloadbalancing:ModifyRule",
          "elasticloadbalancing:RegisterTargets",
          "elasticloadbalancing:DeregisterTargets",
          "elasticloadbalancing:AddTags",
          "elasticloadbalancing:RemoveTags"
        ]
        Resource = [
          aws_lb.autoscaling.arn,
          "${aws_lb.autoscaling.arn}/*",
          aws_lb_listener.autoscaling_https.arn,
          "arn:aws:elasticloadbalancing:${var.aws_region}:${data.aws_caller_identity.current.account_id}:listener-rule/*",
          "arn:aws:elasticloadbalancing:${var.aws_region}:${data.aws_caller_identity.current.account_id}:targetgroup/subscr-*",
          "arn:aws:elasticloadbalancing:${var.aws_region}:${data.aws_caller_identity.current.account_id}:targetgroup/premium-*"
        ]
      },
      # CloudWatch metrics (requires wildcard)
      {
        Effect = "Allow"
        Action = "cloudwatch:PutMetricData"
        Resource = "*"
      },
      # ASG Describe (read-only)
      {
        Effect = "Allow"
        Action = "autoscaling:DescribeAutoScalingGroups"
        Resource = "*"
      },
      # RDS Describe (read-only)
      {
        Effect = "Allow"
        Action = "rds:DescribeDBInstances"
        Resource = "*"
      },
      # S3 access (already scoped)
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.app_storage.arn,
          "${aws_s3_bucket.app_storage.arn}/*"
        ]
      },
      # IAM PassRole (already scoped)
      {
        Effect = "Allow"
        Action = "iam:PassRole"
        Resource = aws_iam_role.ecs_instance_role.arn
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ec2.amazonaws.com"
          }
        }
      },
      # Lambda self-invocation (already scoped)
      {
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = aws_lambda_function.premium_manager.arn
      }
    ]
  })
}

# Spot Interruption Handler Lambda Role
resource "aws_iam_role" "spot_interruption_handler" {
  name = "subscr-spot-interruption-handler-role"

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
}

resource "aws_iam_role_policy_attachment" "spot_interruption_handler_basic" {
  role       = aws_iam_role.spot_interruption_handler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Custom CloudWatch Metrics for Premium Tracking
resource "aws_cloudwatch_log_metric_filter" "premium_assignments" {
  name           = "premium-assignments"
  log_group_name = aws_cloudwatch_log_group.premium_manager_logs.name
  pattern        = "[timestamp, level=\"INFO\", message=\"Successfully assigned premium user*\"]"

  metric_transformation {
    name      = "ActiveAssignments"
    namespace = "OptiNiSt/Premium"
    value     = "1"
  }
}


# ======================
# Cost tracker
# ======================

# Create ZIP using archive_file for cost tracker Lambda
data "archive_file" "cost_tracker_zip" {
  type        = "zip"
  source_dir  = "${path.module}/cost_tracker_package"
  output_path = "${path.module}/cost_tracker.py.zip"
}

# Cost Tracking Lambda Function
resource "aws_lambda_function" "cost_tracker" {
  filename         = "${path.module}/cost_tracker.py.zip"
  function_name    = "subscr-cost-tracker"
  role             = aws_iam_role.premium_manager_lambda.arn
  handler          = "cost_tracker.handler"
  runtime          = "python3.9"
  timeout          = 300
  source_code_hash = data.archive_file.cost_tracker_zip.output_base64sha256

  environment {
    variables = {
      ASG_NAME      = aws_autoscaling_group.main.name
      REGION        = var.aws_region
      INSTANCE_TYPE = "t3.large"
    }
  }

  tags = {
    Name    = "Cost Tracker Lambda"
    Service = "cost-monitoring"
  }

  depends_on = [
    aws_iam_role_policy_attachment.premium_manager_lambda_basic
  ]
}


# Cost Controller Lambda Role
resource "aws_iam_role" "cost_controller_lambda" {
  name = "subscr-cost-controller-lambda-role"

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
}

resource "aws_iam_role_policy_attachment" "cost_controller_lambda_basic" {
  role       = aws_iam_role.cost_controller_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# EventBridge rule to trigger cost tracker Lambda hourly
resource "aws_cloudwatch_event_rule" "cost_tracker_schedule" {
  name                = "subscr-cost-tracker-schedule"
  description         = "Trigger cost tracker Lambda hourly to publish cost metrics"
  schedule_expression = "rate(1 hour)"

  tags = {
    Name    = "Cost Tracker Schedule"
    Service = "cost-monitoring"
  }
}

resource "aws_cloudwatch_event_target" "cost_tracker" {
  rule      = aws_cloudwatch_event_rule.cost_tracker_schedule.name
  target_id = "CostTrackerLambda"
  arn       = aws_lambda_function.cost_tracker.arn
}

resource "aws_lambda_permission" "allow_eventbridge_cost_tracker" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_tracker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cost_tracker_schedule.arn
}

# Essential CloudWatch Alarms for Premium Monitoring
resource "aws_cloudwatch_metric_alarm" "premium_cost_high" {
  alarm_name          = "subscr-premium-monthly-cost-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "TotalMonthlyCost"
  namespace           = "OptiNiSt/Cost"
  period              = "86400" # Daily
  statistic           = "Maximum"
  threshold           = "500"
  alarm_description   = "Monthly cost estimate is high"
  alarm_actions       = []

  tags = {
    Name    = "High Monthly Cost Alarm"
    Service = "cost-monitoring"
  }
}

resource "aws_cloudwatch_metric_alarm" "premium_cpu_high" {
  alarm_name          = "subscr-premium-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "Premium ECS service CPU utilization is high"

  dimensions = {
    ServiceName = aws_ecs_service.premium.name
    ClusterName = aws_ecs_cluster.main.name
  }

  tags = {
    Name    = "Premium CPU High Alarm"
    Service = "premium-monitoring"
  }
}

resource "aws_cloudwatch_metric_alarm" "premium_memory_high" {
  alarm_name          = "subscr-premium-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = "300"
  statistic           = "Average"
  threshold           = "85"
  alarm_description   = "Premium ECS service memory utilization is high"

  dimensions = {
    ServiceName = aws_ecs_service.premium.name
    ClusterName = aws_ecs_cluster.main.name
  }

  tags = {
    Name    = "Premium Memory High Alarm"
    Service = "premium-monitoring"
  }
}

output "premium_manager_lambda_arn" {
  description = "ARN of the premium manager Lambda function"
  value       = aws_lambda_function.premium_manager.arn
}

output "premium_cleanup_lambda_name" {
  description = "Name of the premium cleanup Lambda function"
  value       = aws_lambda_function.premium_cleanup.function_name
}

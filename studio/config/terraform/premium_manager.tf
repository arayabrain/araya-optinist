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
      RDS_HOST                     = aws_db_instance.main.endpoint
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
      RDS_HOST                   = aws_db_instance.main.endpoint
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

# API Gateway for Premium Management
resource "aws_api_gateway_rest_api" "premium_management" {
  name        = "subscr-premium-management-api"
  description = "API for premium user assignment and management"

  tags = {
    Name = "Premium Management API"
    Type = "Premium-API"
  }
}

# API Gateway Resource for Premium endpoints
resource "aws_api_gateway_resource" "premium_resource" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  parent_id   = aws_api_gateway_rest_api.premium_management.root_resource_id
  path_part   = "premium"
}

# API Gateway Resource for assign endpoint
resource "aws_api_gateway_resource" "premium_assign" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  parent_id   = aws_api_gateway_resource.premium_resource.id
  path_part   = "assign"
}

# API Gateway Resource for release endpoint
resource "aws_api_gateway_resource" "premium_release" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  parent_id   = aws_api_gateway_resource.premium_resource.id
  path_part   = "release"
}

# API Gateway Resource for status endpoint
resource "aws_api_gateway_resource" "premium_status" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  parent_id   = aws_api_gateway_resource.premium_resource.id
  path_part   = "status"
}

# API Gateway Method for assign (POST)
resource "aws_api_gateway_method" "premium_assign_post" {
  rest_api_id   = aws_api_gateway_rest_api.premium_management.id
  resource_id   = aws_api_gateway_resource.premium_assign.id
  http_method   = "POST"
  authorization = "NONE"
}

# API Gateway Method for release (POST)
resource "aws_api_gateway_method" "premium_release_post" {
  rest_api_id   = aws_api_gateway_rest_api.premium_management.id
  resource_id   = aws_api_gateway_resource.premium_release.id
  http_method   = "POST"
  authorization = "NONE"
}

# API Gateway Method for status (GET)
resource "aws_api_gateway_method" "premium_status_get" {
  rest_api_id   = aws_api_gateway_rest_api.premium_management.id
  resource_id   = aws_api_gateway_resource.premium_status.id
  http_method   = "GET"
  authorization = "NONE"
}

# API Gateway Integration for assign
resource "aws_api_gateway_integration" "premium_assign_integration" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  resource_id = aws_api_gateway_resource.premium_assign.id
  http_method = aws_api_gateway_method.premium_assign_post.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.premium_manager.invoke_arn
}

# API Gateway Integration for release
resource "aws_api_gateway_integration" "premium_release_integration" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  resource_id = aws_api_gateway_resource.premium_release.id
  http_method = aws_api_gateway_method.premium_release_post.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.premium_manager.invoke_arn
}

# API Gateway Integration for status
resource "aws_api_gateway_integration" "premium_status_integration" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  resource_id = aws_api_gateway_resource.premium_status.id
  http_method = aws_api_gateway_method.premium_status_get.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.premium_manager.invoke_arn
}

# Lambda permission for API Gateway to invoke premium manager
resource "aws_lambda_permission" "premium_manager_api_gateway" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.premium_manager.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = "${aws_api_gateway_rest_api.premium_management.execution_arn}/*/*"
}

# API Gateway Deployment
resource "aws_api_gateway_deployment" "premium_management_deployment" {
  depends_on = [
    aws_api_gateway_integration.premium_assign_integration,
    aws_api_gateway_integration.premium_release_integration,
    aws_api_gateway_integration.premium_status_integration
  ]

  rest_api_id = aws_api_gateway_rest_api.premium_management.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.premium_assign.id,
      aws_api_gateway_resource.premium_release.id,
      aws_api_gateway_resource.premium_status.id,
      aws_api_gateway_method.premium_assign_post.id,
      aws_api_gateway_method.premium_release_post.id,
      aws_api_gateway_method.premium_status_get.id,
      aws_api_gateway_integration.premium_assign_integration.id,
      aws_api_gateway_integration.premium_release_integration.id,
      aws_api_gateway_integration.premium_status_integration.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

# API Gateway Stage
resource "aws_api_gateway_stage" "premium_management_v1" {
  deployment_id = aws_api_gateway_deployment.premium_management_deployment.id
  rest_api_id   = aws_api_gateway_rest_api.premium_management.id
  stage_name    = "v1"

  tags = {
    Name    = "Premium Management API v1"
    Service = "premium-api"
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
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeSpotFleetInstances",
          "ec2:DescribeSpotFleetRequests",
          "ec2:ModifySpotFleetRequest",
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceHealth",
          "ec2:StopInstances",
          "ec2:StartInstances",
          "ec2:TerminateInstances",
          "ec2:RunInstances",
          "ec2:CreateTags"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:UpdateService",
          "ecs:RegisterTargets",
          "ecs:DeregisterTargets",
          "ecs:ListTasks",
          "ecs:DescribeTasks",
          "ecs:DescribeContainerInstances",
          "ecs:ListContainerInstances"
        ]
        Resource = "*"
      },
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
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeRules",
          "elasticloadbalancing:AddTags",
          "elasticloadbalancing:RemoveTags"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds-data:BatchExecuteStatement",
          "rds-data:BeginTransaction",
          "rds-data:CommitTransaction",
          "rds-data:ExecuteStatement",
          "rds-data:RollbackTransaction"
        ]
        Resource = "*"
      },
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
      {
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = aws_iam_role.ecs_instance_role.arn
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ec2.amazonaws.com"
          }
        }
      },
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
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

output "premium_api_gateway_url" {
  description = "URL of the premium management API Gateway"
  value       = "https://${aws_api_gateway_rest_api.premium_management.id}.execute-api.${var.aws_region}.amazonaws.com/${aws_api_gateway_stage.premium_management_v1.stage_name}/premium"
}


output "premium_manager_lambda_arn" {
  description = "ARN of the premium manager Lambda function"
  value       = aws_lambda_function.premium_manager.arn
}

output "premium_cleanup_lambda_name" {
  description = "Name of the premium cleanup Lambda function"
  value       = aws_lambda_function.premium_cleanup.function_name
}

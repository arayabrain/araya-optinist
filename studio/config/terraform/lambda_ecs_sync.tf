# ================================================
# Lambda Function to Sync ECS with ASG (1:1 Ratio)
# ================================================

# Archive the Lambda function code
data "archive_file" "ecs_asg_sync" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_ecs_asg_sync"
  output_path = "${path.module}/lambda_ecs_asg_sync.py.zip"
}

# IAM role for Lambda
resource "aws_iam_role" "ecs_asg_sync_lambda" {
  name = "subscr-ecs-asg-sync-lambda-role"

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
    Name = "subscr-ecs-asg-sync-lambda-role"
  }
}

# IAM policy for Lambda to access ECS and ASG
resource "aws_iam_role_policy" "ecs_asg_sync_lambda" {
  name = "subscr-ecs-asg-sync-lambda-policy"
  role = aws_iam_role.ecs_asg_sync_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:UpdateService"
        ]
        Resource = [
          aws_ecs_service.autoscaling.id,
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.main.name}/${aws_ecs_service.autoscaling.name}"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

# Lambda function
resource "aws_lambda_function" "ecs_asg_sync" {
  filename         = data.archive_file.ecs_asg_sync.output_path
  function_name    = "subscr-ecs-asg-sync"
  role             = aws_iam_role.ecs_asg_sync_lambda.arn
  handler          = "sync_ecs_asg.lambda_handler"
  source_code_hash = data.archive_file.ecs_asg_sync.output_base64sha256
  runtime          = "python3.11"
  timeout          = 60

  environment {
    variables = {
      ECS_CLUSTER_NAME = aws_ecs_cluster.main.name
      ECS_SERVICE_NAME = aws_ecs_service.autoscaling.name
      ASG_NAME         = aws_autoscaling_group.main.name
    }
  }

  tags = {
    Name = "subscr-ecs-asg-sync"
  }

  depends_on = [
    aws_iam_role_policy.ecs_asg_sync_lambda
  ]
}

# CloudWatch Log Group for Lambda
resource "aws_cloudwatch_log_group" "ecs_asg_sync_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.ecs_asg_sync.function_name}"
  retention_in_days = 7

  tags = {
    Name = "subscr-ecs-asg-sync-lambda-logs"
  }
}

# ================================================
# EventBridge Rule to Trigger Lambda on ASG Events
# ================================================

# EventBridge rule for ASG scaling events
resource "aws_cloudwatch_event_rule" "asg_scaling" {
  name        = "subscr-asg-scaling-events"
  description = "Trigger Lambda when ASG capacity changes"

  event_pattern = jsonencode({
    source      = ["aws.autoscaling"]
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
    Name = "subscr-asg-scaling-events"
  }
}

# EventBridge target to invoke Lambda
resource "aws_cloudwatch_event_target" "ecs_asg_sync" {
  rule      = aws_cloudwatch_event_rule.asg_scaling.name
  target_id = "EcsAsgSyncLambda"
  arn       = aws_lambda_function.ecs_asg_sync.arn
}

# Permission for EventBridge to invoke Lambda
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ecs_asg_sync.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.asg_scaling.arn
}

# ================================================
# Additional: CloudWatch Alarm Trigger (Optional)
# ================================================
# This ensures Lambda is also triggered when CloudWatch alarms
# trigger ASG scaling (in addition to EventBridge events)

resource "aws_sns_topic" "asg_scaling_notifications" {
  name = "subscr-asg-scaling-notifications"

  tags = {
    Name = "subscr-asg-scaling-notifications"
  }
}

resource "aws_sns_topic_subscription" "ecs_asg_sync_lambda" {
  topic_arn = aws_sns_topic.asg_scaling_notifications.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.ecs_asg_sync.arn
}

resource "aws_lambda_permission" "allow_sns" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ecs_asg_sync.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.asg_scaling_notifications.arn
}

# Update ASG to send notifications
resource "aws_autoscaling_notification" "scaling_notifications" {
  group_names = [aws_autoscaling_group.main.name]

  notifications = [
    "autoscaling:EC2_INSTANCE_LAUNCH",
    "autoscaling:EC2_INSTANCE_TERMINATE",
    "autoscaling:EC2_INSTANCE_LAUNCH_ERROR",
    "autoscaling:EC2_INSTANCE_TERMINATE_ERROR"
  ]

  topic_arn = aws_sns_topic.asg_scaling_notifications.arn
}

# =============================================================
# Agent recovery — out-of-band detection
# =============================================================
# Three thin pieces, all alarm-only (no auto-termination):
#
#   1. EventBridge rule on `ECS Container Instance State Change`
#      → CloudWatch Logs → metric filter on `agentConnected=false`
#      → alarm. Catches every disconnect the control plane sees.
#
#   2. Heartbeat alarm on `/ecs/agent-recovery` log group. The
#      on-host watchdog (ecs-user-data.sh) writes a "tick" line
#      every run, so a 30-min silence means the watchdog itself
#      is broken.
#
#   3. Reconciliation Lambda — every 5 min, lists ASG instances
#      that are not registered as ECS container instances and
#      emits a count as a custom metric + alarm.
#
# Recovery happens on the host (see ecs-user-data.sh); these
# resources just page humans when the on-host watchdog is silent.

# -------------------------------------------------------------
# Shared log group used by:
#   * the on-host watchdog (PutLogEvents from EC2 instance role)
#   * the EventBridge → CW Logs target below
# -------------------------------------------------------------
resource "aws_cloudwatch_log_group" "agent_recovery" {
  name              = "/ecs/agent-recovery"
  retention_in_days = 30

  tags = {
    Name = "ECS Agent Recovery Log Group"
  }
}

# =============================================================
# EventBridge "Container Instance State Change" → metric
# =============================================================
resource "aws_cloudwatch_event_rule" "ecs_container_instance_state" {
  name        = "${var.environment}-ecs-container-instance-state-change"
  description = "Capture ECS container instance state changes (agentConnected transitions)"

  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Container Instance State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.main.arn]
    }
  })

  tags = {
    Name = "ECS Container Instance State Change Rule"
  }
}

resource "aws_cloudwatch_event_target" "ecs_container_instance_state_logs" {
  rule      = aws_cloudwatch_event_rule.ecs_container_instance_state.name
  target_id = "AgentRecoveryLogs"
  arn       = aws_cloudwatch_log_group.agent_recovery.arn
}

# EventBridge needs an explicit resource policy on the log group to deliver
# events. The provider attribute is `aws_cloudwatch_log_resource_policy`.
data "aws_iam_policy_document" "agent_recovery_logs" {
  statement {
    sid = "EventBridgeToCWLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.agent_recovery.arn}:*",
    ]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "delivery.logs.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "agent_recovery" {
  policy_name     = "${var.environment}-agent-recovery-logs"
  policy_document = data.aws_iam_policy_document.agent_recovery_logs.json
}

resource "aws_cloudwatch_log_metric_filter" "agent_disconnected" {
  name           = "${var.environment}-agent-disconnected"
  log_group_name = aws_cloudwatch_log_group.agent_recovery.name
  # The EventBridge payload contains `detail.agentConnected` as a JSON bool.
  pattern = "{ $.detail.agentConnected IS FALSE }"

  metric_transformation {
    name      = "AgentDisconnectedCount"
    namespace = "OptiNiSt/AgentRecovery"
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "agent_disconnected" {
  alarm_name          = "${var.environment}-ecs-agent-disconnected"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = "1"
  metric_name         = "AgentDisconnectedCount"
  namespace           = "OptiNiSt/AgentRecovery"
  period              = "300"
  statistic           = "Sum"
  threshold           = "1"
  alarm_description   = "ECS container instance reported agentConnected=false."
  alarm_actions       = local.critical_alerts_actions
  ok_actions          = local.critical_alerts_actions
  treat_missing_data  = "notBreaching"

  tags = {
    Name = "ECS Agent Disconnected Alarm"
  }
}

# =============================================================
# Watchdog heartbeat alarm
# =============================================================
# The on-host watchdog (ecs-user-data.sh) emits "watchdog tick" to
# /ecs/agent-recovery every 5 min on every host. If the entire
# fleet goes silent for 30 min, the watchdog itself is broken
# (e.g. never installed on a new AMI) and we need to know.
resource "aws_cloudwatch_log_metric_filter" "agent_recovery_heartbeat" {
  name           = "${var.environment}-agent-recovery-heartbeat"
  log_group_name = aws_cloudwatch_log_group.agent_recovery.name
  pattern        = "watchdog tick"

  metric_transformation {
    name      = "AgentRecoveryHeartbeatCount"
    namespace = "OptiNiSt/AgentRecovery"
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "agent_recovery_heartbeat_missing" {
  alarm_name          = "${var.environment}-agent-recovery-heartbeat-missing"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "AgentRecoveryHeartbeatCount"
  namespace           = "OptiNiSt/AgentRecovery"
  period              = "1800" # 30 min
  statistic           = "Sum"
  threshold           = "1"
  alarm_description   = "No agent-recovery watchdog heartbeats for 30 min — on-host watchdog may not be running."
  alarm_actions       = local.critical_alerts_actions
  ok_actions          = local.critical_alerts_actions
  treat_missing_data  = "breaching"

  tags = {
    Name = "Agent Recovery Heartbeat Missing Alarm"
  }
}

# =============================================================
# ASG ↔ container-instance reconciliation Lambda
# =============================================================
# Single-file zip — deliberately NOT a new package subtree.
data "archive_file" "agent_recovery_lambda" {
  type        = "zip"
  source_file = "${path.module}/agent_recovery_lambda.py"
  output_path = "${path.module}/agent_recovery_lambda.zip"
}

resource "aws_iam_role" "agent_recovery_lambda" {
  name = "${var.environment}-agent-recovery-lambda-role"

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
}

resource "aws_iam_role_policy_attachment" "agent_recovery_lambda_basic" {
  role       = aws_iam_role.agent_recovery_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "agent_recovery_lambda" {
  name = "${var.environment}-agent-recovery-lambda-permissions"
  role = aws_iam_role.agent_recovery_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecs:ListContainerInstances",
          "ecs:DescribeContainerInstances",
          "autoscaling:DescribeAutoScalingGroups",
          "cloudwatch:PutMetricData",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "agent_recovery_lambda" {
  name              = "/aws/lambda/${var.environment}-agent-recovery-reconciliation"
  retention_in_days = 14
}

resource "aws_lambda_function" "agent_recovery_reconciliation" {
  filename                       = data.archive_file.agent_recovery_lambda.output_path
  function_name                  = "${var.environment}-agent-recovery-reconciliation"
  role                           = aws_iam_role.agent_recovery_lambda.arn
  handler                        = "agent_recovery_lambda.handler"
  runtime                        = "python3.11"
  timeout                        = 60
  reserved_concurrent_executions = 1
  source_code_hash               = data.archive_file.agent_recovery_lambda.output_base64sha256

  environment {
    variables = {
      CLUSTERS  = aws_ecs_cluster.main.name
      ASG_NAMES = aws_autoscaling_group.main.name
    }
  }

  tags = {
    Name = "Agent Recovery Reconciliation Lambda"
  }

  depends_on = [
    aws_iam_role_policy.agent_recovery_lambda,
    aws_cloudwatch_log_group.agent_recovery_lambda,
  ]
}

resource "aws_cloudwatch_event_rule" "agent_recovery_reconciliation" {
  name                = "${var.environment}-agent-recovery-reconciliation"
  description         = "Run ASG/ECS reconciliation every 5 minutes"
  schedule_expression = "rate(5 minutes)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "agent_recovery_reconciliation" {
  rule      = aws_cloudwatch_event_rule.agent_recovery_reconciliation.name
  target_id = "AgentRecoveryReconciliation"
  arn       = aws_lambda_function.agent_recovery_reconciliation.arn
}

resource "aws_lambda_permission" "agent_recovery_reconciliation_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent_recovery_reconciliation.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.agent_recovery_reconciliation.arn
}

resource "aws_cloudwatch_metric_alarm" "ecs_asg_instance_unregistered" {
  alarm_name          = "${var.environment}-ecs-asg-instance-unregistered"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "EcsAsgInstanceUnregisteredCount"
  namespace           = "OptiNiSt/AgentRecovery"
  period              = "300"
  statistic           = "Maximum"
  threshold           = "0"
  alarm_description   = "An ASG instance is running but not registered as an ECS container instance."
  alarm_actions       = local.critical_alerts_actions
  ok_actions          = local.critical_alerts_actions
  treat_missing_data  = "notBreaching"

  tags = {
    Name = "ECS ASG Instance Unregistered Alarm"
  }
}

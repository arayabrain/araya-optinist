# ==================================================
# CloudWatch Log Groups for Comprehensive Monitoring
# ==================================================
resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/subscr-optinist-cloud-taskdef"
  retention_in_days = 120

  tags = {
    Name = "subscr-optinist-cloud-logs"
  }
}

resource "aws_cloudwatch_log_group" "premium_ecs" {
  name              = "/ecs/subscr-premium-optinist-cloud-taskdef"
  retention_in_days = 120

  tags = {
    Name = "subscr-premium-optinist-cloud-logs"
  }
}

# ==================================================
# SNS Topic for Critical Alert Notifications
# ==================================================
resource "aws_sns_topic" "critical_alerts" {
  name = "subscr-optinist-critical-alerts"

  tags = {
    Name = "OptiNiSt Critical Alerts"
  }
}

resource "aws_sns_topic_subscription" "critical_alerts_email" {
  topic_arn = aws_sns_topic.critical_alerts.arn
  protocol  = "email"
  endpoint  = "support@araya-optinist.com"
}

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "subscr-optinist-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = "120"
  statistic           = "Average"
  threshold           = "60"
  alarm_description   = "This metric monitors ECS CPU utilization"
  alarm_actions       = [aws_autoscaling_policy.scale_up.arn]
  dimensions = {
    ServiceName = aws_ecs_service.autoscaling.name
    ClusterName = aws_ecs_cluster.main.name
  }
}

resource "aws_cloudwatch_metric_alarm" "memory_high" {
  alarm_name          = "subscr-optinist-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = "120"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "This metric monitors memory utilization"
  alarm_actions       = [aws_autoscaling_policy.scale_up.arn]
  dimensions = {
    ServiceName = aws_ecs_service.autoscaling.name
    ClusterName = aws_ecs_cluster.main.name
  }
}

resource "aws_cloudwatch_metric_alarm" "cpu_low" {
  alarm_name          = "subscr-optinist-cpu-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = "120"
  statistic           = "Average"
  threshold           = "20"
  alarm_description   = "This metric monitors low cpu utilization"
  alarm_actions       = [aws_autoscaling_policy.scale_down.arn] # Enabled: dual CPU+memory scaling
  dimensions = {
    ServiceName = aws_ecs_service.autoscaling.name
    ClusterName = aws_ecs_cluster.main.name
  }
}

resource "aws_cloudwatch_metric_alarm" "memory_low" {
  alarm_name          = "subscr-optinist-memory-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = "120"
  statistic           = "Average"
  threshold           = "10"
  alarm_description   = "This metric monitors memory utilization"
  alarm_actions       = [aws_autoscaling_policy.scale_down.arn]
  dimensions = {
    ServiceName = aws_ecs_service.autoscaling.name
    ClusterName = aws_ecs_cluster.main.name
  }
}


resource "aws_cloudwatch_log_metric_filter" "user_cpu_usage" {
  name           = "user-cpu-usage"
  log_group_name = aws_cloudwatch_log_group.ecs.name
  pattern        = "[timestamp, level, user_id, cpu_usage]"

  metric_transformation {
    name      = "UserCPUUsage"
    namespace = "OptiNiSt/Application"
    value     = "$cpu_usage"
  }
}

# ==================================================
# Load Average Monitoring
# ==================================================
resource "aws_cloudwatch_metric_alarm" "load_average_high" {
  alarm_name          = "subscr-optinist-load-average-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "EC2 CPU utilization is high"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
  ok_actions          = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.main.name
  }
}

# ==================================================
# I/O Wait Monitoring
# ==================================================
resource "aws_cloudwatch_metric_alarm" "high_iowait" {
  alarm_name          = "subscr-optinist-high-iowait"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "cpu_usage_iowait"
  namespace           = "CWAgent"
  period              = "300"
  statistic           = "Average"
  threshold           = "30"
  alarm_description   = "High I/O wait time detected"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
  ok_actions          = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.main.name
  }
}

# ==================================================
# RDS Monitoring Alarms
# ==================================================
resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  alarm_name          = "subscr-optinist-rds-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "RDS CPU utilization is high"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
  ok_actions          = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_connections_high" {
  alarm_name          = "subscr-optinist-rds-connections-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "RDS connection count is high"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
  ok_actions          = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_storage_low" {
  alarm_name          = "subscr-optinist-rds-storage-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = "300"
  statistic           = "Average"
  threshold           = "10737418240"
  alarm_description   = "RDS free storage space is low"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
  ok_actions          = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.id
  }
}

# ==================================================
# EFS Monitoring Alarms
# ==================================================
resource "aws_cloudwatch_metric_alarm" "efs_burst_credit_balance" {
  alarm_name          = "subscr-optinist-efs-burst-credits-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "BurstCreditBalance"
  namespace           = "AWS/EFS"
  period              = "300"
  statistic           = "Average"
  threshold           = "1000000000000"
  alarm_description   = "EFS burst credits running low"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
  ok_actions          = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    FileSystemId = aws_efs_file_system.snmk.id
  }
}

resource "aws_cloudwatch_metric_alarm" "efs_throughput_high" {
  alarm_name          = "subscr-optinist-efs-throughput-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "PercentIOLimit"
  namespace           = "AWS/EFS"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "EFS approaching I/O limit"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
  ok_actions          = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    FileSystemId = aws_efs_file_system.snmk.id
  }
}

# ==================================================
# ALB Monitoring Alarms
# ==================================================
resource "aws_cloudwatch_metric_alarm" "alb_5xx_errors" {
  alarm_name          = "subscr-optinist-alb-5xx-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = "60"
  statistic           = "Sum"
  threshold           = "10"
  alarm_description   = "ALB target 5XX errors exceeded threshold"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
  ok_actions          = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    LoadBalancer = aws_lb.autoscaling.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_response_time_high" {
  alarm_name          = "subscr-optinist-alb-response-time-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = "300"
  statistic           = "Average"
  threshold           = "5"
  alarm_description   = "ALB response time is too high"
  alarm_actions       = [aws_sns_topic.critical_alerts.arn]
  ok_actions          = [aws_sns_topic.critical_alerts.arn]

  dimensions = {
    LoadBalancer = aws_lb.autoscaling.arn_suffix
  }
}

# CloudWatch Dashboard for monitoring both Free and Premium tiers
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "subscr-optinist-monitoring"

  dashboard_body = jsonencode({
    widgets = [
      # Row 1: CPU and Memory Comparison - Free vs Premium
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Free Tier CPU" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Free Tier Memory" }],
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.premium.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Premium CPU" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.premium.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Premium Memory" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Free vs Premium: CPU & Memory Utilization"
          period  = 300
          yAxis = {
            left = {
              min = 0
              max = 100
            }
          }
        }
      },
      # Row 1: Instance Counts and Capacity with Autoscaling
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/AutoScaling", "GroupDesiredCapacity", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Free Tier Desired" }],
            ["AWS/AutoScaling", "GroupInServiceInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Free Tier Running" }],
            ["AWS/AutoScaling", "GroupMinSize", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Free Tier Min" }],
            ["AWS/AutoScaling", "GroupMaxSize", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Free Tier Max" }],
            ["AWS/EC2", "InstanceCount", "InstanceId", aws_instance.premium[0].id, { "label" : "Premium 1" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Autoscaling: Free Tier Capacity Management"
          period  = 300
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # Row 2: Detailed EC2 Metrics
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Free Tier ECS CPU", "stat" : "Average" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Free Tier ECS Memory", "stat" : "Average" }],
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.premium.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Premium ECS CPU", "stat" : "Average" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.premium.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Premium ECS Memory", "stat" : "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "ECS Service Metrics: CPU & Memory"
          period  = 300
          yAxis = {
            left = {
              min = 0
              max = 100
            }
          }
        }
      },
      # Row 2: Cost Metrics
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Billing", "EstimatedCharges", "Currency", "USD", "ServiceName", "AmazonEC2", { "label" : "EC2 Costs" }],
            ["AWS/Billing", "EstimatedCharges", "Currency", "USD", "ServiceName", "AmazonECS", { "label" : "ECS Costs" }],
            ["AWS/Billing", "EstimatedCharges", "Currency", "USD", "ServiceName", "AmazonElasticLoadBalancing", { "label" : "ALB Costs" }],
            ["Optinist/CostTracking", "PremiumInstanceCount", { "label" : "Premium Instances", "yAxis" : "right" }],
            ["Optinist/CostTracking", "FreeInstanceCount", { "label" : "Free Tier Instances", "yAxis" : "right" }],
            ["Optinist/CostTracking", "PremiumUtilization", { "label" : "Premium Utilization %", "yAxis" : "right" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Cost Tracking & Instance Counts"
          period  = 3600
          stat    = "Maximum"
        }
      },
      # Row 3: Load Balancer Performance
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.autoscaling.arn_suffix, { "label" : "Free Tier Requests" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.autoscaling.arn_suffix, { "label" : "Free Tier Response Time" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_2XX_Count", "LoadBalancer", aws_lb.autoscaling.arn_suffix, { "label" : "Free Tier 2XX" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.autoscaling.arn_suffix, { "label" : "Free Tier 5XX" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "ALB Performance: Free Tier & Premium Routing"
          period  = 300
        }
      },
      # Row 3: Free and Premium Tier User Metrics
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["OptiNiSt/FreeUsers", "ActiveLogins", { "label" : "Active Free Tier Users", "yAxis" : "left" }],
            ["OptiNiSt/Premium", "ActiveAssignments", { "label" : "Active Premium Users", "yAxis" : "left" }],
            ["OptiNiSt/Premium", "InstanceUtilization", { "label" : "Premium Instance Utilization %", "yAxis" : "right" }],
            ["OptiNiSt/Application", "UserCPUUsage", { "label" : "User CPU Usage", "stat" : "Average", "yAxis" : "right" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.premium_manager.function_name, { "label" : "Premium Manager Duration (ms)", "yAxis" : "right" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.premium_manager.function_name, { "label" : "Premium Manager Errors", "yAxis" : "left" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "User Tier Operations: Free & Premium"
          period  = 300
          yAxis = {
            left = {
              label = "Count"
              min   = 0
            }
            right = {
              label = "Utilization / Duration"
              min   = 0
            }
          }
        }
      },
      # Row 4: EC2 Load Average and I/O Wait
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "EC2 CPU Utilization %", "yAxis" : "left" }],
            ["CWAgent", "cpu_usage_iowait", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "I/O Wait %", "yAxis" : "left" }],
            ["CWAgent", "system_load1", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Load Average (1 min)", "yAxis" : "right" }],
            ["CWAgent", "system_load5", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Load Average (5 min)", "yAxis" : "right" }],
            ["CWAgent", "system_load15", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Load Average (15 min)", "yAxis" : "right" }],
            ["ECS/ContainerInsights", "RunningTaskCount", "ServiceName", aws_ecs_service.background.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Background Tasks", "stat" : "Average", "yAxis" : "right" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "EC2 Performance & Background Service"
          period  = 300
          yAxis = {
            left = {
              label = "Percentage"
              min   = 0
              max   = 100
            }
            right = {
              label = "Load / Tasks"
              min   = 0
            }
          }
          annotations = {
            horizontal = [
              {
                label = "High CPU Threshold (80%)"
                value = 80
                yAxis = "left"
              },
              {
                label = "High I/O Wait Threshold (30%)"
                value = 30
                yAxis = "left"
              }
            ]
          }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.id, { "label" : "RDS CPU %", "yAxis" : "left" }],
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", aws_db_instance.main.id, { "label" : "RDS Connections", "yAxis" : "right" }],
            ["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", aws_db_instance.main.id, { "label" : "RDS Free Storage (Bytes)", "yAxis" : "right" }],
            ["AWS/EFS", "ClientConnections", "FileSystemId", aws_efs_file_system.snmk.id, { "label" : "EFS Connections", "yAxis" : "right" }],
            ["AWS/EFS", "PercentIOLimit", "FileSystemId", aws_efs_file_system.snmk.id, { "label" : "EFS I/O Limit %", "yAxis" : "left" }],
            ["AWS/EFS", "BurstCreditBalance", "FileSystemId", aws_efs_file_system.snmk.id, { "label" : "EFS Burst Credits", "yAxis" : "right" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Infrastructure Health: RDS & EFS"
          period  = 300
          yAxis = {
            left = {
              label = "Percentage"
              min   = 0
              max   = 100
            }
          }
          annotations = {
            horizontal = [
              {
                label = "RDS CPU High (80%)"
                value = 80
              },
              {
                label = "EFS I/O Limit High (80%)"
                value = 80
              }
            ]
          }
        }
      },
      # Row 5: Autoscaling Activity and Events
      {
        type   = "metric"
        x      = 0
        y      = 24
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/AutoScaling", "GroupTotalInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Total Instances" }],
            ["AWS/AutoScaling", "GroupPendingInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Pending Instances" }],
            ["AWS/AutoScaling", "GroupTerminatingInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Terminating Instances" }],
            ["AWS/AutoScaling", "GroupStandbyInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Standby Instances" }]
          ]
          view    = "timeSeries"
          stacked = true
          region  = "ap-northeast-1"
          title   = "Autoscaling Activity: Instance Lifecycle"
          period  = 300
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # Row 5: Autoscaling Metrics and Alarms
      {
        type   = "metric"
        x      = 12
        y      = 24
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["CWAgent", "mem_used_percent", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "Memory Utilization %" }],
            ["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label" : "CPU Utilization %" }],
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "ECS CPU %" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "ECS Memory %" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Autoscaling Triggers: CPU & Memory Thresholds"
          period  = 300
          yAxis = {
            left = {
              min = 0
              max = 100
            }
          }
          annotations = {
            horizontal = [
              {
                label = "CPU Scale Up (60%)"
                value = 60
              },
              {
                label = "Memory Scale Up (80%)"
                value = 80
              }
            ]
          }
        }
      },
      # Row 6: Background Jobs & User Manager
      {
        type   = "metric"
        x      = 0
        y      = 30
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["ECS/ContainerInsights", "CpuUtilized", "ServiceName", aws_ecs_service.background.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Background Service CPU", "yAxis" : "left" }],
            ["ECS/ContainerInsights", "MemoryUtilized", "ServiceName", aws_ecs_service.background.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Background Service Memory (MB)", "yAxis" : "right" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.common_user_manager.function_name, { "label" : "User Manager Duration (ms)", "yAxis" : "left" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.common_user_manager.function_name, { "label" : "User Manager Errors", "yAxis" : "right", "stat" : "Sum" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Background Jobs Performance"
          period  = 3600
          yAxis = {
            left = {
              label = "Utilization"
              min   = 0
            }
            right = {
              label = "Count / Memory"
              min   = 0
            }
          }
        }
      },
      # Row 6: Lambda Health & Cleanup Operations
      {
        type   = "metric"
        x      = 12
        y      = 30
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.free_manager.function_name, { "label" : "Free Manager Duration (ms)" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.free_manager.function_name, { "label" : "Free Manager Errors", "yAxis" : "right" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.premium_cleanup.function_name, { "label" : "Premium Cleanup Duration (ms)" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.premium_cleanup.function_name, { "label" : "Premium Cleanup Errors", "yAxis" : "right" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.free_cleanup.function_name, { "label" : "Free Cleanup Duration (ms)" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.free_cleanup.function_name, { "label" : "Free Cleanup Errors", "yAxis" : "right" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.cost_tracker.function_name, { "label" : "Cost Tracker Duration (ms)" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.cost_tracker.function_name, { "label" : "Cost Tracker Errors", "yAxis" : "right" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Lambda Operations: Manager & Cleanup Jobs"
          period  = 3600
          yAxis = {
            left = {
              label = "Duration (ms)"
              min   = 0
            }
            right = {
              label = "Error Count"
              min   = 0
            }
          }
        }
      },
      # Row 7: Alarm Status - All Alarms at a Glance
      {
        type   = "alarm"
        x      = 0
        y      = 36
        width  = 24
        height = 4
        properties = {
          title = "Alarm Status Overview"
          alarms = [
            # Background Service Alarms
            aws_cloudwatch_metric_alarm.background_task_stopped.arn,
            aws_cloudwatch_metric_alarm.background_cpu_high.arn,
            aws_cloudwatch_metric_alarm.background_memory_high.arn,
            # Premium Tier Alarms
            aws_cloudwatch_metric_alarm.premium_cost_high.arn,
            aws_cloudwatch_metric_alarm.premium_cpu_high.arn,
            aws_cloudwatch_metric_alarm.premium_memory_high.arn,
            # Autoscaling Alarms
            aws_cloudwatch_metric_alarm.cpu_high.arn,
            aws_cloudwatch_metric_alarm.memory_high.arn,
            aws_cloudwatch_metric_alarm.cpu_low.arn,
            aws_cloudwatch_metric_alarm.memory_low.arn,
            aws_cloudwatch_metric_alarm.load_average_high.arn,
            aws_cloudwatch_metric_alarm.high_iowait.arn,
            # RDS Alarms
            aws_cloudwatch_metric_alarm.rds_cpu_high.arn,
            aws_cloudwatch_metric_alarm.rds_connections_high.arn,
            aws_cloudwatch_metric_alarm.rds_storage_low.arn,
            # EFS Alarms
            aws_cloudwatch_metric_alarm.efs_burst_credit_balance.arn,
            aws_cloudwatch_metric_alarm.efs_throughput_high.arn,
            # ALB Alarms
            aws_cloudwatch_metric_alarm.alb_5xx_errors.arn,
            aws_cloudwatch_metric_alarm.alb_response_time_high.arn
          ]
        }
      }
    ]
  })
}

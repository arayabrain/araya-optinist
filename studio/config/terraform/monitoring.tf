# ==================================================
# CloudWatch Log Groups for Comprehensive Monitoring
# ==================================================
resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/subscr-optinist-cloud-taskdef"
  retention_in_days = 7

  tags = {
    Name = "subscr-optinist-cloud-logs"
  }
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
  namespace           = "AWS/AutoScaling"
  period              = "120"
  statistic           = "Average"
  threshold           = "20"
  alarm_description   = "This metric monitors low cpu utilization"
  alarm_actions       = [aws_autoscaling_policy.scale_down.arn] # Enabled: dual CPU+memory scaling
  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.main.name
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
      # Row 3: Premium-specific Metrics
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["OptiNiSt/Premium", "ActiveAssignments", { "label" : "Active Premium Users" }],
            ["OptiNiSt/Premium", "InstanceUtilization", { "label" : "Premium Instance Utilization %" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.premium_manager.function_name, { "label" : "Premium Manager Duration" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.premium_manager.function_name, { "label" : "Premium Manager Errors" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Premium Tier Operations"
          period  = 300
        }
      },
      # Row 4: Batch Processing Metrics
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.batch.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Batch CPU" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.batch.name, "ClusterName", aws_ecs_cluster.main.name, { "label" : "Batch Memory" }],
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.batch.arn_suffix, { "label" : "Batch Requests" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.batch.arn_suffix, { "label" : "Batch Response Time" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Batch Processing Performance"
          period  = 300
        }
      },
      # Row 4: System Health Overview
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.id, { "label" : "RDS CPU" }],
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", aws_db_instance.main.id, { "label" : "RDS Connections" }],
            ["AWS/EFS", "ClientConnections", "FileSystemId", aws_efs_file_system.snmk.id, { "label" : "EFS Connections" }],
            ["AWS/EFS", "DataReadIOBytes", "FileSystemId", aws_efs_file_system.snmk.id, { "label" : "EFS Read I/O" }],
            ["AWS/EFS", "DataWriteIOBytes", "FileSystemId", aws_efs_file_system.snmk.id, { "label" : "EFS Write I/O" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Infrastructure Health: RDS & EFS"
          period  = 300
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
      }
    ]
  })
}

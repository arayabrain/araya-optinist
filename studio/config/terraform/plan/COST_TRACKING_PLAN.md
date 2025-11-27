# Cost Tracking Implementation Summary

## Overview
Automated cost tracking system for monitoring AWS resource usage and costs across free tier and premium instances using Lambda functions, CloudWatch metrics, and EventBridge scheduling.

---

## Architecture

### Components
1. **Cost Tracker Lambda** (`subscr-cost-tracker`)
   - Runtime: Python 3.9
   - Timeout: 300 seconds
   - Trigger: EventBridge hourly schedule
   - Purpose: Track instance counts, calculate utilization, publish CloudWatch metrics

2. **CloudWatch Custom Metrics**
   - Namespace: `Optinist/CostTracking`
   - Metrics:
     - `PremiumInstanceCount` - Running premium instances
     - `FreeInstanceCount` - Running free tier instances
     - `PremiumUtilization` - Utilization percentage

3. **Additional Cost Namespace**
   - Namespace: `OptiNiSt/Cost`
   - Metric: `TotalMonthlyCost`
   - CloudWatch alarm threshold: $500/month

4. **Premium Monitoring Namespace**
   - Namespace: `OptiNiSt/Premium`
   - Metrics:
     - `ActiveAssignments` - Active premium users
     - `InstanceUtilization` - Premium instance utilization %

---

## Lambda Function Details

### Cost Tracker (`cost_tracker.py`)

**Functionality:**
- Tracks premium instances via Spot Fleet
- Tracks free tier instances via Auto Scaling Group
- Calculates premium utilization metrics
- Publishes metrics to CloudWatch

**Environment Variables:**
- `ASG_NAME` - Auto Scaling Group name
- `REGION` - AWS region (ap-northeast-1)
- `INSTANCE_TYPE` - Instance type (t3.large)

**Key Functions:**
```python
track_premium_instances()  # Monitor spot fleet instances
track_free_instances()     # Monitor ASG instances
calculate_premium_utilization()  # Calculate utilization %
publish_cost_metrics()     # Publish to CloudWatch
```

**Error Handling:**
- Graceful degradation (returns 0 counts on errors)
- Comprehensive logging for debugging
- HTTP 500 response on failure

---

## EventBridge Schedule

**Rule:** `subscr-cost-tracker-schedule`
- Schedule: Hourly (`rate(1 hour)`)
- Target: Cost Tracker Lambda
- Purpose: Regular cost metric updates

---

## CloudWatch Dashboard Integration

### Dashboard Widget: "Cost Tracking & Instance Counts"
**Location:** Row 2 of main monitoring dashboard

**Metrics Displayed:**
1. **AWS Billing Metrics:**
   - EC2 Costs (USD)
   - ECS Costs (USD)
   - ALB Costs (USD)

2. **Custom Instance Tracking:**
   - Premium Instance Count (right Y-axis)
   - Free Tier Instance Count (right Y-axis)
   - Premium Utilization % (right Y-axis)

**Configuration:**
- Period: 1 hour (3600s)
- Statistic: Maximum
- View: Time series (not stacked)

---

## CloudWatch Alarms

### Premium Cost High Alarm
**Name:** `subscr-premium-monthly-cost-high`
- Metric: `TotalMonthlyCost`
- Namespace: `OptiNiSt/Cost`
- Threshold: $500
- Evaluation: Daily (86400s period)
- Statistic: Maximum
- Actions: None (monitoring only)

---

## Terraform Resources

### Files Modified:
- `premium_manager.tf` - Lambda function, IAM roles, EventBridge rules
- `monitoring.tf` - Dashboard integration, cost tracking widgets

### Key Resources:
```hcl
aws_lambda_function.cost_tracker
aws_cloudwatch_event_rule.cost_tracker_schedule
aws_cloudwatch_event_target.cost_tracker
aws_lambda_permission.allow_eventbridge_cost_tracker
aws_cloudwatch_metric_alarm.premium_cost_high
```

---

## Metrics Flow

```
EventBridge (hourly)
    ↓
Cost Tracker Lambda
    ↓
    ├─→ Query Spot Fleet (Premium)
    ├─→ Query ASG (Free Tier)
    ├─→ Calculate Utilization
    └─→ Publish to CloudWatch
         ↓
CloudWatch Metrics (Optinist/CostTracking)
    ↓
Dashboard Visualization
```
---

## Monitoring & Observability

### Lambda Logs
- Log group: `/aws/lambda/subscr-cost-tracker`
- Retention: Configured via Terraform
- Key log events:
  - Instance count tracking
  - Metric publishing confirmation
  - Error conditions

### CloudWatch Metrics
- **Update Frequency:** Hourly
- **Retention:** Standard CloudWatch retention
- **Granularity:** 1-hour periods

### Dashboard Access
- Dashboard: `subscr-optinist-monitoring`
- Widget: "Cost Tracking & Instance Counts"
- Real-time cost visibility

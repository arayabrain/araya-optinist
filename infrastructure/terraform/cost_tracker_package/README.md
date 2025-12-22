# Cost Tracker Lambda

## Overview
Tracks resource usage and costs for premium and free tier instances. Publishes metrics to CloudWatch for monitoring and alerting.

## Primary Responsibilities
- **Premium Instance Tracking**: Monitor spot fleet instances
- **Free Instance Tracking**: Monitor auto scaling group instances
- **Utilization Calculation**: Calculate resource utilization percentages
- **Metrics Publishing**: Publish cost metrics to CloudWatch

## Triggers
- **CloudWatch Events**: Scheduled (configurable interval)

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    COST TRACKER                             │
│                  (Scheduled Run)                            │
└─────────────────────────────────────────────────────────────┘
                              │
                              │
              ┌───────────────▼───────────────┐
              │                               │
              │  1. Get Environment Config    │
              │     - Spot Fleet ID           │
              │     - ASG Name                │
              │     - Region                  │
              │                               │
              └───────────────┬───────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        │                     │                     │
┌───────▼────────┐  ┌─────────▼────────┐  ┌────────▼────────┐
│ PREMIUM        │  │ FREE TIER        │  │ UTILIZATION     │
│ TRACKING       │  │ TRACKING         │  │ CALCULATION     │
│                │  │                  │  │                 │
│ • Query Spot   │  │ • Query ASG      │  │ • Total         │
│   Fleet        │  │ • Count          │  │   Instances     │
│ • Count        │  │   Instances      │  │ • Running       │
│   Instances    │  │ • Check Health   │  │   Instances     │
│ • Check Health │  │                  │  │ • Calculate %   │
│                │  │                  │  │                 │
└───────┬────────┘  └─────────┬────────┘  └────────┬────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
              ┌───────────────▼───────────────┐
              │                               │
              │  PUBLISH TO CLOUDWATCH        │
              │                               │
              │  Metrics:                     │
              │  • PremiumInstanceCount       │
              │  • FreeInstanceCount          │
              │  • PremiumUtilization         │
              │                               │
              │  Namespace:                   │
              │  Optinist/CostTracking        │
              │                               │
              └───────────────────────────────┘
```

## Related Files

### Core Files
- `cost_tracker.py` - Main Lambda function

### Terraform Configuration
- `monitoring.tf` - Lambda infrastructure and CloudWatch configuration
- `infrastructure.tf` - VPC, subnets, security groups

### Related Lambdas
- `premium_manager.py` - Premium tier management
- `free_manager.py` - Free tier management

## Key Environment Variables
- `SPOT_FLEET_ID` - Spot fleet request ID for premium instances
- `ASG_NAME` - Auto Scaling Group name for free tier instances
- `REGION` - AWS region (default: ap-northeast-1)

## CloudWatch Metrics

### Namespace
`Optinist/CostTracking`

### Metrics Published

| Metric Name | Description | Unit |
|------------|-------------|------|
| `PremiumInstanceCount` | Number of running premium instances | Count |
| `FreeInstanceCount` | Number of running free tier instances | Count |
| `PremiumUtilization` | Premium resource utilization percentage | Percent |

## Utilization Calculation

Current implementation uses a simple formula:
```python
utilization = min(100, total_instances * 80)
```

This assumes 80% utilization per instance. In production, this should be enhanced to:
- Query actual user assignments from RDS
- Calculate real utilization based on active users
- Consider CPU/memory metrics from CloudWatch

## Monitoring Use Cases

### Cost Alerts
Set CloudWatch alarms on instance counts:
```
PremiumInstanceCount > 10  → Alert: High premium usage
FreeInstanceCount > 5      → Alert: High free tier usage
```

### Utilization Tracking
Monitor utilization trends:
```
PremiumUtilization < 50%   → Alert: Underutilized resources
PremiumUtilization > 90%   → Alert: Near capacity
```

### Cost Dashboard
Create CloudWatch dashboard with:
- Instance count trends (premium vs free)
- Utilization over time
- Cost projections based on instance hours

## Future Enhancements

1. **Real User Tracking**: Query RDS for actual user assignments
2. **Cost Calculation**: Integrate with AWS Cost Explorer API
3. **Detailed Metrics**: Per-instance CPU, memory, network usage
4. **Anomaly Detection**: Alert on unusual usage patterns
5. **Cost Optimization**: Recommend instance type changes
6. **Historical Analysis**: Store metrics in S3 for long-term analysis

## Response Format

Success response:
```json
{
  "statusCode": 200,
  "body": {
    "message": "Cost tracking completed",
    "premium_metrics": {
      "instance_count": 5,
      "running_instances": 5,
      "spot_fleet_id": "sfr-..."
    },
    "free_metrics": {
      "instance_count": 2,
      "running_instances": 2,
      "asg_name": "subscr-free-asg"
    },
    "utilization": 80,
    "timestamp": "2024-01-15T10:30:00.000Z"
  }
}
```

Error response:
```json
{
  "statusCode": 500,
  "body": {
    "message": "Cost tracking failed: <error details>",
    "timestamp": "2024-01-15T10:30:00.000Z"
  }
}
```

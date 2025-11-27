### Summary

As an alternative to AWS batch, autoscaling can be used.

### Tasks
Enhanced Autoscaling Infrastructure ✅ **COMPLETED**

1.1 Auto Scaling Group Optimization
  - 1.1.1 ✅ **IMPLEMENTED** Add missing CloudWatch alarms for CPU/Memory thresholds
    - Files to modify: main.tf
    - Status: ✅ All CloudWatch alarms implemented with proper thresholds
    - Implementation: CloudWatch alarms added and connected to scaling policies
    - Details:
    - ✅ aws_cloudwatch_metric_alarm.cpu_high (threshold: 60%) - IMPLEMENTED
    - ✅ aws_cloudwatch_metric_alarm.cpu_low (threshold: 20%) - IMPLEMENTED
    - ✅ aws_cloudwatch_metric_alarm.memory_high (threshold: 80%) - IMPLEMENTED
    - ✅ aws_cloudwatch_metric_alarm.memory_low (threshold: 10%) - IMPLEMENTED

  - 1.1.2 - ECS Capacity Provider
  **Intentionally NOT IMPLEMENTED** - Enable ECS Capacity Provider managed scaling
  - Status: Intentionally disabled (status = "DISABLED")
  - Rationale: CloudWatch-based autoscaling proved more robust. ECS Capacity Provider didn't handle startup CPU/memory spikes well, triggering cascading instance launches.

  - 1.1.3 - Dynamic Scaling Policies
    ✅ **IMPLEMENTED** - Configure dynamic scaling policies
    - Implementation: Dual-layer autoscaling system
      - ASG Layer: CloudWatch alarms (CPU 60%, Memory 80%) → instance scaling (300s cooldown)
      - ECS Layer: Target tracking (CPU 60%, Memory 80%) → task count scaling (60s scale-out, 300s scale-in)
    - Test Coverage: test_autoscaling.py validates ASG layer only
    - Note: ECS service autoscaling operates independently (1-3 task capacity)
    - Files: compute.tf (ECS autoscaling), monitoring.tf  (CloudWatch alarms)

  - 1.1.4 ✅ **IMPLEMENTED** Optimize instance warmup periods and cooldown timers
    - Files: compute.tf (ASG default_cooldown), compute.tf (scaling policies)
    - Implementation: ⚠️ Using 300s cooldown - could be optimized
    - Improvements: Performance optimization needed based on real-world usage up-down statistics
    - Details: Balance between responsiveness and stability
    **- This was a considerable issue in previous testing and may take many days for optimisation**

  - 1.1.5 - Load Testing
    ✅ **IMPLEMENTED** - Test scaling behavior under load simulation
    - Test: test_autoscaling.py
    - Coverage:
      - ✅ ASG capacity changes and CloudWatch alarms
      - ✅ Load generation and task distribution tracking
      - ⚠️ LIMITATION: Only monitors ASG, not ECS service task count
    - Gap: No validation of ECS Application Autoscaling (separate mechanism)

1.2 Application Load Balancer Enhancement
  - 1.2.1 ✅ **IMPLEMENTED** Sticky sessions enabled for session continuity
    - Files: compute.tf , batch.tf (Target Group stickiness configuration)
    - Status: ✅ Sticky sessions enabled on target groups
    - Implementation: ✅ Target groups have stickiness enabled=true (lb_cookie, 86400s duration)
    - Rationale: Required to preserve unsaved workflow state in frontend Redux store
    - Details: Frontend stores workflow data in-memory only, sticky sessions prevent data loss on page refresh

  - 1.2.2 ✅ **IMPLEMENTED** Set up ALB access logs and monitoring
    - Status: ✅ Access logs enabled with S3 bucket configuration
    - Implementation: ✅ ALB access logs configured (S3 bucket with alb-logs prefix)

  - 1.2.3 - ⚠️ **PARTIAL** - Test load distribution across multiple instances
    - Test: test_autoscaling.py includes task distribution analysis
    - Gap: No validation of ALB health checks or ECS task routing during scaling

1.3 Infrastructure Monitoring Setup
- 1.3.1 ✅ **IMPLEMENTED** Enhanced CloudWatch dashboard with comprehensive autoscaling metrics
  - Status: ✅ Complete dashboard with free tier, premium tier, and autoscaling monitoring
  - Implementation: ✅ Enhanced dashboard with 5 rows of comprehensive metrics
  - Details: ✅ Added autoscaling capacity, instance lifecycle, scaling triggers, and cost tracking

  **Dashboard Features Implemented:**
  - **Row 1**: Free vs Premium CPU/Memory comparison + Autoscaling capacity management
  - **Row 2**: EC2 host metrics + Cost tracking with daily spot savings calculation
  - **Row 3**: Load balancer performance + Premium operations monitoring
  - **Row 4**: Batch processing metrics + Infrastructure health (RDS/EFS)
  - **Row 5**: Autoscaling activity/lifecycle + Scaling trigger thresholds with annotations

  **Autoscaling Metrics Added:**
  - GroupDesiredCapacity, GroupInServiceInstances, GroupMinSize, GroupMaxSize
  - GroupTotalInstances, GroupPendingInstances, GroupTerminatingInstances
  - CPU/Memory thresholds with scale-up annotations (60% CPU, 80% Memory)
  - Instance lifecycle tracking with stacked visualization

  **Cost Tracking Features:**
  - Daily cost tracking Lambda (`subscr-cost-tracker`) with AWS Pricing API integration
  - Real-time spot savings calculation vs on-demand pricing
  - Monthly cost estimation for both free and premium tiers
  - Custom CloudWatch metrics in `OptiNiSt/Cost` and `OptiNiSt/Premium` namespaces

## Critical Missing Tests

### Priority 1: Production Critical
- ECS Service Autoscaling validation (task count scaling, target tracking policies)
- Dual-layer coordination testing (ASG + ECS working together, no conflicts)
- ALB health check integration (/health endpoint, unhealthy instance removal)

### Priority 2: Reliability
- CloudWatch alarm state transitions and cooldown enforcement
- Edge cases (max capacity boundaries, rapid oscillations, launch failures)
- Premium/Free tier separation (independent scaling, placement constraints)
- Enhanced test_autoscaling.py to monitor both ASG and ECS metrics

#!/bin/bash
# Shared helper: read ASG target lifecycle state via IMDS.
# Returns the lifecycle state on stdout, or empty if IMDS is unreachable or
# the metadata key isn't populated yet — both safe to treat as "OK".
TOKEN=$(curl -s -m 2 -X PUT \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60" \
  http://169.254.169.254/latest/api/token 2>/dev/null || true)
[ -z "$TOKEN" ] && exit 0
curl -s -m 2 -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/autoscaling/target-lifecycle-state \
  2>/dev/null || true

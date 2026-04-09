#!/bin/bash
# Shared helper: read ASG target lifecycle state via IMDS.
# Returns the lifecycle state on stdout, or empty if IMDS is unreachable or
# the metadata key isn't populated yet — both safe to treat as "OK".
curl -s -m 2 \
  http://169.254.169.254/latest/meta-data/autoscaling/target-lifecycle-state \
  2>/dev/null || true

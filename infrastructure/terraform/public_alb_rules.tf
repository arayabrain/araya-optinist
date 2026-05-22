# ALB listener rules for the multi-tier split. Lower priority wins.
# 100-199: premium dynamic rules (created by premium-manager Lambda)
# 200-312: public-bound (system-internal sync, /api/public/*, /auth/*,
#          /users/me/*, /log-report/*, assets)
# 315-320: free-bound (authenticated own-data, anonymous flows, Authorization catch-all)
# default: public TG (see compute.tf)

resource "aws_lb_listener_rule" "sync_experiment_to_public" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 200

  condition {
    path_pattern {
      values = ["/system-internal/sync-experiment/*"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn
  }
}

resource "aws_lb_listener_rule" "sync_experiments_to_free" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 210

  condition {
    path_pattern {
      values = ["/system-internal/sync-experiments/*"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn # free TG
  }
}

# Frontend sets DATAVIEW_PUBLIC_REQUEST on /outputs/* requests made from
# /public/* pages (see frontend/src/utils/DataviewUtils.ts). Carves public-
# dataview reads onto the public TG; own-data reads fall through to p315.
# TODO: After #636 (path rename to /api/visualizations/*), update both rules together.
resource "aws_lb_listener_rule" "outputs_public_header" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 280

  condition {
    path_pattern {
      values = ["/outputs/*"]
    }
  }

  condition {
    http_header {
      http_header_name = "DATAVIEW_PUBLIC_REQUEST"
      values           = ["true"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn
  }
}

# Bare path enumerated alongside the wildcard: ALB "*" requires a preceding "/".
resource "aws_lb_listener_rule" "public_dataview_api" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 300

  condition {
    path_pattern {
      values = [
        "/api/public/dataview",
        "/api/public/dataview/*",
      ]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn
  }
}

# Hosted on public so authentication survives a free-tier outage.
resource "aws_lb_listener_rule" "auth_to_public" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 305

  condition {
    path_pattern {
      values = ["/auth/*"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn
  }
}

# Bootstrap calls the SPA issues right after /auth/login: GET /users/me, the
# premium-assign / heartbeat / beacon endpoints, etc. Without this rule those
# requests would hit p320's Bearer catch-all and 503 during a free outage,
# leaving the user with a valid JWT but an unusable app.
resource "aws_lb_listener_rule" "users_me_to_public" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 306

  condition {
    path_pattern {
      values = [
        "/users/me",
        "/users/me/*",
      ]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn
  }
}

# Frontend error reporting survives a free outage so client-side errors still
# reach CloudWatch. The log *viewer* (/logs) stays on its owning tier (premium
# via p100-199 routing-id, free via p320 Bearer catch-all) — users should never
# see public-task logs mixed into their own.
resource "aws_lb_listener_rule" "log_report_to_public" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 307

  condition {
    path_pattern {
      values = ["/log-report/*"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn
  }
}

resource "aws_lb_listener_rule" "static_assets_to_public" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 310

  condition {
    path_pattern {
      values = [
        "/static/*",
        "/images/*",
        "/favicon.ico",
        "/manifest.json",
        "/robots.txt",
      ]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn
  }
}

# Split across two rules: ALB caps path_pattern values at 5 per rule.
resource "aws_lb_listener_rule" "docs_to_public" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 311

  condition {
    path_pattern {
      values = [
        "/docs",
        "/docs/*",
        "/openapi",
        "/redoc",
        "/health",
      ]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn
  }
}

resource "aws_lb_listener_rule" "asset_manifest_to_public" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 312

  condition {
    path_pattern {
      values = ["/asset-manifest.json"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.public.arn
  }
}

resource "aws_lb_listener_rule" "outputs_authenticated_to_free" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 315

  condition {
    path_pattern {
      values = ["/outputs/*"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn # free TG
  }
}

# Anonymous flows (no Authorization header) — must precede the Bearer catch-all.
resource "aws_lb_listener_rule" "anonymous_flows_to_free" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 316

  condition {
    path_pattern {
      values = [
        "/api/register/*",
        "/api/subsc/webhooks/*",
      ]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn # free TG
  }
}

# Catch-all for authenticated traffic; premium-tagged requests match earlier.
resource "aws_lb_listener_rule" "authenticated_to_free" {
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 320

  condition {
    http_header {
      http_header_name = "Authorization"
      values           = ["Bearer *"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn # free TG
  }
}

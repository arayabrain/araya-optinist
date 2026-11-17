# =======
# SSL/TLS & Custom Domain Resources
# =======
# Only created when enable_custom_domain = true

# Reference to existing Route53 hosted zone
data "aws_route53_zone" "main" {
  count        = var.enable_custom_domain ? 1 : 0
  name         = var.frontend_domain
  private_zone = false
}

# SSL/TLS certificate for HTTPS support
resource "aws_acm_certificate" "main" {
  count             = var.enable_custom_domain ? 1 : 0
  domain_name       = var.frontend_domain
  validation_method = "DNS"

  subject_alternative_names = [
    "*.${var.frontend_domain}" # Support subdomains
  ]

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.frontend_domain} certificate"
  }
}

# DNS validation record for ACM certificate
resource "aws_route53_record" "cert_validation" {
  for_each = var.enable_custom_domain ? {
    for dvo in aws_acm_certificate.main[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = data.aws_route53_zone.main[0].zone_id
}

# Wait for certificate validation to complete
resource "aws_acm_certificate_validation" "main" {
  count                   = var.enable_custom_domain ? 1 : 0
  certificate_arn         = aws_acm_certificate.main[0].arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

# Route53 A record pointing to ALB
resource "aws_route53_record" "main" {
  count   = var.enable_custom_domain ? 1 : 0
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = var.frontend_domain
  type    = "A"

  alias {
    name                   = aws_lb.autoscaling.dns_name
    zone_id                = aws_lb.autoscaling.zone_id
    evaluate_target_health = true
  }
}

# Route53 A record for www, the host the app is served from (apex_to_www below)
resource "aws_route53_record" "www" {
  count   = var.enable_custom_domain ? 1 : 0
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = "www.${var.frontend_domain}"
  type    = "A"

  alias {
    name                   = aws_lb.autoscaling.dns_name
    zone_id                = aws_lb.autoscaling.zone_id
    evaluate_target_health = true
  }
}

# Sessions and client storage are per host, so the app is served from www
# only, and page loads on the apex are sent there. Only page loads match: API
# calls, webhook POSTs and scripts on the apex keep reaching the app. The host
# match is exact, so callers that use the ALB DNS name are never redirected.
resource "aws_lb_listener_rule" "apex_to_www" {
  count        = var.enable_custom_domain ? 1 : 0
  listener_arn = aws_lb_listener.autoscaling_https.arn
  priority     = 20 # see the priority map in public_alb_rules.tf

  # After the image rollout, so no page load is sent to www while an older
  # bundle that calls a fixed API host still serves
  depends_on = [null_resource.deploy_to_ecs]

  condition {
    host_header {
      values = [var.frontend_domain]
    }
  }

  condition {
    http_request_method {
      values = ["GET", "HEAD"]
    }
  }

  # Browsers send text/html only when loading a page, never from fetch/XHR
  condition {
    http_header {
      http_header_name = "Accept"
      values           = ["*text/html*"]
    }
  }

  action {
    type = "redirect"

    redirect {
      host        = "www.${var.frontend_domain}"
      path        = "/#{path}"
      query       = "#{query}"
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

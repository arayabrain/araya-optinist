# =============
# Setup scripts
# =============

resource "null_resource" "build_and_deploy" {
  depends_on = [aws_lb.autoscaling, aws_ecs_service.autoscaling]

  triggers = {
    alb_dns = aws_lb.autoscaling.dns_name
    # Force rebuild when git branch changes
    git_branch = var.git_branch
    # Force rebuild when ECR repo changes
    ecr_repo = local.ecr_repository_url
    # Force rebuild when code changes. Without this the trigger set is
    # constant across commits on a branch, so a routine apply skips the
    # image build entirely.
    source_revision = data.external.tf_build_info.result.git_commit
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      echo "=== Starting automated build and deploy ==="
      echo "ALB DNS: ${aws_lb.autoscaling.dns_name}"

      # Build and push image
      echo "Building and pushing Docker image..."
      chmod +x ../scripts/ecr_build_push.sh
      ../scripts/ecr_build_push.sh --yes

      echo "Waiting for ECR image to be available..."
      sleep 60

      echo "Build and push completed successfully"

    EOT
  }
}

# ============================================================================
# Application Setup Script
# ============================================================================
# The app_setup.sh script is now a static file in infrastructure/scripts/
# It reads all secrets from AWS Secrets Manager at runtime, enabling
# deployment by team members without access to terraform.tfvars
#
# The script is uploaded to S3 and executed via AWS Systems Manager
# ============================================================================

# Note: The static app_setup.sh file is located at:
# infrastructure/scripts/app_setup.sh
#
# It dynamically fetches:
# - Firebase configuration from Secrets Manager
# - Database credentials from Secrets Manager
# - Application secrets from Secrets Manager
# - RDS endpoint via AWS CLI
# - S3 bucket name via AWS CLI
#
# This was previously a Terraform-generated file (local_file resource)
# but has been converted to a static file for easier deployment without
# terraform.tfvars access.

# Static app_setup.sh file location
locals {
  app_setup_script_path = "${path.module}/../scripts/app_setup.sh"
}

# Upload the setup script to S3 so SSM can download it
resource "aws_s3_object" "app_setup_script" {
  bucket = aws_s3_bucket.app_storage.id
  key    = "scripts/app_setup.sh"
  source = local.app_setup_script_path
  etag   = filemd5(local.app_setup_script_path)

  tags = {
    Name = "OptiNiSt App Setup Script"
  }
}

# SSM document to run setup script
resource "aws_ssm_document" "app_setup" {
  name            = "${local.env_prefix}-app-setup"
  document_type   = "Command"
  document_format = "YAML"

  depends_on = [aws_s3_object.app_setup_script]

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Application setup for OptiNiSt instances"
    parameters    = {}
    mainSteps = [
      {
        action = "aws:downloadContent"
        name   = "downloadSetupScript"
        inputs = {
          sourceType = "S3"
          sourceInfo = jsonencode({
            path = "https://s3.amazonaws.com/${aws_s3_bucket.app_storage.id}/scripts/app_setup.sh"
          })
          destinationPath = "/tmp"
        }
      },
      {
        action = "aws:runShellScript"
        name   = "runSetupScript"
        inputs = {
          timeoutSeconds = "3600"
          runCommand = [
            "chmod +x /tmp/app_setup.sh",
            "ENV_PREFIX=${var.environment} S3_BUCKET_NAME=${aws_s3_bucket.app_storage.id} RDS_PROXY_ENDPOINT=${aws_db_proxy.main.endpoint} /tmp/app_setup.sh"
          ]
        }
      }
    ]
  })
}

resource "aws_ssm_association" "app_setup" {
  name = aws_ssm_document.app_setup.name

  targets {
    key    = "tag:aws:autoscaling:groupName"
    values = [aws_autoscaling_group.main.name]
  }

  schedule_expression = "rate(30 minutes)"
  max_concurrency     = "1"
  max_errors          = "0"

  compliance_severity = "HIGH"
}

# Docker hygiene: prune stopped containers and unused images on every ECS
# host, so deploys cannot fill the root volume. The script is inlined (no S3
# round trip, so it also runs on hosts whose download would fail). The ECS
# agent's own cleanup forgets what it pulled whenever premium wipes its
# checkpoint on boot, so this runs regardless.
resource "aws_ssm_document" "docker_hygiene" {
  name            = "${local.env_prefix}-docker-hygiene"
  document_type   = "Command"
  document_format = "YAML"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Prune docker containers and images on OptiNiSt ECS hosts"
    parameters    = {}
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "runDockerHygiene"
        inputs = {
          timeoutSeconds = "600"
          runCommand = [
            "cat > /usr/local/sbin/docker_hygiene.sh <<'HYGIENE_EOF'",
            file("${path.module}/../scripts/docker_hygiene.sh"),
            "HYGIENE_EOF",
            "chmod 0755 /usr/local/sbin/docker_hygiene.sh",
            "/usr/local/sbin/docker_hygiene.sh",
          ]
        }
      }
    ]
  })
}

# Keyed on the launch template ids: every host launched from one of the four
# templates, the Lambda-created premium hosts included, carries this tag. A
# replaced template (new id) drops hosts launched from the old id until they
# are recycled. The NAT instance has no template and is left alone.
resource "aws_ssm_association" "docker_hygiene" {
  name = aws_ssm_document.docker_hygiene.name

  targets {
    key = "tag:aws:ec2launchtemplate:id"
    values = [
      aws_launch_template.ecs.id,
      aws_launch_template.premium.id,
      aws_launch_template.background.id,
      aws_launch_template.public.id,
    ]
  }

  schedule_expression = "rate(6 hours)"
  max_concurrency     = "25%"
  max_errors          = "100%"

  # exit 2 (root still over 80 % after pruning) marks the host non-compliant:
  # a flag on the State Manager dashboard, not an alarm
  compliance_severity = "MEDIUM"
}

output "docker_hygiene_association_id" {
  description = "SSM association that prunes docker on every ECS host; run it with start-associations-once after a deploy"
  value       = aws_ssm_association.docker_hygiene.association_id
}


resource "null_resource" "deploy_to_ecs" {
  depends_on = [null_resource.build_and_deploy]

  triggers = {
    git_branch = var.git_branch
    ecr_repo   = local.ecr_repository_url
    # depends_on orders the two but does not force replacement, so without
    # this the redeployment does not run after a freshly pushed image. The
    # build's id changes whenever it is recreated, which is exactly when a
    # new image exists to roll out.
    build_id = null_resource.build_and_deploy.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      echo "=== Starting ECS deployment ==="

      # Force a new deployment on EVERY service in the cluster (main, premium,
      # public, background) so all tiers re-pull the just-pushed :latest, not
      # only the main service.
      echo "Forcing ECS deployment on all services in ${aws_ecs_cluster.main.name}..."
      SERVICES=$(aws ecs list-services \
        --cluster ${aws_ecs_cluster.main.name} \
        --region ${var.aws_region} \
        --query 'serviceArns[]' --output text)
      if [ -z "$SERVICES" ]; then
        echo "ERROR: no ECS services found in ${aws_ecs_cluster.main.name}"
        exit 1
      fi
      for svc in $SERVICES; do
        echo "  -> $${svc##*/}"
        aws ecs update-service \
          --cluster ${aws_ecs_cluster.main.name} \
          --service "$svc" \
          --force-new-deployment \
          --region ${var.aws_region} >/dev/null
      done

      # One `aws ecs wait` stops at 10 minutes, shorter than a public rollout
      echo "Waiting for all services to stabilize (up to 60 minutes)..."
      DEADLINE=$(( $(date +%s) + 3600 ))
      until aws ecs wait services-stable \
        --cluster ${aws_ecs_cluster.main.name} \
        --services $SERVICES \
        --region ${var.aws_region}; do
        if [ "$(date +%s)" -ge "$DEADLINE" ]; then
          echo "ERROR: services not stable after 60 minutes; the new image may not be serving."
          aws ecs describe-services \
            --cluster ${aws_ecs_cluster.main.name} \
            --services $SERVICES \
            --region ${var.aws_region} \
            --query 'services[?!(length(deployments) == `1` && runningCount == desiredCount)].[serviceName, events[0].message]' \
            --output text || true
          echo "Check those services in the ECS console. deploy_to_ecs is marked tainted,"
          echo "so the next terraform apply forces a new deployment on every service again."
          exit 1
        fi
        echo "Still rolling out, waiting again..."
        sleep 15
      done

      # Each host now holds the image this deploy replaced; prune it now
      # rather than at the next scheduled run
      echo "Triggering docker hygiene on all hosts..."
      aws ssm start-associations-once \
        --association-ids ${aws_ssm_association.docker_hygiene.association_id} \
        --region ${var.aws_region} || echo "Warning: could not trigger docker hygiene; it runs on its 6 h schedule"

      echo "=== DEPLOYMENT COMPLETE ==="
      echo "Application is ready at: http://${aws_lb.autoscaling.dns_name}"
      echo "Health check: http://${aws_lb.autoscaling.dns_name}/health"
    EOT
  }
}

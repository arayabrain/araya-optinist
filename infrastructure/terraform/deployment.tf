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
    ecr_repo = var.ecr_repository_url
    # Force rebuild when code changes
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      echo "=== Starting automated build and deploy ==="
      echo "ALB DNS: ${aws_lb.autoscaling.dns_name}"

      # Build and push image
      echo "Building and pushing Docker image..."
      chmod +x ../scripts/ecr_build_push.sh
      ../scripts/ecr_build_push.sh

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
            "ENV_PREFIX=${var.environment} /tmp/app_setup.sh"
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


resource "null_resource" "deploy_to_ecs" {
  depends_on = [null_resource.build_and_deploy]

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      echo "=== Starting ECS deployment ==="

      # Force ECS deployment
      echo "Forcing ECS service deployment..."
      aws ecs update-service \
        --cluster ${aws_ecs_cluster.main.name} \
        --service ${aws_ecs_service.autoscaling.name} \
        --force-new-deployment \
        --region ${var.aws_region}

      echo "Waiting for ECS service to stabilize..."
            # Check if service is already running first
            SERVICE_STATUS=$(aws ecs describe-services \
              --cluster ${aws_ecs_cluster.main.name} \
              --services ${aws_ecs_service.autoscaling.name} \
              --region ${var.aws_region} \
              --query 'services[0].status' --output text)

            if [ "$SERVICE_STATUS" = "ACTIVE" ]; then
              echo "Service is already active, checking running count..."
              RUNNING_COUNT=$(aws ecs describe-services \
                --cluster ${aws_ecs_cluster.main.name} \
                --services ${aws_ecs_service.autoscaling.name} \
                --region ${var.aws_region} \
                --query 'services[0].runningCount' --output text)

              if [ "$RUNNING_COUNT" -gt "0" ]; then
                echo "Service already has $RUNNING_COUNT running tasks"
              else
                echo "Waiting for service to stabilize..."
                timeout 1800 aws ecs wait services-stable \
                  --cluster ${aws_ecs_cluster.main.name} \
                  --services ${aws_ecs_service.autoscaling.name} \
                  --region ${var.aws_region} \
                  --cli-read-timeout 1800 \
                  --cli-connect-timeout 120 || echo "Warning: Service stabilization timed out, but continuing..."
              fi
            else
              echo "Service not active, waiting..."
              timeout 1800 aws ecs wait services-stable \
                --cluster ${aws_ecs_cluster.main.name} \
                --services ${aws_ecs_service.autoscaling.name} \
                --region ${var.aws_region} \
                --cli-read-timeout 1800 \
                --cli-connect-timeout 120 || echo "Warning: Service stabilization timed out, but continuing..."
            fi

      echo "=== DEPLOYMENT COMPLETE ==="
      echo "Application is ready at: http://${aws_lb.autoscaling.dns_name}"
      echo "Health check: http://${aws_lb.autoscaling.dns_name}/health"
    EOT
  }
}

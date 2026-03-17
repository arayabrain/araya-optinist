# ===========================
# ECR Repository (optional)
# ===========================
# Creates an ECR repository when ecr_repository_url is not set.
# Development: ecr_repository_url is omitted → Terraform creates a new repo.
# Production:  ecr_repository_url is set    → uses the pre-existing repo.

resource "aws_ecr_repository" "app" {
  count = var.ecr_repository_url == "" ? 1 : 0

  name                 = "${var.environment}-optinist-for-cloud"
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name    = "${var.environment} OptiNiSt ECR"
    Service = "ecr"
  }
}

# Lifecycle policy:
#   - Remove untagged images after 7 days
#   - Keep only the last 10 versioned images (YYYYMMDD-HHMMSS-<sha> tags)
#   - :latest is always kept (not matched by tagPrefixList)
resource "aws_ecr_lifecycle_policy" "app" {
  count = var.ecr_repository_url == "" ? 1 : 0

  repository = aws_ecr_repository.app[0].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Remove untagged images older than 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep only last 10 versioned images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["20"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# ===========================
# Single source of truth for ECR URL
# ===========================
# When ecr_repository_url is empty -> use the Terraform-created repo URL
# When ecr_repository_url is set   -> use the pre-existing repo URL
locals {
  ecr_repository_url = var.ecr_repository_url == "" ? aws_ecr_repository.app[0].repository_url : var.ecr_repository_url
}

# ===========================
# Outputs
# ===========================
output "ecr_repository_url" {
  description = "ECR repository URL used by this environment"
  value       = local.ecr_repository_url
}

output "ecr_repository_name" {
  description = "ECR repository name (if managed by Terraform)"
  value       = var.ecr_repository_url == "" ? aws_ecr_repository.app[0].name : null
}

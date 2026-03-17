# ===========================
# ECR Repository (optional)
# ===========================
# Creates an ECR repository when manage_ecr_repository = true.
# This is used by the development environment to have its own
# isolated ECR repo, separate from production.
#
# Production uses an existing ECR repo (optinist-for-cloud) that
# was created outside Terraform, so manage_ecr_repository = false.

resource "aws_ecr_repository" "app" {
  count = var.manage_ecr_repository ? 1 : 0

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

# Lifecycle policy: keep only the last 10 untagged images to control storage costs.
# Tagged images (like :latest) are kept indefinitely.
resource "aws_ecr_lifecycle_policy" "app" {
  count = var.manage_ecr_repository ? 1 : 0

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
      }
    ]
  })
}

# ===========================
# Outputs
# ===========================
output "ecr_repository_url" {
  description = "ECR repository URL used by this environment"
  value       = var.ecr_repository_url
}

output "ecr_repository_name" {
  description = "ECR repository name (if managed by Terraform)"
  value       = var.manage_ecr_repository ? aws_ecr_repository.app[0].name : null
}

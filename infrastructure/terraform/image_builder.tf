# ================================================
# EC2 Image Builder — Custom ECS-Optimized AMI
# ================================================
#
# Builds a pre-baked AMI with all system packages (yum, AWS CLI v2, Docker,
# CloudWatch agent, etc.) already installed.  This eliminates ~5 min of
# yum install/update from every instance first-boot, reducing ECS agent
# startup from ~8 min to ~2 min.
#
# Gated by var.use_custom_ami (default false).  When disabled, launch
# templates fall back to the stock Amazon ECS-optimized AMI.

# =====================
# IAM — Build Instance
# =====================
resource "aws_iam_role" "image_builder" {
  count = var.use_custom_ami ? 1 : 0

  name = "${local.env_prefix}-image-builder-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })

  tags = {
    Name    = "${local.env_prefix}-image-builder-role"
    Service = "image-builder"
  }
}

resource "aws_iam_instance_profile" "image_builder" {
  count = var.use_custom_ami ? 1 : 0

  name = "${local.env_prefix}-image-builder-profile"
  role = aws_iam_role.image_builder[0].name
}

resource "aws_iam_role_policy_attachment" "image_builder_ec2" {
  count = var.use_custom_ami ? 1 : 0

  role       = aws_iam_role.image_builder[0].name
  policy_arn = "arn:aws:iam::aws:policy/EC2InstanceProfileForImageBuilder"
}

resource "aws_iam_role_policy_attachment" "image_builder_ssm" {
  count = var.use_custom_ami ? 1 : 0

  role       = aws_iam_role.image_builder[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Allow build instance to access SSM Parameter Store.
# Note: PutParameter is not used at build time (the build instance cannot know
# the output AMI ID).  Retained for future EventBridge → Lambda automation that
# will write the AMI ID after pipeline completion.
resource "aws_iam_role_policy" "image_builder_ssm_param" {
  count = var.use_custom_ami ? 1 : 0

  name = "${local.env_prefix}-image-builder-ssm-param"
  role = aws_iam_role.image_builder[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssm:PutParameter",
        "ssm:GetParameter"
      ]
      Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/${var.environment}/optinist/custom-ami-id"
    }]
  })
}

# Allow build instance to write logs to S3
# (EC2InstanceProfileForImageBuilder only covers ec2imagebuilder-* buckets)
resource "aws_iam_role_policy" "image_builder_s3_logs" {
  count = var.use_custom_ami ? 1 : 0

  name = "${local.env_prefix}-image-builder-s3-logs"
  role = aws_iam_role.image_builder[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject"]
      Resource = "${aws_s3_bucket.app_storage.arn}/image-builder-logs/*"
    }]
  })
}

# ==========================
# IAM — Lifecycle Execution
# ==========================
resource "aws_iam_role" "image_builder_lifecycle" {
  count = var.use_custom_ami ? 1 : 0

  name = "${local.env_prefix}-imagebuilder-lifecycle-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "imagebuilder.amazonaws.com"
      }
    }]
  })

  tags = {
    Name    = "${local.env_prefix}-imagebuilder-lifecycle-role"
    Service = "image-builder"
  }
}

resource "aws_iam_role_policy_attachment" "image_builder_lifecycle" {
  count = var.use_custom_ami ? 1 : 0

  role       = aws_iam_role.image_builder_lifecycle[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/EC2ImageBuilderLifecycleExecutionPolicy"
}

# ==========
# Components
# ==========
resource "aws_imagebuilder_component" "optinist_packages" {
  count = var.use_custom_ami ? 1 : 0

  name        = "${local.env_prefix}-packages-v${replace(var.custom_ami_version, ".", "-")}"
  platform    = "Linux"
  version     = var.custom_ami_version
  description = "Install system packages and AWS CLI v2 for OptiNiSt ECS instances"

  supported_os_versions = ["Amazon Linux 2"]

  data = file("${path.module}/../image_builder/components/optinist-packages.yml")

  tags = {
    Name    = "${local.env_prefix}-packages-component"
    Service = "image-builder"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_imagebuilder_component" "optinist_validate" {
  count = var.use_custom_ami ? 1 : 0

  name        = "${local.env_prefix}-validate-v${replace(var.custom_ami_version, ".", "-")}"
  platform    = "Linux"
  version     = var.custom_ami_version
  description = "Validate OptiNiSt ECS AMI package installations"

  supported_os_versions = ["Amazon Linux 2"]

  data = file("${path.module}/../image_builder/components/optinist-validate.yml")

  tags = {
    Name    = "${local.env_prefix}-validate-component"
    Service = "image-builder"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ============
# Image Recipe
# ============
resource "aws_imagebuilder_image_recipe" "optinist" {
  count = var.use_custom_ami ? 1 : 0

  name         = "${local.env_prefix}-ecs-recipe-v${replace(var.custom_ami_version, ".", "-")}"
  parent_image = data.aws_ami.ecs_optimized.id
  version      = var.custom_ami_version
  description  = "ECS-optimized AMI with pre-installed OptiNiSt packages"

  component {
    component_arn = aws_imagebuilder_component.optinist_packages[0].arn
  }

  component {
    component_arn = aws_imagebuilder_component.optinist_validate[0].arn
  }

  block_device_mapping {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 30
      volume_type           = "gp3"
      delete_on_termination = true
      encrypted             = true
    }
  }

  tags = {
    Name    = "${local.env_prefix}-ecs-recipe"
    Service = "image-builder"
  }

  # Recipes are immutable.  ignore parent_image to prevent forced
  # recreation when Amazon publishes a new ECS-optimized AMI.
  # Bump custom_ami_version to pick up a newer base AMI.
  lifecycle {
    create_before_destroy = true
    ignore_changes        = [parent_image]
  }
}

# ============================
# Infrastructure Configuration
# ============================
resource "aws_imagebuilder_infrastructure_configuration" "optinist" {
  count = var.use_custom_ami ? 1 : 0

  name                  = "${local.env_prefix}-image-builder-infra"
  instance_profile_name = aws_iam_instance_profile.image_builder[0].name
  instance_types        = ["t3.medium"]
  # Public subnet for simplicity; consider private subnet + NAT for production hardening
  subnet_id                     = aws_subnet.public1.id
  security_group_ids            = [aws_security_group.ecs.id]
  terminate_instance_on_failure = true

  logging {
    s3_logs {
      s3_bucket_name = aws_s3_bucket.app_storage.id
      s3_key_prefix  = "image-builder-logs"
    }
  }

  tags = {
    Name    = "${local.env_prefix}-image-builder-infra"
    Service = "image-builder"
  }
}

# ==========================
# Distribution Configuration
# ==========================
resource "aws_imagebuilder_distribution_configuration" "optinist" {
  count = var.use_custom_ami ? 1 : 0

  name        = "${local.env_prefix}-image-distribution"
  description = "Distribute OptiNiSt custom AMI within region"

  distribution {
    region = var.aws_region

    ami_distribution_configuration {
      name        = "${local.env_prefix}-ecs-{{ imagebuilder:buildDate }}"
      description = "OptiNiSt ECS-optimized AMI built {{ imagebuilder:buildDate }}"

      ami_tags = {
        Name        = "${local.env_prefix}-ecs-custom-ami"
        Service     = "image-builder"
        Project     = "optinist-cloud"
        Environment = local.environment_label
        BaseAMI     = data.aws_ami.ecs_optimized.id
      }

      # No launch_permission needed — AMIs are automatically available
      # to the account that creates them.  Explicitly setting user_ids
      # triggers "sharing" logic, which conflicts with AWS-managed CMK
      # encrypted snapshots.
    }
  }

  tags = {
    Name    = "${local.env_prefix}-image-distribution"
    Service = "image-builder"
  }
}

# ==============
# Image Pipeline
# ==============
resource "aws_imagebuilder_image_pipeline" "optinist" {
  count = var.use_custom_ami ? 1 : 0

  name        = "${local.env_prefix}-ami-pipeline"
  description = "Monthly AMI build for OptiNiSt ECS instances"
  status      = "ENABLED"

  image_recipe_arn                 = aws_imagebuilder_image_recipe.optinist[0].arn
  infrastructure_configuration_arn = aws_imagebuilder_infrastructure_configuration.optinist[0].arn
  distribution_configuration_arn   = aws_imagebuilder_distribution_configuration.optinist[0].arn

  # Monthly rebuild: 1st of each month at 03:00 UTC (12:00 JST)
  schedule {
    schedule_expression                = "cron(0 3 1 * ? *)"
    pipeline_execution_start_condition = "EXPRESSION_MATCH_ONLY"
  }

  image_tests_configuration {
    image_tests_enabled = true
    timeout_minutes     = 60
  }

  tags = {
    Name    = "${local.env_prefix}-ami-pipeline"
    Service = "image-builder"
  }

  depends_on = [
    aws_iam_role_policy_attachment.image_builder_ec2,
    aws_iam_role_policy_attachment.image_builder_ssm,
    aws_iam_role_policy.image_builder_s3_logs,
  ]
}

# ====================
# AMI Lifecycle Policy
# ====================
resource "aws_imagebuilder_lifecycle_policy" "optinist_cleanup" {
  count = var.use_custom_ami ? 1 : 0

  name        = "${local.env_prefix}-ami-lifecycle"
  description = "Retain AMIs for 90 days, delete older ones (~3 monthly builds)"
  status      = "ENABLED"

  resource_type = "AMI_IMAGE"

  execution_role = aws_iam_role.image_builder_lifecycle[0].arn

  policy_detail {
    action {
      type = "DELETE"
    }
    filter {
      type  = "AGE"
      value = 90
      unit  = "DAYS"
    }
  }

  resource_selection {
    tag_map = {
      Service = "image-builder"
      Project = "optinist-cloud"
    }
  }

  tags = {
    Name    = "${local.env_prefix}-ami-lifecycle"
    Service = "image-builder"
  }
}

# ==============
# SSM Parameter
# ==============
resource "aws_ssm_parameter" "custom_ami_id" {
  count = var.use_custom_ami ? 1 : 0

  name        = "/${var.environment}/optinist/custom-ami-id"
  type        = "String"
  value       = data.aws_ami.ecs_optimized.id
  description = "Latest custom AMI ID from EC2 Image Builder (initially set to stock AMI)"

  tags = {
    Name    = "Custom AMI ID"
    Service = "image-builder"
  }

  lifecycle {
    ignore_changes = [value]
  }
}

# =======
# Outputs
# =======
output "image_builder_pipeline_arn" {
  description = "ARN of the EC2 Image Builder pipeline"
  value       = var.use_custom_ami ? aws_imagebuilder_image_pipeline.optinist[0].arn : null
}

output "custom_ami_ssm_parameter" {
  description = "SSM parameter name for custom AMI ID"
  value       = var.use_custom_ami ? "/${var.environment}/optinist/custom-ami-id" : null
}

output "effective_ami_id" {
  description = "The AMI ID currently used by launch templates"
  value       = local.effective_ami_id
  sensitive   = true
}

output "ami_source" {
  description = "Whether using custom or stock AMI"
  value       = var.use_custom_ami ? "custom (Image Builder)" : "stock (Amazon ECS-optimized)"
}

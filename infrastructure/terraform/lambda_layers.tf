# ===============================================
# Lambda Layers
# ===============================================
# Shared code and dependencies packaged as Lambda Layers
# for reuse across multiple Lambda functions.

# ===========================
# AWS Constants Layer
# ===========================
# This layer contains shared AWS constants (ECSTaskStatus, BatchJobStatus,
# PremiumInstanceConfig, RoutingHeaders, DatabaseConfig) used by multiple
# Lambda functions. Using a layer avoids code duplication and ensures
# all Lambdas use the same constant values.

# Create the layer directory structure
# Lambda layers for Python must have files under python/ directory
resource "null_resource" "prepare_aws_constants_layer" {
  provisioner "local-exec" {
    command = <<-EOT
      mkdir -p ${path.module}/aws_constants_layer/python
      cp ${path.module}/../aws_constants.py ${path.module}/aws_constants_layer/python/aws_constants.py
    EOT
  }

  triggers = {
    # Rebuild layer when aws_constants.py changes
    code_hash = filesha256("${path.module}/../aws_constants.py")
  }
}

# Create ZIP for the layer
data "archive_file" "aws_constants_layer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/aws_constants_layer"
  output_path = "${path.module}/aws_constants_layer.zip"

  depends_on = [null_resource.prepare_aws_constants_layer]
}

# Create the Lambda Layer
resource "aws_lambda_layer_version" "aws_constants" {
  filename            = "${path.module}/aws_constants_layer.zip"
  layer_name          = "${var.environment}-aws-constants"
  description         = "Shared AWS constants for subscription Lambda functions"
  compatible_runtimes = ["python3.9", "python3.10", "python3.11", "python3.12"]

  source_code_hash = data.archive_file.aws_constants_layer_zip.output_base64sha256

  depends_on = [data.archive_file.aws_constants_layer_zip]
}

# ===========================
# Outputs
# ===========================

output "aws_constants_layer_arn" {
  description = "ARN of the aws_constants Lambda layer"
  value       = aws_lambda_layer_version.aws_constants.arn
}

output "aws_constants_layer_version" {
  description = "Version of the aws_constants Lambda layer"
  value       = aws_lambda_layer_version.aws_constants.version
}

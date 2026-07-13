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

# Create ZIP for the layer
# Uses inline source block so the archive is built at plan time without
# needing a pre-existing directory (avoids null_resource chicken-and-egg).
data "archive_file" "aws_constants_layer_zip" {
  type        = "zip"
  output_path = "${path.module}/aws_constants_layer.zip"

  source {
    content  = file("${path.module}/../aws_constants.py")
    filename = "python/aws_constants.py"
  }
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

# Provider configuration
provider "aws" {
  region = var.aws_region
}

terraform {
  backend "s3" {
    bucket = "subscr-optinist-for-cloud-tfstate"
    key    = "terraform.tfstate"
    region = "ap-northeast-1"
    # dynamodb_table = "terraform-state-lock"  # Uncomment after initial bootstrap
    # encrypt        = true                     # Uncomment after initial bootstrap
  }
}


# Variables
variable "aws_region" {
  description = "AWS region to deploy resources"
  default     = ""
}
variable "availability_zone" {
  description = "Availability zone for the subnet"
  default     = ""
}

# Database configuration
variable "mysql_root_password" {
  description = "MySQL/MariaDB root password"
  type        = string
  default     = ""
}

variable "mysql_database" {
  description = "MySQL/MariaDB database name"
  type        = string
  default     = ""
}

variable "mysql_user" {
  description = "MySQL/MariaDB user"
  type        = string
  default     = ""
}

variable "mysql_password" {
  description = "MySQL/MariaDB password"
  type        = string
  default     = ""
}

variable "optinist_org_name" {
  description = "Name for initial organization"
  type        = string
}

variable "optinist_admin_name" {
  description = "Name for initial admin user"
  type        = string
}

variable "optinist_admin_email" {
  description = "Email for initial admin user"
  type        = string
}

variable "optinist_admin_uid" {
  description = "Firebase UID for initial admin user"
  type        = string
}

variable "optinist_secret_key" {
  description = "Secret key for OptiNiSt"
  type        = string
  sensitive   = true
}

variable "stripe_webhook_secret" {
  description = "Stripe webhook secret for validating webhook events"
  type        = string
  sensitive   = true
}

variable "firebase_config_json" {
  description = "Firebase web configuration JSON"
  type        = string
  sensitive   = true
}

variable "firebase_private_json" {
  description = "Firebase service account private key JSON"
  type        = string
  sensitive   = true
}

variable "test_users" {
  description = "Test user configuration with Firebase UIDs and subscription details"
  type = list(object({
    email                = string
    name                 = string
    firebase_uid         = string
    subscription_plan_id = number
    role_id              = number
    storage_quota_gb     = number
  }))
  default   = []
  sensitive = true
}

variable "subscription_plans" {
  description = "Subscription plan configurations with Stripe integration details"
  type = list(object({
    id                = number
    name              = string
    price             = number
    billing_cycle     = number
    currency          = number
    status            = number
    stripe_product_id = string
    stripe_price_id   = string
    storage_quota_gb  = number
    features          = map(list(object({
      text      = string
      isPremium = bool
    })))
  }))
  default   = []
  sensitive = true
}

variable "git_repo" {
  description = "Git repository"
  type        = string
}

variable "git_branch" {
  description = "Git branch to checkout"
  type        = string
}

variable "ecr_repository_url" {
  description = "ECR repository URL for OptiNiSt Docker image"
  type        = string
}

variable "asg_min_size" {
  description = "Minimum number of instances in ASG"
  type        = number
  default     = 1
}

variable "asg_max_size" {
  description = "Maximum number of instances in ASG"
  type        = number
  default     = 3
}

variable "asg_desired_capacity" {
  description = "Desired number of instances in ASG"
  type        = number
  default     = 1
}

# Frontend domain configuration
variable "frontend_domain" {
  description = "Custom domain name for the frontend application"
  type        = string
  default     = "araya-optinist.com"
}

variable "frontend_protocol" {
  description = "Protocol for frontend access (http or https)"
  type        = string
  default     = "https"
}

variable "frontend_port" {
  description = "Port for frontend access (80 for http, 443 for https)"
  type        = string
  default     = "443"
}

# Data sources
data "aws_caller_identity" "current" {}



# =======
# Outputs
# =======
output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "CIDR block of the VPC"
  value       = aws_vpc.main.cidr_block
}

output "public_subnet_ids" {
  description = "IDs of public subnets"
  value = {
    public1 = aws_subnet.public1.id
    public2 = aws_subnet.public2.id
  }
}

output "private_subnet_ids" {
  description = "IDs of private subnets"
  value = {
    private1 = aws_subnet.private1.id
    private2 = aws_subnet.private2.id
  }
}

output "public_subnet_cidrs" {
  description = "CIDR blocks of public subnets"
  value = {
    public1 = aws_subnet.public1.cidr_block
    public2 = aws_subnet.public2.cidr_block
  }
}

output "private_subnet_cidrs" {
  description = "CIDR blocks of private subnets"
  value = {
    private1 = aws_subnet.private1.cidr_block
    private2 = aws_subnet.private2.cidr_block
  }
}

output "rds_endpoint" {
  description = "RDS instance endpoint (autoscaling)"
  value       = aws_db_instance.main.endpoint
}

output "rds_security_group_id" {
  value = aws_security_group.rds.id
}

output "ecs_security_group_id" {
  value = aws_security_group.ecs.id
}

output "alb_dns_name" {
  description = "ALB DNS name"
  value       = aws_lb.autoscaling.dns_name
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name_autoscaling" {
  description = "Name of the ECS service"
  value       = aws_ecs_service.autoscaling.name
}

output "efs_id" {
  description = "ID of the EFS file system"
  value       = aws_efs_file_system.snmk.id
}

output "app_storage_bucket" {
  description = "S3 bucket for application storage (autoscaling)"
  value       = aws_s3_bucket.app_storage.id
}

output "asg_name" {
  description = "Name of the Auto Scaling Group"
  value       = aws_autoscaling_group.main.name
}

output "launch_template_id" {
  description = "ID of the Launch Template"
  value       = aws_launch_template.ecs.id
}

# Configuration Outputs
output "frontend_config_autoscaling" {
  description = "Configuration values for frontend/.env.production"
  value = {
    REACT_APP_SERVER_HOST             = aws_lb.autoscaling.dns_name
    REACT_APP_SERVER_PORT             = "80"
    REACT_APP_SERVER_PROTO            = "http"
    REACT_APP_EXPDB_METADATA_EDITABLE = true
  }
}


output "backend_config" {
  description = "Configuration values for studio/auth/config/.env"
  value = {
    S3_DEFAULT_BUCKET_NAME = aws_s3_bucket.app_storage.id
  }
}

# Output the key information
output "ssh_key_name" {
  description = "Name of the generated SSH key pair"
  value       = aws_key_pair.subscr_optinist_cloud_key_pair.key_name
}

output "ssh_private_key_path" {
  description = "Path to the private key file"
  value       = local_file.private_key.filename
}

output "ssh_command" {
  description = "SSH command to connect to instances"
  value       = "ssh -i ${local_file.private_key.filename} ec2-user@<INSTANCE_IP>"
}

# Output the access key credentials
output "optinist_cloud_user_access_key_id" {
  description = "Access Key ID for subscr-optinist-cloud-user"
  value       = aws_iam_access_key.subscr_optinist_cloud_user_access_key.id
}

output "optinist_cloud_user_secret_access_key" {
  description = "Secret Access Key for subscr-optinist-cloud-user"
  value       = aws_iam_access_key.subscr_optinist_cloud_user_access_key.secret
  sensitive   = true
}

output "alb_arn" {
  description = "ARN of the main ALB for premium instance routing"
  value       = aws_lb.autoscaling.arn
}

output "alb_listener_arn" {
  description = "ARN of the main ALB HTTPS listener for premium routing rules"
  value       = aws_lb_listener.autoscaling_https.arn
}

output "premium_instance_ids" {
  description = "IDs of the premium standby instances"
  value       = aws_instance.premium[*].id
}

output "test_users" {
  description = "Test user configuration for load testing (includes Firebase UIDs)"
  value       = var.test_users
  sensitive   = true
}

# Route53 and SSL outputs
output "domain_name" {
  description = "Custom domain name for the application"
  value       = var.frontend_domain
}

output "domain_url" {
  description = "Full URL for the application"
  value       = "${var.frontend_protocol}://${var.frontend_domain}"
}

output "domain_protocol" {
  description = "Protocol for the application (http or https)"
  value       = var.frontend_protocol
}

output "domain_port" {
  description = "Port for the application"
  value       = var.frontend_port
}

output "acm_certificate_arn" {
  description = "ARN of the ACM certificate for HTTPS"
  value       = aws_acm_certificate.main.arn
}

output "acm_certificate_status" {
  description = "Validation status of the ACM certificate"
  value       = aws_acm_certificate.main.status
}

output "route53_zone_id" {
  description = "Route53 hosted zone ID for araya-optinist.com"
  value       = data.aws_route53_zone.main.zone_id
}

output "alb_listener_https_arn" {
  description = "ARN of the HTTPS ALB listener"
  value       = aws_lb_listener.autoscaling_https.arn
}

output "rds_proxy_endpoint" {
  value = aws_db_proxy.main.endpoint
}

# Provider configuration
provider "aws" {
  region = var.aws_region
}

terraform {
  backend "s3" {
    bucket         = "subscr-optinist-for-cloud-tfstate"
    key            = "terraform.tfstate"
    region         = "ap-northeast-1"
    # dynamodb_table = "terraform-state-lock"  # Uncomment after initial bootstrap
    # encrypt        = true                     # Uncomment after initial bootstrap
  }
}

# DynamoDB Table for Terraform State Locking
# This table must be created first before enabling locking in the backend config above
resource "aws_dynamodb_table" "terraform_state_lock" {
  name           = "terraform-state-lock"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name        = "Terraform State Lock Table"
    ManagedBy   = "Terraform"
    Description = "Prevents concurrent terraform operations"
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
    email               = string
    name                = string
    firebase_uid        = string
    subscription_plan_id = number
    role_id             = number
    storage_quota_gb    = number
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

variable "ecr_batch_repository_url" {
  description = "ECR repository URL for OptiNiSt Batch Docker image"
  type        = string
}

variable "ecr_snakemake_batch_repository_url" {
  description = "ECR repository URL for OptiNiSt Snakemake Batch Docker image"
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

# Data sources
data "aws_caller_identity" "current" {}

# ==============
# Key generation
# ==============
variable "key_name" {
  description = "Name of the SSH key pair"
  type        = string
}

# Generate random suffix for key pair name
resource "random_id" "key_suffix" {
  byte_length = 4
}

# Generate a unique key pair name
locals {
  key_name = var.key_name != "" ? var.key_name : "subscr-optinist-cloud-${random_id.key_suffix.hex}"
}

# Generate SSH key pair
resource "tls_private_key" "subscr_optinist_cloud_key" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

# Create AWS key pair
resource "aws_key_pair" "subscr_optinist_cloud_key_pair" {
  key_name   = local.key_name
  public_key = tls_private_key.subscr_optinist_cloud_key.public_key_openssh  # Fixed reference

  tags = {
    Name = "subscr-optinist-cloud-key"
  }
}

# Save private key to local file
resource "local_file" "private_key" {
  content         = tls_private_key.subscr_optinist_cloud_key.private_key_pem  # Fixed reference
  filename        = "${path.module}/subscr-optinist-cloud-private-key.pem"
  file_permission = "0400"
}


# =============
# VPC & Network
# =============
resource "aws_vpc" "main" {
  cidr_block           = "10.1.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "subscr-optinist-cloud-vpc"
  }
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "subscr-optinist-cloud-igw"
  }
}

# Public Subnets
resource "aws_subnet" "public1" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.1.0.0/20"
  availability_zone = "ap-northeast-1a"
  map_public_ip_on_launch = true

  tags = {
    Name = "subscr-optinist-cloud-subnet-public1-ap-northeast-1a"
  }
}

resource "aws_subnet" "public2" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.1.16.0/20"
  availability_zone = "ap-northeast-1c"
  map_public_ip_on_launch = true

  tags = {
    Name = "subscr-optinist-cloud-subnet-public2-ap-northeast-1c"
  }
}

# Private Subnets
resource "aws_subnet" "private1" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.1.128.0/20"
  availability_zone = "ap-northeast-1a"

  tags = {
    Name = "subscr-optinist-cloud-subnet-private1-ap-northeast-1a"
  }
}

resource "aws_subnet" "private2" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.1.144.0/20"
  availability_zone = "ap-northeast-1c"

  tags = {
    Name = "subscr-optinist-cloud-subnet-private2-ap-northeast-1c"
  }
}

# ============
# NAT Instance
# ============
resource "aws_instance" "nat" {
  ami                    = data.aws_ami.nat_instance.id
  instance_type          = "t3a.nano"
  subnet_id              = aws_subnet.public1.id
  vpc_security_group_ids = [aws_security_group.nat_instance.id]
  source_dest_check      = false

  iam_instance_profile   = aws_iam_instance_profile.nat_instance.name

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  user_data = <<-EOF
              #!/bin/bash
              yum update -y

              # Enable IP forwarding
              echo 'net.ipv4.ip_forward = 1' >> /etc/sysctl.conf
              sysctl -p

              # Configure NAT with iptables
              iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
              iptables -A FORWARD -i eth0 -o eth1 -m state --state RELATED,ESTABLISHED -j ACCEPT
              iptables -A FORWARD -i eth1 -o eth0 -j ACCEPT

              # Save iptables rules
              service iptables save

              # Ensure iptables starts on boot
              chkconfig iptables on
              EOF

  tags = {
    Name = "subscr-optinist-nat-instance"
  }
}

# Elastic IP for NAT Instance
resource "aws_eip" "nat_instance" {
  domain = "vpc"
  instance = aws_instance.nat.id

  tags = {
    Name = "subscr-optinist-nat-instance-eip"
  }
}

# NAT Instance AMI
data "aws_ami" "nat_instance" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

data "aws_network_interface" "nat" {
  depends_on = [aws_instance.nat]

  filter {
    name   = "attachment.instance-id"
    values = [aws_instance.nat.id]
  }

  filter {
    name   = "attachment.device-index"
    values = ["0"]
  }
}

# ============
# Route Tables
# ============
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "subscr-optinist-cloud-rtb-public"
  }
}

resource "aws_route_table" "private1" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    network_interface_id = data.aws_network_interface.nat.id
  }

  tags = {
    Name = "subscr-optinist-cloud-rtb-private1-ap-northeast-1a"
  }
}

resource "aws_route_table" "private2" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    network_interface_id = data.aws_network_interface.nat.id
  }

  tags = {
    Name = "subscr-optinist-cloud-rtb-private2-ap-northeast-1c"
  }
}

# Route Table Associations
resource "aws_route_table_association" "public1" {
  subnet_id      = aws_subnet.public1.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "public2" {
  subnet_id      = aws_subnet.public2.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private1" {
  subnet_id      = aws_subnet.private1.id
  route_table_id = aws_route_table.private1.id
}

resource "aws_route_table_association" "private2" {
  subnet_id      = aws_subnet.private2.id
  route_table_id = aws_route_table.private2.id
}

resource "aws_vpc_endpoint_route_table_association" "private1_s3" {
  route_table_id  = aws_route_table.private1.id
  vpc_endpoint_id = aws_vpc_endpoint.s3.id
}

resource "aws_vpc_endpoint_route_table_association" "private2_s3" {
  route_table_id  = aws_route_table.private2.id
  vpc_endpoint_id = aws_vpc_endpoint.s3.id
}

# S3 VPC Endpoint
resource "aws_vpc_endpoint" "s3" {
  vpc_id       = aws_vpc.main.id
  service_name = "com.amazonaws.ap-northeast-1.s3"

  tags = {
    Name = "subscr-optinist-cloud-vpce-s3"
  }
}

# Security Group for VPC Endpoints
resource "aws_security_group" "vpc_endpoints" {
  name        = "subscr-optinist-vpc-endpoints-sg"
  description = "Security group for VPC endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  tags = {
    Name = "subscr-optinist-vpc-endpoints-sg"
  }
}

# ECR API endpoint
resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.ap-northeast-1.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private1.id, aws_subnet.private2.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]

  tags = {
    Name = "subscr-optinist-ecr-api-endpoint"
  }
}

# ECR Docker endpoint
resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.ap-northeast-1.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private1.id, aws_subnet.private2.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]

  tags = {
    Name = "subscr-optinist-ecr-dkr-endpoint"
  }
}

# CloudWatch Logs endpoint
resource "aws_vpc_endpoint" "logs" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.ap-northeast-1.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private1.id, aws_subnet.private2.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]

  tags = {
    Name = "subscr-optinist-logs-endpoint"
  }
}

# ===
# RDS
# ===
resource "aws_db_subnet_group" "main" {
  name       = "subscr-optinist-rds-subnet-group"
  subnet_ids = [
    aws_subnet.private1.id,
    aws_subnet.private2.id
  ]

  tags = {
    Name = "subscr-optinist-rds-subnet-group"
  }
}

# RDS Parameter Group (Custom)
resource "aws_db_parameter_group" "main" {
  family = "mysql8.0"
  name   = "subscr-optinist-no-ssl"

  parameter {
    name  = "require_secure_transport"
    value = "0"
  }

  tags = {
    Name = "subscr-optinist-no-ssl"
  }
}

# RDS Instance

resource "aws_db_instance" "main" {
  identifier              = "subscr-optinist-cloud-rds"
  allocated_storage       = 20
  storage_type            = "gp3"
  engine                  = "mysql"
  engine_version          = "8.0"
  instance_class          = "db.t4g.micro"
  parameter_group_name    = "default.mysql8.0"
  db_name                 = var.mysql_database
  username                = var.mysql_user
  password                = var.mysql_password
  skip_final_snapshot     = true
  final_snapshot_identifier = "${var.mysql_database}-final-snapshot"
  backup_retention_period = 7
  monitoring_interval     = 60
  monitoring_role_arn     = aws_iam_role.rds_monitoring.arn
  publicly_accessible     = false
  enabled_cloudwatch_logs_exports = ["error", "general", "slowquery"]
  network_type            = "IPV4"
  port                    = 3306
  vpc_security_group_ids  = [aws_security_group.rds.id]
  db_subnet_group_name    = aws_db_subnet_group.main.name
  multi_az                = false
  storage_encrypted       = true

  tags = {
    Name = "subscr-optinist-cloud-rds"
  }
}

# Create premium user assignments table in RDS
# Premium user assignments table now managed via Alembic migration
# To apply the migration, ssh into an instance in the Auto Scaling Group and run:
# cd studio && alembic upgrade head

# RDS Instance for Batch Service (Isolated)
resource "aws_db_instance" "batch" {
  identifier              = "subscr-optinist-cloud-rds-batch"
  allocated_storage       = 20
  storage_type            = "gp3"
  engine                  = "mysql"
  engine_version          = "8.0"
  instance_class          = "db.t4g.micro"
  parameter_group_name    = "default.mysql8.0"
  db_name                 = var.mysql_database
  username                = var.mysql_user
  password                = var.mysql_password
  skip_final_snapshot     = true
  final_snapshot_identifier = "${var.mysql_database}-batch-final-snapshot"
  backup_retention_period = 7
  monitoring_interval     = 60
  monitoring_role_arn     = aws_iam_role.rds_monitoring.arn
  publicly_accessible     = false
  enabled_cloudwatch_logs_exports = ["error", "general", "slowquery"]
  network_type            = "IPV4"
  port                    = 3306
  vpc_security_group_ids  = [aws_security_group.rds.id]
  db_subnet_group_name    = aws_db_subnet_group.main.name
  multi_az                = false
  storage_encrypted       = true

  tags = {
    Name = "subscr-optinist-cloud-rds-batch"
    Service = "batch"
  }
}


# ===============
# EFS File System
# ===============
resource "aws_efs_file_system" "snmk" {
  creation_token = "subscr-optinist-cloud-snmk-volume"

  performance_mode = "generalPurpose"
  throughput_mode = "bursting"

  tags = {
    Name = "subscr-optinist-cloud-snmk-volume"
  }
}

# EFS Mount Targets
resource "aws_efs_mount_target" "private1" {
  file_system_id  = aws_efs_file_system.snmk.id
  subnet_id       = aws_subnet.private1.id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_mount_target" "private2" {
  file_system_id  = aws_efs_file_system.snmk.id
  subnet_id       = aws_subnet.private2.id
  security_groups = [aws_security_group.efs.id]
}

# EFS Access Point
resource "aws_efs_access_point" "snmk" {
  file_system_id = aws_efs_file_system.snmk.id

  root_directory {
    path = "/"
    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "755"
    }
  }

  tags = {
    Name = "subscr-optinist-cloud-efs-ap"
  }
}

# ========================
# EFS File System for Batch (Isolated)
# ========================
resource "aws_efs_file_system" "batch" {
  creation_token = "subscr-optinist-cloud-batch-snmk-volume"

  performance_mode = "generalPurpose"
  throughput_mode = "bursting"

  tags = {
    Name = "subscr-optinist-cloud-batch-efs"
    Service = "batch"
  }
}

# EFS Mount Targets for Batch
resource "aws_efs_mount_target" "batch_private1" {
  file_system_id  = aws_efs_file_system.batch.id
  subnet_id       = aws_subnet.private1.id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_mount_target" "batch_private2" {
  file_system_id  = aws_efs_file_system.batch.id
  subnet_id       = aws_subnet.private2.id
  security_groups = [aws_security_group.efs.id]
}

# EFS Access Point for Batch
resource "aws_efs_access_point" "batch" {
  file_system_id = aws_efs_file_system.batch.id

  root_directory {
    path = "/"
    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "755"
    }
  }

  tags = {
    Name = "subscr-optinist-cloud-batch-efs-ap"
    Service = "batch"
  }
}

# =================================
# S3 bucket for application storage
# =================================

resource "aws_s3_bucket" "app_storage" {
  bucket = "subscr-optinist-app-storage"
  force_destroy = true

  tags = {
    Name        = "Subscr OptiNiSt Application Storage"
    Environment = "Production"
  }
}

resource "aws_s3_bucket" "app_storage_batch" {
  bucket = "subscr-optinist-batch-app-storage"
  force_destroy = true

  tags = {
    Name        = "Subscr OptiNiSt Batch Application Storage"
    Environment = "Production"
  }
}

resource "aws_s3_bucket_versioning" "app_storage" {
  bucket = aws_s3_bucket.app_storage.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_versioning" "app_storage_batch" {
  bucket = aws_s3_bucket.app_storage_batch.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Block all public access to S3
resource "aws_s3_bucket_public_access_block" "app_storage" {
  bucket = aws_s3_bucket.app_storage.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "app_storage_batch" {
  bucket = aws_s3_bucket.app_storage_batch.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# =========================
# Application Load Balancer
# =========================
resource "aws_lb" "autoscaling" {
  name               = "subscr-optinist-lb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id, aws_security_group.ecs.id]
  subnets           = [aws_subnet.public1.id, aws_subnet.public2.id]

  enable_deletion_protection = false
  idle_timeout              = 180  # Increased to accommodate premium instance cold starts

  # Enable access logs for detailed monitoring
  access_logs {
    bucket  = aws_s3_bucket.app_storage.id
    prefix  = "alb-logs"
    enabled = true
  }

  depends_on = [
    aws_s3_bucket_policy.app_storage
  ]

  tags = {
    Name = "subscr-optinist-load-balancer"
  }
}

resource "aws_lb" "batch" {
  name               = "subscr-batch-optinist-lb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id, aws_security_group.ecs.id]
  subnets           = [aws_subnet.public1.id, aws_subnet.public2.id]

  enable_deletion_protection = false
  idle_timeout              = 60

  # Enable access logs for detailed monitoring
  access_logs {
    bucket  = aws_s3_bucket.app_storage.id
    prefix  = "alb-logs"
    enabled = true
  }

  depends_on = [
    aws_s3_bucket_policy.app_storage
  ]

  tags = {
    Name = "subscr-batch-optinist-load-balancer"
  }
}

# Load Balancer Listener
resource "aws_lb_listener" "autoscaling" {
  load_balancer_arn = aws_lb.autoscaling.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autoscaling.arn
  }
}

resource "aws_lb_listener" "batch" {
  load_balancer_arn = aws_lb.batch.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.batch.arn
  }
}

# ==================
# Auto Scaling Group
# ==================
resource "aws_autoscaling_group" "main" {
  name                = "subscr-optinist-asg"
  vpc_zone_identifier = [aws_subnet.private1.id, aws_subnet.private2.id]
  target_group_arns   = [aws_lb_target_group.autoscaling.arn]
  health_check_type   = "ELB"
  health_check_grace_period = 900
  default_cooldown = 300

  min_size         = var.asg_min_size
  max_size         = var.asg_max_size
  desired_capacity = var.asg_desired_capacity

  launch_template {
    id      = aws_launch_template.ecs.id
    version = "$Latest"
  }

  force_delete = true
  termination_policies = ["OldestInstance"]
  wait_for_capacity_timeout = "0"

  # Enable instance scale-in protection
  protect_from_scale_in = false

  # Enable detailed monitoring
  enabled_metrics = [
    "GroupMinSize",
    "GroupMaxSize",
    "GroupDesiredCapacity",
    "GroupInServiceInstances",
    "GroupTotalInstances",
    "GroupPendingInstances",
    "GroupStandbyInstances",
    "GroupTerminatingInstances"
  ]

  tag {
    key                 = "Name"
    value               = "subscr-optinist-asg-instance"
    propagate_at_launch = true
  }

  tag {
    key                 = "Service"
    value               = "autoscaling"
    propagate_at_launch = true
  }

  tag {
    key                 = "Type"
    value               = "ASG-ECS"
    propagate_at_launch = true
  }

  tag {
    key                 = "LaunchTemplateVersion"
    value               = aws_launch_template.ecs.latest_version
    propagate_at_launch = true
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      instance_warmup = 300
      min_healthy_percentage = 0
    }
  }

  lifecycle {
    ignore_changes = [desired_capacity]
  }

  timeouts {
    delete = "30m"
  }

  # Lifecycle hooks for logging
  initial_lifecycle_hook {
    name                 = "subscr-optinist-launch-hook"
    default_result       = "CONTINUE"
    heartbeat_timeout    = 300
    lifecycle_transition = "autoscaling:EC2_INSTANCE_LAUNCHING"
  }

  initial_lifecycle_hook {
    name                 = "subscr-optinist-terminate-hook"
    default_result       = "CONTINUE"
    heartbeat_timeout    = 300
    lifecycle_transition = "autoscaling:EC2_INSTANCE_TERMINATING"
  }
}

# Auto Scaling Policies
resource "aws_autoscaling_policy" "scale_up" {
  name                   = "subscr-optinist-scale-up"
  scaling_adjustment     = 1
  adjustment_type        = "ChangeInCapacity"
  cooldown              = 300
  autoscaling_group_name = aws_autoscaling_group.main.name
}

resource "aws_autoscaling_policy" "scale_down" {
  name                   = "subscr-optinist-scale-down"
  scaling_adjustment     = -1
  adjustment_type        = "ChangeInCapacity"
  cooldown              = 300
  autoscaling_group_name = aws_autoscaling_group.main.name
}

# Target Group for ALB
resource "aws_lb_target_group" "autoscaling" {
  name        = "subscr-optinist-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 5
    interval            = 60
    matcher            = "200"
    path               = "/health"
    port               = "traffic-port"
    protocol           = "HTTP"
    timeout            = 30
  }

  stickiness {
    type            = "lb_cookie"
    cookie_duration = 86400
    enabled         = true
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "subscr-optinist-cloud-target-group"
  }
}

resource "aws_lb_target_group" "batch" {
  name        = "subscr-batch-optinist-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 5
    interval            = 60
    matcher            = "200"
    path               = "/health"
    port               = "traffic-port"
    protocol           = "HTTP"
    timeout            = 30
  }

  stickiness {
    type            = "lb_cookie"
    cookie_duration = 86400
    enabled         = true
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "subscr-batch-optinist-cloud-target-group"
  }
}

# ===========
# ECS Cluster
# ===========
resource "aws_ecs_cluster" "main" {
  name = "subscr-optinist-cloud-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  service_connect_defaults {
    namespace = aws_service_discovery_private_dns_namespace.main.arn
  }

  tags = {
    Name = "subscr-optinist-cloud-cluster"
  }
}

# Service Discovery
resource "aws_service_discovery_private_dns_namespace" "main" {
  name        = "subscr.optinist.local"
  vpc         = aws_vpc.main.id
}

# ECS Capacity Provider
resource "aws_ecs_capacity_provider" "main" {
  name = "subscr-optinist-capacity-provider"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.main.arn
    managed_termination_protection = "DISABLED"

    managed_scaling {
      maximum_scaling_step_size = 1
      minimum_scaling_step_size = 1
      status                    = "DISABLED"
      target_capacity           = 90
      instance_warmup_period    = 300
    }
  }

  depends_on = [
    aws_autoscaling_group.main,
    aws_launch_template.ecs
  ]

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "subscr-optinist-capacity-provider"
  }
}

# ECS Cluster Capacity Providers
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = [aws_ecs_capacity_provider.main.name]

  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.main.name
    weight           = 1
    base            = 0
  }

  depends_on = [
    aws_ecs_capacity_provider.main,
    aws_autoscaling_group.main,
    aws_ecs_cluster.main,
    aws_launch_template.ecs
  ]

  lifecycle {
    create_before_destroy = false
    prevent_destroy = false
    ignore_changes = [capacity_providers]
  }
}

# ===============
# Security groups
# ===============
resource "aws_security_group" "ecs" {
  name        = "subscr-ecs-optinist-cloud-security-group"
  description = "Created by Terraform for ECS"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "subscr-optinist-cloud-sg-ecs"
  }
}

resource "aws_security_group_rule" "ecs_ingress_all" {
  type              = "ingress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  ipv6_cidr_blocks  = ["::/0"]
  security_group_id = aws_security_group.ecs.id
}

resource "aws_security_group_rule" "ecs_ingress_http" {
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  ipv6_cidr_blocks  = ["::/0"]
  security_group_id = aws_security_group.ecs.id
}

resource "aws_security_group_rule" "ecs_ingress_app" {
  type              = "ingress"
  from_port         = 8000
  to_port           = 8009
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  ipv6_cidr_blocks  = ["::/0"]
  security_group_id = aws_security_group.ecs.id
}

resource "aws_security_group_rule" "ecs_ingress_dynamic_ports" {
  type                     = "ingress"
  from_port                = 32768
  to_port                  = 65535
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb.id
  security_group_id        = aws_security_group.ecs.id
}

resource "aws_security_group_rule" "ecs_ingress_nfs" {
  type              = "ingress"
  from_port         = 2049
  to_port           = 2049
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.ecs.id
}

resource "aws_security_group_rule" "ecs_ingress_mysql" {
  type              = "ingress"
  from_port         = 3306
  to_port           = 3306
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.ecs.id
}

resource "aws_security_group_rule" "ecs_egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  ipv6_cidr_blocks  = ["::/0"]
  security_group_id = aws_security_group.ecs.id
}

resource "aws_security_group" "alb" {
  name        = "subscr-optinist-alb-security-group"
  description = "Security group for ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  ingress {
    from_port        = 443
    to_port          = 443
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  ingress {
    from_port        = 8000
    to_port          = 8000
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "subscr-optinist-alb-sg"
  }

  lifecycle {
    ignore_changes = [egress]
  }
}

resource "aws_security_group_rule" "ecs_from_alb" {
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb.id
  security_group_id        = aws_security_group.ecs.id
  description              = "ALB health checks"
}

resource "aws_security_group_rule" "alb_to_ecs_dynamic" {
  type                     = "egress"
  from_port                = 32768
  to_port                  = 65535
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ecs.id
  security_group_id        = aws_security_group.alb.id
  description              = "ALB to ECS dynamic ports"
}

resource "aws_security_group_rule" "alb_to_ecs" {
  type                     = "egress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ecs.id
  security_group_id        = aws_security_group.alb.id
  description              = "Health check to ECS targets"
}

resource "aws_security_group" "rds" {
  name        = "subscr-optinist-rds-security-group"
  description = "Security group for RDS"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "subscr-optinist-cloud-sg-rds"
  }

  lifecycle {
    ignore_changes = [ingress]
  }
}

resource "aws_security_group" "efs" {
  name        = "subscr-optinist-cloud-efs-sg"
  description = "Security group for EFS mount targets"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  tags = {
    Name = "subscr-optinist-cloud-efs-sg"
  }
}

resource "aws_security_group" "nat_instance" {
  name        = "subscr-nat-instance-sg"
  description = "Security group for NAT Instance"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "subscr-optinist-nat-instance-sg"
  }
}

resource "aws_security_group" "batch" {
  name        = "subscr-optinist-batch-sg"
  description = "Security group for AWS Batch compute environments"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port = 2049
    to_port   = 2049
    protocol  = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  # Same rules as ECS for RDS access
  egress {
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    security_groups = [aws_security_group.rds.id]
  }

  # Internet access for ECR/S3
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "subscr-optinist-batch-sg"
  }
}

resource "aws_security_group_rule" "rds_from_batch" {
  type                     = "ingress"
  from_port                = 3306
  to_port                  = 3306
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.batch.id
  security_group_id        = aws_security_group.rds.id
  description              = "MySQL access from Batch instances"
}

# ====================
# IAM ROLES & POLICIES
# ====================

# ECS Task Execution Role (for ECS agent to pull images, etc.)
# --------------------------------------------------------------
resource "aws_iam_role" "ecs_task_execution" {
  name = "subscr-optinist-cloud-task-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_secrets_policy" {
  name = "ecs-secrets-policy"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.aws_credentials.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "batch_service_additional" {
  name = "subscr-batch-service-additional"
  role = aws_iam_role.batch_service.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceTypes",
          "ec2:DescribeImages",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSubnets",
          "ec2:DescribeVpcs",
          "ec2:DeleteLaunchTemplate",
          "ec2:DescribeLaunchTemplates",
          "ec2:DescribeLaunchTemplateVersions",
          "ecs:DeleteCluster",
          "ecs:ListClusters",
          "ecs:DescribeClusters"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow",
        Action = [
            "s3:GetObject",
            "s3:PutObject",
            "s3:DeleteObject",
            "s3:ListBucket"
        ],
        Resource = [
            "arn:aws:s3:::subscr-optinist-batch-app-storage",
            "arn:aws:s3:::subscr-optinist-batch-app-storage/*"
        ]
      }
    ]
  })
}


resource "time_sleep" "batch_role_propagation" {
  depends_on = [
    aws_iam_role_policy_attachment.batch_service_role,
    aws_iam_role_policy_attachment.batch_service_ecs
  ]
  create_duration = "20s"
}


# ECS Task Role (for containers to call AWS services)
# ------------------------------------------------------
resource "aws_iam_role" "ecs_task" {
  name = "subscr-optinist-cloud-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

# Attach standard policies to ECS Task Role
resource "aws_iam_role_policy_attachment" "ecs_task_efs" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonElasticFileSystemClientFullAccess"
}

resource "aws_iam_role_policy_attachment" "ecs_task_cloudwatch" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
}

resource "aws_iam_role_policy_attachment" "ecs_task_ecr" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}
resource "aws_iam_role_policy_attachment" "ecs_instance_ecr" {
  role       = aws_iam_role.ecs_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser"
}

# Custom policy for ECS Exec (SSM)
resource "aws_iam_role_policy" "ecs_task_ssm_exec" {
  name = "subscr-ecs-task-ssm-exec"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel"
        ]
        Resource = "*"
      }
    ]
  })
}

# ECS Instance Role (for EC2 instances running ECS tasks)
# -------------------------------------------------------
resource "aws_iam_role" "ecs_instance_role" {
  name = "subscr-optinist-ecs-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ecs_instance_profile" {
  name = "subscr-optinist-ecs-instance-profile"
  role = aws_iam_role.ecs_instance_role.name
}

# Standard policy attachments for ECS instances
resource "aws_iam_role_policy_attachment" "ecs_instance_role_policy" {
  role       = aws_iam_role.ecs_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "ecs_instance_cloudwatch_agent" {
  role       = aws_iam_role.ecs_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_role_policy_attachment" "ecs_instance_ssm" {
  role       = aws_iam_role.ecs_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Custom policies for ECS instances
resource "aws_iam_role_policy" "ecs_instance_enhanced_monitoring" {
  name = "subscr-ecs-instance-enhanced-monitoring"
  role = aws_iam_role.ecs_instance_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:ListMetrics",
          "logs:PutLogEvents",
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:DescribeLogStreams",
          "autoscaling:Describe*",
          "autoscaling:CompleteLifecycleAction",
          "autoscaling:RecordLifecycleActionHeartbeat",
          "ec2:DescribeVolumes",
          "ec2:DescribeNetworkInterfaces",
          "ecs:ListTasks",
          "ecs:DescribeTasks"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_instance_ssm_complex" {
  name = "subscr-ecs-instance-ssm-complex"
  role = aws_iam_role.ecs_instance_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:SendCommand",
          "ssm:GetCommandInvocation",
          "ssm:UpdateInstanceInformation"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_instance_s3_access" {
  name = "subscr-ecs-instance-s3-access"
  role = aws_iam_role.ecs_instance_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.app_storage.arn,
          "${aws_s3_bucket.app_storage.arn}/*",
          aws_s3_bucket.app_storage_batch.arn,
          "${aws_s3_bucket.app_storage_batch.arn}/*"
        ]
      }
    ]
  })
}

# NAT Instance Role (for NAT gateway instances)
# -----------------------------------------------
resource "aws_iam_role" "nat_instance" {
  name = "subscr-nat-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_instance_profile" "nat_instance" {
  name = "subscr-nat-instance-profile"
  role = aws_iam_role.nat_instance.name
}

# RDS monitoring role
# -------------------
resource "aws_iam_role" "rds_monitoring" {
  name = "subscr-rds-monitoring-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "monitoring.rds.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}


# S3 policy for application storage
# ---------------------------------
resource "aws_s3_bucket_policy" "app_storage" {
  bucket = aws_s3_bucket.app_storage.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowECSTaskAccess"
        Effect    = "Allow"
        Principal = {
          AWS = aws_iam_role.ecs_task.arn
        }
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
          "s3:DeleteObject"
        ]
        Resource = [
          aws_s3_bucket.app_storage.arn,
          "${aws_s3_bucket.app_storage.arn}/*"
        ]
      },
      {
        Sid       = "AllowBatchJobAccess"
        Effect    = "Allow"
        Principal = {
          AWS = aws_iam_role.batch_job.arn
        }
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
          "s3:DeleteObject"
        ]
        Resource = [
          aws_s3_bucket.app_storage.arn,
          "${aws_s3_bucket.app_storage.arn}/*"
        ]
      },
      {
        Sid    = "AllowALBLogsAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::582318560864:root" # ALB service account for ap-northeast-1
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.app_storage.arn}/alb-logs/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
      },
      {
        Sid    = "AllowLogsDeliveryAccess"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.app_storage.arn}/alb-logs/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      {
        Sid    = "AllowALBGetBucketAcl"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::582318560864:root"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.app_storage.arn
      }
    ]
  })
}

resource "aws_s3_bucket_policy" "app_storage_batch" {
  bucket = aws_s3_bucket.app_storage_batch.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowECSTaskAccess"
        Effect    = "Allow"
        Principal = {
          AWS = aws_iam_role.ecs_task.arn
        }
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
          "s3:DeleteObject"
        ]
        Resource = [
          aws_s3_bucket.app_storage_batch.arn,
          "${aws_s3_bucket.app_storage_batch.arn}/*"
        ]
      },
      {
        Sid       = "AllowBatchJobAccess"
        Effect    = "Allow"
        Principal = {
          AWS = aws_iam_role.batch_job.arn
        }
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
          "s3:DeleteObject"
        ]
        Resource = [
          aws_s3_bucket.app_storage_batch.arn,
          "${aws_s3_bucket.app_storage_batch.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_s3" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

# Cloudwatch
# ----------
resource "aws_iam_role_policy" "ecs_task_execution_cloudwatch" {
  name = "subscr-cloudwatch-logs"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      }
    ]
  })
}

# Cloudwatch agent monitoring of ECS
resource "aws_iam_role_policy" "ecs_instance_detailed_monitoring" {
  name = "subscr-ecs-instance-detailed-monitoring"
  role = aws_iam_role.ecs_instance_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData",
          "ec2:DescribeVolumes",
          "ec2:DescribeNetworkInterfaces",
          "logs:PutLogEvents",
          "logs:CreateLogGroup",
          "logs:CreateLogStream"
        ]
        Resource = "*"
      }
    ]
  })
}

# Batch Service
# -------------
resource "aws_iam_role" "batch_service" {
  name = "subscr-optinist-batch-service-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "batch.amazonaws.com"
        }
      }
    ]
  })
}
# Batch Job Execution Role
resource "aws_iam_role" "batch_job" {
  name = "subscr-optinist-batch-job-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "subscr-optinist-batch-job-role"
  }
}

resource "aws_iam_role_policy_attachment" "batch_service_role" {
  role       = aws_iam_role.batch_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"
}

resource "aws_iam_role_policy_attachment" "batch_service_ecs" {
  role       = aws_iam_role.batch_service.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonECS_FullAccess"
}

resource "aws_iam_role_policy_attachment" "batch_job_execution" {
  role       = aws_iam_role.batch_job.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_batch" {
  name = "subscr-ecs-task-batch-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "batch:SubmitJob",
          "batch:DescribeJobs",
          "batch:ListJobs",
          "batch:CancelJob",
          "batch:RegisterJobDefinition"
        ]
        Resource = "*"
      }
    ]
  })
}

# IAM User for this OptiNiSt Cloud project (separate from other webapps)
resource "aws_iam_user" "subscr_optinist_cloud_user" {
  name = "subscr-optinist-cloud-user"
  path = "/"
}

# IAM Policy for this OptiNiSt Cloud User
resource "aws_iam_policy" "subscr_optinist_cloud_user_policy" {
  name        = "subscr-optinist-cloud-user-policy"
  description = "Policy for this OptiNiSt Cloud project"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "batch:SubmitJob",
          "batch:DescribeJobs",
          "batch:ListJobs",
          "batch:CancelJob",
          "batch:TerminateJob",
          "batch:RegisterJobDefinition",
          "batch:DeregisterJobDefinition",
          "batch:DescribeJobQueues",
          "batch:DescribeComputeEnvironments",
          "batch:UpdateComputeEnvironment",
          "batch:TagResource",
          "batch:UntagResource",
          "batch:DescribeJobDefinitions",
          "logs:GetLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "iam:PassRole",
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "ec2:DescribeInstances",
          "ecs:DescribeTasks",
          "ecs:DescribeContainerInstances",
          "ecr:GetAuthorizationToken",
          "ecr:DescribeRepositories",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
          "ecr:GetRepositoryPolicy",
          "cloudwatch:ListMetrics",
          "cloudwatch:GetMetricStatistics",
          "lambda:InvokeFunction"
        ]
        Resource = "*"
      }
    ]
  })
}

# Attach policy to user
resource "aws_iam_user_policy_attachment" "subscr_optinist_cloud_user_policy_attachment" {
  user       = aws_iam_user.subscr_optinist_cloud_user.name
  policy_arn = aws_iam_policy.subscr_optinist_cloud_user_policy.arn
}

# Create access key for the user
resource "aws_iam_access_key" "subscr_optinist_cloud_user_access_key" {
  user = aws_iam_user.subscr_optinist_cloud_user.name
}

# Batch Spot Fleet Role
resource "aws_iam_role" "batch_spot_fleet" {
  name = "subscr-optinist-batch-spot-fleet-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "spotfleet.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "batch_spot_fleet" {
  role       = aws_iam_role.batch_spot_fleet.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole"
}

resource "aws_iam_role_policy" "batch_job_s3" {
  name = "subscr-batch-job-s3-access"
  role = aws_iam_role.batch_job.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.app_storage.arn,
          "${aws_s3_bucket.app_storage.arn}/*",
          aws_s3_bucket.app_storage_batch.arn,
          "${aws_s3_bucket.app_storage_batch.arn}/*"
        ]
      }
    ]
  })
}

# Add ECR access to batch job role
resource "aws_iam_role_policy" "batch_job_ecr_access" {
  name = "subscr-batch-job-ecr-access"
  role = aws_iam_role.batch_job.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
          "ecr:GetRepositoryPolicy"
        ]
        Resource = "*"
      }
    ]
  })
}
# Add S3 access to batch spot fleet role
resource "aws_iam_role_policy" "batch_spot_fleet_s3" {
  name = "subscr-batch-spot-fleet-s3-access"
  role = aws_iam_role.batch_spot_fleet.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow",
        Action = [
            "s3:GetObject",
            "s3:PutObject",
            "s3:DeleteObject",
            "s3:ListBucket"
        ],
        Resource = [
            "arn:aws:s3:::subscr-optinist-batch-app-storage",
            "arn:aws:s3:::subscr-optinist-batch-app-storage/*"
        ]
      }
    ]
  })
}
# Add CloudWatch logs permissions for batch jobs
resource "aws_iam_role_policy" "batch_job_logs" {
  name = "subscr-batch-job-logs-access"
  role = aws_iam_role.batch_job.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:CreateLogGroup"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task_ecr_access" {
  name = "subscr-ecs-task-ecr-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = "*"
      }
    ]
  })
}

# ======================
# PREMIUM TIER IAM ROLES
# ======================


# Premium Manager Lambda Role
resource "aws_iam_role" "premium_manager_lambda" {
  name = "subscr-premium-manager-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "premium_manager_lambda_basic" {
  role       = aws_iam_role.premium_manager_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "premium_manager_lambda_vpc" {
  role       = aws_iam_role.premium_manager_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Premium Manager Lambda Permissions
resource "aws_iam_role_policy" "premium_manager_permissions" {
  name = "subscr-premium-manager-permissions"
  role = aws_iam_role.premium_manager_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeSpotFleetInstances",
          "ec2:DescribeSpotFleetRequests",
          "ec2:ModifySpotFleetRequest",
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceHealth",
          "ec2:StopInstances",
          "ec2:StartInstances",
          "ec2:TerminateInstances",
          "ec2:RunInstances",
          "ec2:CreateTags"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:UpdateService",
          "ecs:RegisterTargets",
          "ecs:DeregisterTargets",
          "ecs:ListTasks",
          "ecs:DescribeTasks",
          "ecs:DescribeContainerInstances",
          "ecs:ListContainerInstances"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:CreateTargetGroup",
          "elasticloadbalancing:DeleteTargetGroup",
          "elasticloadbalancing:CreateRule",
          "elasticloadbalancing:DeleteRule",
          "elasticloadbalancing:ModifyRule",
          "elasticloadbalancing:RegisterTargets",
          "elasticloadbalancing:DeregisterTargets",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeRules",
          "elasticloadbalancing:AddTags",
          "elasticloadbalancing:RemoveTags"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds-data:BatchExecuteStatement",
          "rds-data:BeginTransaction",
          "rds-data:CommitTransaction",
          "rds-data:ExecuteStatement",
          "rds-data:RollbackTransaction"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.app_storage.arn,
          "${aws_s3_bucket.app_storage.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = aws_iam_role.ecs_instance_role.arn
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ec2.amazonaws.com"
          }
        }
      }
    ]
  })
}

# Spot Interruption Handler Lambda Role
resource "aws_iam_role" "spot_interruption_handler" {
  name = "subscr-spot-interruption-handler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "spot_interruption_handler_basic" {
  role       = aws_iam_role.spot_interruption_handler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Cost Controller Lambda Role
resource "aws_iam_role" "cost_controller_lambda" {
  name = "subscr-cost-controller-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "cost_controller_lambda_basic" {
  role       = aws_iam_role.cost_controller_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# =============================
# PREMIUM TIER LAMBDA FUNCTIONS
# =============================

# Premium Manager Lambda Function
resource "aws_lambda_function" "premium_manager" {
  filename      = "${path.module}/premium_manager.py.zip"
  function_name = "subscr-premium-manager"
  role          = aws_iam_role.premium_manager_lambda.arn
  handler       = "premium_manager.handler"
  runtime       = "python3.9"
  timeout       = 300

  source_code_hash = data.archive_file.premium_manager_zip.output_base64sha256

  environment {
    variables = {
      VPC_ID                = aws_vpc.main.id
      SUBNET_IDS            = "${aws_subnet.private1.id},${aws_subnet.private2.id}"
      SECURITY_GROUP_ID     = aws_security_group.ecs.id
      ALB_ARN               = aws_lb.autoscaling.arn
      ALB_LISTENER_ARN      = aws_lb_listener.autoscaling.arn
      PREMIUM_INSTANCE_IDS  = join(",", aws_instance.premium[*].id)
      PREMIUM_LAUNCH_TEMPLATE_ID = aws_launch_template.premium.id
      CLUSTER_NAME          = aws_ecs_cluster.main.name
      RDS_HOST              = aws_db_instance.main.endpoint
      RDS_USER              = var.mysql_user
      RDS_PASSWORD          = var.mysql_password
      RDS_DATABASE          = var.mysql_database
      # Dynamic capacity settings (use existing ABSOLUTE_MAX + minimal new ones)
      PREMIUM_SAFETY_BUFFER      = "1"   # Extra instances for quick response
      PREMIUM_STANDBY_POOL_SIZE  = "1"   # Number of stopped instances to maintain
      PREMIUM_IDLE_TIMEOUT_HOURS = "3"   # Hours before idle instances are converted to standby
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  tags = {
    Name = "Premium Manager Lambda"
    Type = "Premium-Lambda"
    Service = "premium-tier"
  }

  depends_on = [
    aws_iam_role_policy_attachment.premium_manager_lambda_basic,
    aws_cloudwatch_log_group.premium_manager_logs,
    data.archive_file.premium_manager_zip
  ]
}

# Migration Queue Processing Lambda Function

# CloudWatch Log Groups

# CloudWatch Events Rule for Migration Queue Processing (every 2 minutes)

# CloudWatch Events Rule for Premium Cleanup (every hour)
resource "aws_cloudwatch_event_rule" "premium_cleanup_schedule" {
  name                = "subscr-premium-cleanup-schedule"
  description         = "Trigger premium assignment cleanup every hour"
  schedule_expression = "rate(1 hour)"
  state              = "ENABLED"

  tags = {
    Name = "Premium Cleanup Schedule"
    Type = "Premium-CloudWatch"
    Service = "premium-tier"
  }
}

# CloudWatch Events Target for Cleanup
resource "aws_cloudwatch_event_target" "premium_cleanup_target" {
  rule      = aws_cloudwatch_event_rule.premium_cleanup_schedule.name
  target_id = "PremiumCleanupTarget"
  arn       = aws_lambda_function.premium_cleanup.arn

  input = jsonencode({
    source      = "aws.events"
    detail-type = "Scheduled Event"
    detail = {
      action = "cleanup"
    }
  })
}

# =======
# Lambda
# =======

# Lambda Permission for Cleanup CloudWatch Events
resource "aws_lambda_permission" "allow_cloudwatch_cleanup" {
  statement_id  = "AllowExecutionFromCloudWatchCleanup"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.premium_cleanup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.premium_cleanup_schedule.arn
}

# Create ZIP file for premium manager Lambda with dependencies
# Install dependencies first
resource "null_resource" "install_dependencies" {
  provisioner "local-exec" {
    command = <<-EOT
      mkdir -p ${path.module}/premium_manager_package
      /usr/bin/python3 -m pip install pymysql -t ${path.module}/premium_manager_package/ --no-cache-dir
    EOT
  }

  triggers = {
    code_changes = md5(join("", [
      filesha256("${path.module}/premium_manager_package/premium_manager.py"),
      filesha256("${path.module}/../../app/common/core/premium/premium_assignment_service.py")
    ]))
  }
}

# Create ZIP using archive_file
data "archive_file" "premium_manager_zip" {
  type        = "zip"
  source_dir  = "${path.module}/premium_manager_package"
  output_path = "${path.module}/premium_manager.py.zip"

  depends_on = [null_resource.install_dependencies]
}

# CloudWatch Log Group for Premium Manager
resource "aws_cloudwatch_log_group" "premium_manager_logs" {
  name              = "/aws/lambda/subscr-premium-manager"
  retention_in_days = 14

  tags = {
    Name = "Premium Manager Logs"
    Type = "Premium-CloudWatch"
  }
}

# Premium Cleanup Lambda Function
resource "aws_lambda_function" "premium_cleanup" {
  filename      = "${path.module}/premium_cleanup.py.zip"
  function_name = "subscr-premium-cleanup"
  role          = aws_iam_role.premium_manager_lambda.arn
  handler       = "premium_cleanup.handler"
  runtime       = "python3.9"
  timeout       = 300

  source_code_hash = data.archive_file.premium_cleanup_zip.output_base64sha256

  environment {
    variables = {
      VPC_ID                = aws_vpc.main.id
      SUBNET_IDS            = "${aws_subnet.private1.id},${aws_subnet.private2.id}"
      SECURITY_GROUP_ID     = aws_security_group.ecs.id
      ALB_ARN               = aws_lb.autoscaling.arn
      ALB_LISTENER_ARN      = aws_lb_listener.autoscaling.arn
      PREMIUM_INSTANCE_IDS  = join(",", aws_instance.premium[*].id)
      PREMIUM_LAUNCH_TEMPLATE_ID = aws_launch_template.premium.id
      CLUSTER_NAME          = aws_ecs_cluster.main.name
      RDS_HOST              = aws_db_instance.main.endpoint
      RDS_USER              = var.mysql_user
      RDS_PASSWORD          = var.mysql_password
      RDS_DATABASE          = var.mysql_database
      # Cleanup-specific settings
      PREMIUM_IDLE_TIMEOUT_HOURS = "3"
    }
  }

  vpc_config {
    subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.ecs.id]
  }

  tags = {
    Name = "Premium Cleanup Lambda"
    Type = "Premium-Lambda"
    Service = "premium-tier"
  }

  depends_on = [
    aws_iam_role_policy_attachment.premium_manager_lambda_basic,
    aws_cloudwatch_log_group.premium_cleanup_logs,
    data.archive_file.premium_cleanup_zip
  ]
}

# Install dependencies for premium cleanup Lambda
resource "null_resource" "install_cleanup_dependencies" {
  provisioner "local-exec" {
    command = <<-EOT
      mkdir -p ${path.module}/premium_cleanup_package
      cp ${path.module}/premium_cleanup.py ${path.module}/premium_cleanup_package/
      /usr/bin/python3 -m pip install pymysql -t ${path.module}/premium_cleanup_package/ --no-cache-dir
    EOT
  }

  triggers = {
    code_changes = filesha256("${path.module}/premium_cleanup.py")
  }
}

# Create ZIP file for premium cleanup Lambda
data "archive_file" "premium_cleanup_zip" {
  type        = "zip"
  source_dir  = "${path.module}/premium_cleanup_package"
  output_path = "${path.module}/premium_cleanup.py.zip"

  depends_on = [null_resource.install_cleanup_dependencies]
}

# CloudWatch Log Group for Premium Cleanup
resource "aws_cloudwatch_log_group" "premium_cleanup_logs" {
  name              = "/aws/lambda/subscr-premium-cleanup"
  retention_in_days = 14

  tags = {
    Name = "Premium Cleanup Lambda Logs"
    Type = "Premium-CloudWatch"
  }
}

# API Gateway for Premium Management
resource "aws_api_gateway_rest_api" "premium_management" {
  name        = "subscr-premium-management-api"
  description = "API for premium user assignment and management"

  tags = {
    Name = "Premium Management API"
    Type = "Premium-API"
  }
}

# API Gateway Resource for Premium endpoints
resource "aws_api_gateway_resource" "premium_resource" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  parent_id   = aws_api_gateway_rest_api.premium_management.root_resource_id
  path_part   = "premium"
}

# API Gateway Resource for assign endpoint
resource "aws_api_gateway_resource" "premium_assign" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  parent_id   = aws_api_gateway_resource.premium_resource.id
  path_part   = "assign"
}

# API Gateway Resource for release endpoint
resource "aws_api_gateway_resource" "premium_release" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  parent_id   = aws_api_gateway_resource.premium_resource.id
  path_part   = "release"
}

# API Gateway Resource for status endpoint
resource "aws_api_gateway_resource" "premium_status" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  parent_id   = aws_api_gateway_resource.premium_resource.id
  path_part   = "status"
}

# API Gateway Method for assign (POST)
resource "aws_api_gateway_method" "premium_assign_post" {
  rest_api_id   = aws_api_gateway_rest_api.premium_management.id
  resource_id   = aws_api_gateway_resource.premium_assign.id
  http_method   = "POST"
  authorization = "NONE"
}

# API Gateway Method for release (POST)
resource "aws_api_gateway_method" "premium_release_post" {
  rest_api_id   = aws_api_gateway_rest_api.premium_management.id
  resource_id   = aws_api_gateway_resource.premium_release.id
  http_method   = "POST"
  authorization = "NONE"
}

# API Gateway Method for status (GET)
resource "aws_api_gateway_method" "premium_status_get" {
  rest_api_id   = aws_api_gateway_rest_api.premium_management.id
  resource_id   = aws_api_gateway_resource.premium_status.id
  http_method   = "GET"
  authorization = "NONE"
}

# API Gateway Integration for assign
resource "aws_api_gateway_integration" "premium_assign_integration" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  resource_id = aws_api_gateway_resource.premium_assign.id
  http_method = aws_api_gateway_method.premium_assign_post.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.premium_manager.invoke_arn
}

# API Gateway Integration for release
resource "aws_api_gateway_integration" "premium_release_integration" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  resource_id = aws_api_gateway_resource.premium_release.id
  http_method = aws_api_gateway_method.premium_release_post.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.premium_manager.invoke_arn
}

# API Gateway Integration for status
resource "aws_api_gateway_integration" "premium_status_integration" {
  rest_api_id = aws_api_gateway_rest_api.premium_management.id
  resource_id = aws_api_gateway_resource.premium_status.id
  http_method = aws_api_gateway_method.premium_status_get.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.premium_manager.invoke_arn
}

# Lambda permission for API Gateway to invoke premium manager
resource "aws_lambda_permission" "premium_manager_api_gateway" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.premium_manager.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = "${aws_api_gateway_rest_api.premium_management.execution_arn}/*/*"
}

# API Gateway Deployment
resource "aws_api_gateway_deployment" "premium_management_deployment" {
  depends_on = [
    aws_api_gateway_integration.premium_assign_integration,
    aws_api_gateway_integration.premium_release_integration,
    aws_api_gateway_integration.premium_status_integration
  ]

  rest_api_id = aws_api_gateway_rest_api.premium_management.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.premium_assign.id,
      aws_api_gateway_resource.premium_release.id,
      aws_api_gateway_resource.premium_status.id,
      aws_api_gateway_method.premium_assign_post.id,
      aws_api_gateway_method.premium_release_post.id,
      aws_api_gateway_method.premium_status_get.id,
      aws_api_gateway_integration.premium_assign_integration.id,
      aws_api_gateway_integration.premium_release_integration.id,
      aws_api_gateway_integration.premium_status_integration.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

# API Gateway Stage
resource "aws_api_gateway_stage" "premium_management_v1" {
  deployment_id = aws_api_gateway_deployment.premium_management_deployment.id
  rest_api_id   = aws_api_gateway_rest_api.premium_management.id
  stage_name    = "v1"

  tags = {
    Name = "Premium Management API v1"
    Service = "premium-api"
  }
}

# Store AWS credentials in Secrets Manager
resource "aws_secretsmanager_secret" "aws_credentials" {
  name = "subscr-optinist-cloud-credentials"
  description = "AWS credentials for optinist cloud user"
}

resource "aws_secretsmanager_secret_version" "aws_credentials" {
  secret_id = aws_secretsmanager_secret.aws_credentials.id
  secret_string = jsonencode({
    AWS_ACCESS_KEY_ID = aws_iam_access_key.subscr_optinist_cloud_user_access_key.id
    AWS_SECRET_ACCESS_KEY = aws_iam_access_key.subscr_optinist_cloud_user_access_key.secret
  })
}


# ===================
# AWS Batch resources
# ===================

resource "aws_batch_job_queue" "free_plan" {
  name     = "subscr-optinist-free-queue"
  state    = "ENABLED"
  priority = 1
  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.free_plan.arn
  }

  depends_on = [aws_batch_compute_environment.free_plan]

  lifecycle {
    create_before_destroy = true
    prevent_destroy = false
  }
}

resource "aws_batch_job_queue" "paid_plan" {
  name     = "subscr-optinist-paid-queue"
  state    = "ENABLED"
  priority = 10  # Higher priority than free plan
  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.paid_plan.arn
  }

  depends_on = [aws_batch_compute_environment.paid_plan]

  lifecycle {
    create_before_destroy = true
    prevent_destroy = false
  }
}

resource "aws_batch_compute_environment" "free_plan" {
  name = "subscr-optinist-batch-free-plan"
  type                    = "MANAGED"
  state                   = "ENABLED"
  service_role           = aws_iam_role.batch_service.arn
  depends_on = [time_sleep.batch_role_propagation]

  compute_resources {
    type                = "EC2"
    min_vcpus          = 0
    max_vcpus          = 5
    desired_vcpus      = 0
    instance_type      = ["m5.large", "m5.xlarge"]

    subnets            = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.batch.id]

    instance_role = aws_iam_instance_profile.ecs_instance_profile.arn

    tags = {
      Name = "subscr-optinist-batch-free"
    }
  }
}

resource "aws_batch_compute_environment" "paid_plan" {
  name = "subscr-optinist-batch-paid-plan"
  type                    = "MANAGED"
  state                   = "ENABLED"
  service_role           = aws_iam_role.batch_service.arn
  depends_on = [time_sleep.batch_role_propagation]

  compute_resources {
    type                = "EC2"
    min_vcpus          = 0
    max_vcpus          = 10
    desired_vcpus      = 0
    instance_type      = ["optimal"]

    # Same network setup as ECS
    subnets            = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.batch.id]

    instance_role = aws_iam_instance_profile.ecs_instance_profile.arn

    tags = {
      Name = "subscr-optinist-batch-paid"
    }
  }
}

# ==================================================
# CloudWatch Log Groups for Comprehensive Monitoring
# ==================================================
resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/subscr-optinist-cloud-taskdef"
  retention_in_days = 7

  tags = {
    Name = "subscr-optinist-cloud-logs"
  }
}

resource "aws_cloudwatch_log_group" "autoscaling" {
  name              = "/aws/autoscaling/subscr-optinist"
  retention_in_days = 14

  tags = {
    Name = "subscr-optinist-autoscaling-logs"
  }
}

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "subscr-optinist-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = "120"
  statistic           = "Average"
  threshold           = "60"
  alarm_description   = "This metric monitors ECS CPU utilization"
  alarm_actions       = [aws_autoscaling_policy.scale_up.arn]
  dimensions = {
    ServiceName = aws_ecs_service.autoscaling.name
    ClusterName = aws_ecs_cluster.main.name
  }
}

resource "aws_cloudwatch_metric_alarm" "memory_high" {
  alarm_name          = "subscr-optinist-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = "120"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "This metric monitors memory utilization"
  alarm_actions       = [aws_autoscaling_policy.scale_up.arn]
  dimensions = {
    ServiceName = aws_ecs_service.autoscaling.name
    ClusterName = aws_ecs_cluster.main.name
  }
}

resource "aws_cloudwatch_metric_alarm" "cpu_low" {
  alarm_name          = "subscr-optinist-cpu-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/AutoScaling"
  period              = "120"
  statistic           = "Average"
  threshold           = "20"
  alarm_description   = "This metric monitors low cpu utilization"
  alarm_actions       = [aws_autoscaling_policy.scale_down.arn]  # Enabled: dual CPU+memory scaling
  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.main.name
  }
}

resource "aws_cloudwatch_metric_alarm" "memory_low" {
  alarm_name          = "subscr-optinist-memory-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = "120"
  statistic           = "Average"
  threshold           = "10"
  alarm_description   = "This metric monitors memory utilization"
  alarm_actions       = [aws_autoscaling_policy.scale_down.arn]
  dimensions = {
    ServiceName = aws_ecs_service.autoscaling.name
    ClusterName = aws_ecs_cluster.main.name
  }
}

resource "aws_cloudwatch_log_group" "application" {
  name              = "/aws/application/subscr-optinist"
  retention_in_days = 14

  tags = {
    Name = "subscr-optinist-application-logs"
  }
}

resource "aws_cloudwatch_log_group" "batch" {
  name              = "/aws/batch/job"
  retention_in_days = 14

  tags = {
    Name = "subscr-optinist-batch-logs"
  }
  depends_on = [
    aws_batch_job_queue.free_plan,
    aws_batch_job_queue.paid_plan,
    aws_batch_compute_environment.free_plan,
    aws_batch_compute_environment.paid_plan
  ]
  lifecycle {
    ignore_changes = [name]
    prevent_destroy = false
  }
}

# CloudWatch Log Groups for Batch ECS Service (Isolated)
resource "aws_cloudwatch_log_group" "ecs_batch" {
  name              = "/ecs/subscr-optinist-batch-cloud-taskdef"
  retention_in_days = 7

  tags = {
    Name = "subscr-optinist-batch-ecs-logs"
    Service = "batch"
  }
}

resource "aws_cloudwatch_log_group" "batch_application" {
  name              = "/aws/application/subscr-optinist-batch"
  retention_in_days = 14

  tags = {
    Name = "subscr-optinist-batch-application-logs"
    Service = "batch"
  }
}

resource "aws_cloudwatch_log_metric_filter" "user_cpu_usage" {
  name           = "user-cpu-usage"
  log_group_name = aws_cloudwatch_log_group.ecs.name
  pattern        = "[timestamp, level, user_id, cpu_usage]"

  metric_transformation {
    name      = "UserCPUUsage"
    namespace = "OptiNiSt/Application"
    value     = "$cpu_usage"
  }
}

# Custom CloudWatch Metrics for Premium Tracking
resource "aws_cloudwatch_log_metric_filter" "premium_assignments" {
  name           = "premium-assignments"
  log_group_name = aws_cloudwatch_log_group.premium_manager_logs.name
  pattern        = "[timestamp, level=\"INFO\", message=\"Successfully assigned premium user*\"]"

  metric_transformation {
    name      = "ActiveAssignments"
    namespace = "OptiNiSt/Premium"
    value     = "1"
  }
}
# Create ZIP using archive_file for cost tracker Lambda
data "archive_file" "cost_tracker_zip" {
  type        = "zip"
  source_dir  = "${path.module}/cost_tracker_package"
  output_path = "${path.module}/cost_tracker.py.zip"
}

# Cost Tracking Lambda Function
resource "aws_lambda_function" "cost_tracker" {
  filename         = "${path.module}/cost_tracker.py.zip"
  function_name    = "subscr-cost-tracker"
  role             = aws_iam_role.premium_manager_lambda.arn
  handler          = "cost_tracker.handler"
  runtime          = "python3.9"
  timeout          = 300
  source_code_hash = data.archive_file.cost_tracker_zip.output_base64sha256

  environment {
    variables = {
      ASG_NAME      = aws_autoscaling_group.main.name
      REGION        = var.aws_region
      INSTANCE_TYPE = "t3.large"
    }
  }

  tags = {
    Name    = "Cost Tracker Lambda"
    Service = "cost-monitoring"
  }

  depends_on = [
    aws_iam_role_policy_attachment.premium_manager_lambda_basic
  ]
}

# EventBridge rule to trigger cost tracker Lambda hourly
resource "aws_cloudwatch_event_rule" "cost_tracker_schedule" {
  name                = "subscr-cost-tracker-schedule"
  description         = "Trigger cost tracker Lambda hourly to publish cost metrics"
  schedule_expression = "rate(1 hour)"

  tags = {
    Name    = "Cost Tracker Schedule"
    Service = "cost-monitoring"
  }
}

resource "aws_cloudwatch_event_target" "cost_tracker" {
  rule      = aws_cloudwatch_event_rule.cost_tracker_schedule.name
  target_id = "CostTrackerLambda"
  arn       = aws_lambda_function.cost_tracker.arn
}

resource "aws_lambda_permission" "allow_eventbridge_cost_tracker" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_tracker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cost_tracker_schedule.arn
}

# Essential CloudWatch Alarms for Premium Monitoring
resource "aws_cloudwatch_metric_alarm" "premium_cost_high" {
  alarm_name          = "subscr-premium-monthly-cost-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "TotalMonthlyCost"
  namespace           = "OptiNiSt/Cost"
  period              = "86400"  # Daily
  statistic           = "Maximum"
  threshold           = "500"
  alarm_description   = "Monthly cost estimate is high"
  alarm_actions       = []

  tags = {
    Name = "High Monthly Cost Alarm"
    Service = "cost-monitoring"
  }
}

resource "aws_cloudwatch_metric_alarm" "premium_cpu_high" {
  alarm_name          = "subscr-premium-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "Premium ECS service CPU utilization is high"

  dimensions = {
    ServiceName = aws_ecs_service.premium.name
    ClusterName = aws_ecs_cluster.main.name
  }

  tags = {
    Name = "Premium CPU High Alarm"
    Service = "premium-monitoring"
  }
}

resource "aws_cloudwatch_metric_alarm" "premium_memory_high" {
  alarm_name          = "subscr-premium-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "3"
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = "300"
  statistic           = "Average"
  threshold           = "85"
  alarm_description   = "Premium ECS service memory utilization is high"

  dimensions = {
    ServiceName = aws_ecs_service.premium.name
    ClusterName = aws_ecs_cluster.main.name
  }

  tags = {
    Name = "Premium Memory High Alarm"
    Service = "premium-monitoring"
  }
}


# CloudWatch Dashboard for monitoring both Free and Premium tiers
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "subscr-optinist-monitoring"

  dashboard_body = jsonencode({
    widgets = [
      # Row 1: CPU and Memory Comparison - Free vs Premium
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Free Tier CPU" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Free Tier Memory" }],
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.premium.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Premium CPU" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.premium.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Premium Memory" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Free vs Premium: CPU & Memory Utilization"
          period  = 300
          yAxis = {
            left = {
              min = 0
              max = 100
            }
          }
        }
      },
      # Row 1: Instance Counts and Capacity with Autoscaling
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/AutoScaling", "GroupDesiredCapacity", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "Free Tier Desired" }],
            ["AWS/AutoScaling", "GroupInServiceInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "Free Tier Running" }],
            ["AWS/AutoScaling", "GroupMinSize", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "Free Tier Min" }],
            ["AWS/AutoScaling", "GroupMaxSize", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "Free Tier Max" }],
            ["AWS/EC2", "InstanceCount", "InstanceId", aws_instance.premium[0].id, { "label": "Premium 1" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Autoscaling: Free Tier Capacity Management"
          period  = 300
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # Row 2: Detailed EC2 Metrics
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Free Tier ECS CPU", "stat": "Average" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Free Tier ECS Memory", "stat": "Average" }],
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.premium.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Premium ECS CPU", "stat": "Average" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.premium.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Premium ECS Memory", "stat": "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "ECS Service Metrics: CPU & Memory"
          period  = 300
          yAxis = {
            left = {
              min = 0
              max = 100
            }
          }
        }
      },
      # Row 2: Cost Metrics
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Billing", "EstimatedCharges", "Currency", "USD", "ServiceName", "AmazonEC2", { "label": "EC2 Costs" }],
            ["AWS/Billing", "EstimatedCharges", "Currency", "USD", "ServiceName", "AmazonECS", { "label": "ECS Costs" }],
            ["AWS/Billing", "EstimatedCharges", "Currency", "USD", "ServiceName", "AmazonElasticLoadBalancing", { "label": "ALB Costs" }],
            ["Optinist/CostTracking", "PremiumInstanceCount", { "label": "Premium Instances", "yAxis": "right" }],
            ["Optinist/CostTracking", "FreeInstanceCount", { "label": "Free Tier Instances", "yAxis": "right" }],
            ["Optinist/CostTracking", "PremiumUtilization", { "label": "Premium Utilization %", "yAxis": "right" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Cost Tracking & Instance Counts"
          period  = 3600
          stat    = "Maximum"
        }
      },
      # Row 3: Load Balancer Performance
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.autoscaling.arn_suffix, { "label": "Free Tier Requests" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.autoscaling.arn_suffix, { "label": "Free Tier Response Time" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_2XX_Count", "LoadBalancer", aws_lb.autoscaling.arn_suffix, { "label": "Free Tier 2XX" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.autoscaling.arn_suffix, { "label": "Free Tier 5XX" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "ALB Performance: Free Tier & Premium Routing"
          period  = 300
        }
      },
      # Row 3: Premium-specific Metrics
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["OptiNiSt/Premium", "ActiveAssignments", { "label": "Active Premium Users" }],
            ["OptiNiSt/Premium", "InstanceUtilization", { "label": "Premium Instance Utilization %" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.premium_manager.function_name, { "label": "Premium Manager Duration" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.premium_manager.function_name, { "label": "Premium Manager Errors" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Premium Tier Operations"
          period  = 300
        }
      },
      # Row 4: Batch Processing Metrics
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.batch.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Batch CPU" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.batch.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "Batch Memory" }],
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.batch.arn_suffix, { "label": "Batch Requests" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.batch.arn_suffix, { "label": "Batch Response Time" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Batch Processing Performance"
          period  = 300
        }
      },
      # Row 4: System Health Overview
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.id, { "label": "RDS CPU" }],
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", aws_db_instance.main.id, { "label": "RDS Connections" }],
            ["AWS/EFS", "ClientConnections", "FileSystemId", aws_efs_file_system.snmk.id, { "label": "EFS Connections" }],
            ["AWS/EFS", "DataReadIOBytes", "FileSystemId", aws_efs_file_system.snmk.id, { "label": "EFS Read I/O" }],
            ["AWS/EFS", "DataWriteIOBytes", "FileSystemId", aws_efs_file_system.snmk.id, { "label": "EFS Write I/O" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Infrastructure Health: RDS & EFS"
          period  = 300
        }
      },
      # Row 5: Autoscaling Activity and Events
      {
        type   = "metric"
        x      = 0
        y      = 24
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/AutoScaling", "GroupTotalInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "Total Instances" }],
            ["AWS/AutoScaling", "GroupPendingInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "Pending Instances" }],
            ["AWS/AutoScaling", "GroupTerminatingInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "Terminating Instances" }],
            ["AWS/AutoScaling", "GroupStandbyInstances", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "Standby Instances" }]
          ]
          view    = "timeSeries"
          stacked = true
          region  = "ap-northeast-1"
          title   = "Autoscaling Activity: Instance Lifecycle"
          period  = 300
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # Row 5: Autoscaling Metrics and Alarms
      {
        type   = "metric"
        x      = 12
        y      = 24
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["CWAgent", "mem_used_percent", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "Memory Utilization %" }],
            ["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", aws_autoscaling_group.main.name, { "label": "CPU Utilization %" }],
            ["AWS/ECS", "CPUUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "ECS CPU %" }],
            ["AWS/ECS", "MemoryUtilization", "ServiceName", aws_ecs_service.autoscaling.name, "ClusterName", aws_ecs_cluster.main.name, { "label": "ECS Memory %" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = "ap-northeast-1"
          title   = "Autoscaling Triggers: CPU & Memory Thresholds"
          period  = 300
          yAxis = {
            left = {
              min = 0
              max = 100
            }
          }
          annotations = {
            horizontal = [
              {
                label = "CPU Scale Up (60%)"
                value = 60
              },
              {
                label = "Memory Scale Up (80%)"
                value = 80
              }
            ]
          }
        }
      }
    ]
  })
}


# ======================================
# Launch Template for Auto Scaling Group
# ======================================

# Get the latest ECS-optimized AMI
data "aws_ami" "ecs_optimized" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-ecs-hvm-*-x86_64-ebs"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_launch_template" "ecs" {
  name_prefix   = "subscr-optinist-ecs-"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = "t3.large"
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 30
      volume_type = "gp3"
      encrypted   = true
    }
  }

  monitoring {
    enabled = true
  }

user_data = base64encode(<<-EOF
    #!/bin/bash
    set -e
    exec > /var/log/ecs-setup.log 2>&1

    echo "$(date): Starting ECS setup with OptiNiSt configuration"

    # ECS Configuration
    echo ECS_CLUSTER=${aws_ecs_cluster.main.name} >> /etc/ecs/ecs.config
    echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
    echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config
    echo ECS_INSTANCE_ATTRIBUTES='{"tier":"free"}' >> /etc/ecs/ecs.config

    # Install packages
    yum update -y
    yum install -y amazon-ssm-agent mysql amazon-efs-utils nc mysql-client git docker amazon-cloudwatch-agent awscli

    # Start SSM agent
    if ! systemctl is-active --quiet amazon-ssm-agent; then
        systemctl enable amazon-ssm-agent
        systemctl start amazon-ssm-agent
    fi

    # Create CloudWatch agent config
    cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'CW_CONFIG'
    {
        "metrics": {
            "namespace": "CWAgent",
            "metrics_collected": {
                "mem": {
                    "measurement": [
                        "mem_used_percent"
                    ]
                },
                "cpu": {
                    "measurement": [
                        "cpu_usage_idle",
                        "cpu_usage_iowait"
                    ],
                    "totalcpu": true
                }
            }
        }
    }
    CW_CONFIG

    # Start CloudWatch agent
    /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
        -a fetch-config -m ec2 -s \
        -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

    # Start Docker (using same safe approach)
    if ! systemctl is-active --quiet docker; then
        systemctl enable docker || echo "$(date): Docker enable failed"
        systemctl start docker || echo "$(date): Docker start failed"
    fi
    for user in ec2-user ssm-user; do
      if id "$user" &>/dev/null; then
          usermod -a -G docker "$user" && echo "$(date): Added $user to docker group"
          break
      fi
    done

    # Clone and build OptiNiSt
    echo "$(date): Cloning OptiNiSt repository"
    cd /opt
        git clone -b ${var.git_branch} ${var.git_repo} optinist-for-cloud || {
        echo "$(date): ERROR: Git clone failed!"
        exit 1
    }
    if [ ! -d "optinist-for-cloud" ]; then
        echo "$(date): ERROR: Repository directory not created"
        exit 1
    fi
    cd optinist-for-cloud

    # Create Firebase configuration files on the host
    echo "$(date): Creating Firebase configuration files"
    mkdir -p /opt/optinist-for-cloud/studio/config/auth

    # Create firebase_config.json
    cat > /opt/optinist-for-cloud/studio/config/auth/firebase_config.json << 'FIREBASE_CONFIG'
    ${var.firebase_config_json}
    FIREBASE_CONFIG

    # Create firebase_private.json
    cat > /opt/optinist-for-cloud/studio/config/auth/firebase_private.json << 'FIREBASE_PRIVATE'
    ${var.firebase_private_json}
    FIREBASE_PRIVATE

    # Set proper permissions
    chmod 644 /opt/optinist-for-cloud/studio/config/auth/firebase_*.json

    # Add AWS Batch plugins to Dockerfile
    echo "$(date): Adding AWS Batch plugins to Dockerfile"
    # Build the Docker image
    echo "$(date): Building OptiNiSt Docker image"
    if [ ! -f "studio/config/docker/Dockerfile" ]; then
        echo "ERROR: Dockerfile not found in repository"
        ls -la
        exit 1
    fi

    # ECR login and pull pre-built image
    echo "$(date): Logging into ECR and pulling pre-built image"
    aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin ${split("/", var.ecr_repository_url)[0]}
    echo "$(date): Pulling OptiNiSt Docker image from ECR"
    docker pull "${var.ecr_repository_url}:latest" || {
        echo "ERROR: Docker pull failed!"
        exit 1
    }

    # EFS setup
    mkdir -p /mnt/efs
    echo "${aws_efs_file_system.snmk.id}.efs.ap-northeast-1.amazonaws.com:/ /mnt/efs efs tls,_netdev" >> /etc/fstab
    mount -a || echo "EFS will retry"

    # Test DB connection (non-blocking)
    nc -z ${replace(aws_db_instance.main.endpoint, ":3306", "")} 3306 && echo "DB accessible" || echo "DB will be available"
    EOF
  )
  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "subscr-optinist-asg-instance"
      Type = "ECS-ASG"
      Service = "autoscaling"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# =============================================================================
# PREMIUM TIER INFRASTRUCTURE
# =============================================================================

# Premium Launch Template - Optimized for dedicated premium users
resource "aws_launch_template" "premium" {
  name_prefix   = "subscr-optinist-premium-"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = "t3.large"  # Will be overridden by spot fleet instance types
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 30
      volume_type = "gp3"
      encrypted   = true
    }
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    set -e
    exec > /var/log/ecs-setup.log 2>&1

    echo "$(date): Starting ECS setup with OptiNiSt configuration"

    # ECS Configuration
    echo ECS_CLUSTER=${aws_ecs_cluster.main.name} >> /etc/ecs/ecs.config
    echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
    echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config
    echo ECS_INSTANCE_ATTRIBUTES='{"tier":"premium"}' >> /etc/ecs/ecs.config

    # Install packages
    yum update -y
    yum install -y amazon-ssm-agent mysql amazon-efs-utils nc mysql-client git docker amazon-cloudwatch-agent awscli

    # Start SSM agent
    if ! systemctl is-active --quiet amazon-ssm-agent; then
        systemctl enable amazon-ssm-agent
        systemctl start amazon-ssm-agent
    fi

    # Create CloudWatch agent config
    cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'CW_CONFIG'
    {
        "metrics": {
            "namespace": "CWAgent",
            "metrics_collected": {
                "mem": {
                    "measurement": [
                        "mem_used_percent"
                    ]
                },
                "cpu": {
                    "measurement": [
                        "cpu_usage_idle",
                        "cpu_usage_iowait"
                    ],
                    "totalcpu": true
                }
            }
        }
    }
    CW_CONFIG

    # Start CloudWatch agent
    /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
        -a fetch-config -m ec2 -s \
        -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

    # Start Docker (using same safe approach)
    if ! systemctl is-active --quiet docker; then
        systemctl enable docker || echo "$(date): Docker enable failed"
        systemctl start docker || echo "$(date): Docker start failed"
    fi
    for user in ec2-user ssm-user; do
      if id "$user" &>/dev/null; then
          usermod -a -G docker "$user" && echo "$(date): Added $user to docker group"
          break
      fi
    done

    # Clone and build OptiNiSt
    echo "$(date): Cloning OptiNiSt repository"
    cd /opt
        git clone -b ${var.git_branch} ${var.git_repo} optinist-for-cloud || {
        echo "$(date): ERROR: Git clone failed!"
        exit 1
    }
    if [ ! -d "optinist-for-cloud" ]; then
        echo "$(date): ERROR: Repository directory not created"
        exit 1
    }
    cd optinist-for-cloud

    # Create Firebase configuration files on the host
    echo "$(date): Creating Firebase configuration files"
    mkdir -p /opt/optinist-for-cloud/studio/config/auth

    # Create firebase_config.json
    cat > /opt/optinist-for-cloud/studio/config/auth/firebase_config.json << 'FIREBASE_CONFIG'
    ${var.firebase_config_json}
    FIREBASE_CONFIG

    # Create firebase_private.json
    cat > /opt/optinist-for-cloud/studio/config/auth/firebase_private.json << 'FIREBASE_PRIVATE'
    ${var.firebase_private_json}
    FIREBASE_PRIVATE

    # Set proper permissions
    chmod 644 /opt/optinist-for-cloud/studio/config/auth/firebase_*.json

    # Add AWS Batch plugins to Dockerfile
    echo "$(date): Adding AWS Batch plugins to Dockerfile"
    # Build the Docker image
    echo "$(date): Building OptiNiSt Docker image"
    if [ ! -f "studio/config/docker/Dockerfile" ]; then
        echo "ERROR: Dockerfile not found in repository"
        ls -la
        exit 1
    }

    # ECR login and pull pre-built image
    echo "$(date): Logging into ECR and pulling pre-built image"
    aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin ${split("/", var.ecr_repository_url)[0]}
    echo "$(date): Pulling OptiNiSt Docker image from ECR"
    docker pull "${var.ecr_repository_url}:latest" || {
        echo "ERROR: Docker pull failed!"
        exit 1
    }

    # EFS setup
    mkdir -p /mnt/efs
    echo "${aws_efs_file_system.snmk.id}.efs.ap-northeast-1.amazonaws.com:/ /mnt/efs efs tls,_netdev" >> /etc/fstab
    mount -a || echo "EFS will retry"

    # Test DB connection (non-blocking)
    nc -z ${replace(aws_db_instance.main.endpoint, ":3306", "")} 3306 && echo "DB accessible" || echo "DB will be available"
    EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "subscr-optinist-premium-instance"
      Type = "ECS-Premium"
      Tier = "premium"
      Service = "premium-spot-fleet"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Premium Instances - Base instances managed by Terraform
# Note: Lambda (premium_manager.py) handles dynamic scaling by creating additional
# standby instances and managing instance lifecycle based on user demand
resource "aws_instance" "premium" {
  count = 1  # Start with 1 premium instance as base capacity

  launch_template {
    id      = aws_launch_template.premium.id
    version = "$Latest"
  }

  instance_type = "t3.large"
  subnet_id     = aws_subnet.private1.id

  # On shutdown, stop instance instead of terminating
  instance_initiated_shutdown_behavior = "stop"

  # Prevent accidental termination
  disable_api_termination = false

  tags = {
    Name = "subscr-premium-${count.index + 1}"
    Type = "Premium-Instance"
    Service = "premium-tier"
    Tier = "premium"
    InstanceIndex = count.index + 1
  }

  # Stop instance on creation to reduce costs when not in use
  # Lambda will start instances when users request premium access
  provisioner "local-exec" {
    command = "aws ec2 stop-instances --instance-ids ${self.id} --region ${var.aws_region} || true"
  }


  lifecycle {
    create_before_destroy = true
  }
}



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

      # Create frontend config with ALB DNS
      echo "Creating frontend .env.production..."
      cat > ../../../frontend/.env.production << 'ENV_EOF'
REACT_APP_SERVER_HOST=${aws_lb.autoscaling.dns_name}
REACT_APP_SERVER_PORT=80
REACT_APP_SERVER_PROTO=http
REACT_APP_EXPDB_METADATA_EDITABLE=true
ENV_EOF

      echo "Frontend configuration created:"
      cat ../../../frontend/.env.production

      # Build and push image
      echo "Building and pushing Docker image..."
      chmod +x ecr_build_push.sh
      ./ecr_build_push.sh

      echo "Waiting for ECR image to be available..."
      sleep 60

      echo "✅ Build and push completed successfully"

    EOT
  }
}

resource "null_resource" "build_and_deploy_batch" {
  depends_on = [aws_lb.batch, aws_ecs_service.batch]

  triggers = {
    alb_dns = aws_lb.batch.dns_name
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
      echo "ALB DNS: ${aws_lb.batch.dns_name}"

      # Create frontend config with ALB DNS
      echo "Creating frontend .env.production..."
      cat > ../../../frontend/.env.production << 'ENV_EOF'
REACT_APP_SERVER_HOST=${aws_lb.batch.dns_name}
REACT_APP_SERVER_PORT=80
REACT_APP_SERVER_PROTO=http
REACT_APP_EXPDB_METADATA_EDITABLE=true
ENV_EOF

      echo "Frontend configuration created:"
      cat ../../../frontend/.env.production

      # Build and push image
      echo "Building and pushing Docker image..."
      chmod +x ecr_build_push.sh
      ./ecr_build_push.sh

      echo "Waiting for ECR image to be available..."
      sleep 60

      echo "✅ Build and push completed successfully"

    EOT
  }
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
      echo "✅ Application is ready at: http://${aws_lb.autoscaling.dns_name}"
      echo "✅ Health check: http://${aws_lb.autoscaling.dns_name}/health"
    EOT
  }
}

resource "null_resource" "deploy_to_ecs_batch" {
  depends_on = [null_resource.build_and_deploy_batch]

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      echo "=== Starting ECS deployment ==="

      # Force ECS deployment
      echo "Forcing ECS service deployment..."
      aws ecs update-service \
        --cluster ${aws_ecs_cluster.main.name} \
        --service ${aws_ecs_service.batch.name} \
        --force-new-deployment \
        --region ${var.aws_region}

      echo "Waiting for ECS service to stabilize..."
            # Check if service is already running first
            SERVICE_STATUS=$(aws ecs describe-services \
              --cluster ${aws_ecs_cluster.main.name} \
              --services ${aws_ecs_service.batch.name} \
              --region ${var.aws_region} \
              --query 'services[0].status' --output text)

            if [ "$SERVICE_STATUS" = "ACTIVE" ]; then
              echo "Service is already active, checking running count..."
              RUNNING_COUNT=$(aws ecs describe-services \
                --cluster ${aws_ecs_cluster.main.name} \
                --services ${aws_ecs_service.batch.name} \
                --region ${var.aws_region} \
                --query 'services[0].runningCount' --output text)

              if [ "$RUNNING_COUNT" -gt "0" ]; then
                echo "Service already has $RUNNING_COUNT running tasks"
              else
                echo "Waiting for service to stabilize..."
                timeout 1800 aws ecs wait services-stable \
                  --cluster ${aws_ecs_cluster.main.name} \
                  --services ${aws_ecs_service.batch.name} \
                  --region ${var.aws_region} \
                  --cli-read-timeout 1800 \
                  --cli-connect-timeout 120 || echo "Warning: Service stabilization timed out, but continuing..."
              fi
            else
              echo "Service not active, waiting..."
              timeout 1800 aws ecs wait services-stable \
                --cluster ${aws_ecs_cluster.main.name} \
                --services ${aws_ecs_service.batch.name} \
                --region ${var.aws_region} \
                --cli-read-timeout 1800 \
                --cli-connect-timeout 120 || echo "Warning: Service stabilization timed out, but continuing..."
            fi

      echo "=== DEPLOYMENT COMPLETE ==="
      echo "✅ Application is ready at: http://${aws_lb.batch.dns_name}"
      echo "✅ Health check: http://${aws_lb.batch.dns_name}/health"
    EOT
  }
}

# ========================================
resource "local_file" "app_setup_script" {
  content = <<-EOF
#!/usr/bin/env bash
set -e

LOGFILE="/var/log/app-setup.log"
exec > $LOGFILE 2>&1

echo "$(date): Starting application setup script"

# Function for retries
retry_command() {
    local max_attempts=$1
    local delay=$2
    local command="$${@:3}"

    for i in $$(seq 1 $max_attempts); do
        echo "$$(date): Attempting: $$command (attempt $$i/$$max_attempts)"
        if eval "$$command"; then
            echo "$$(date): Success: $$command"
            return 0
        else
            echo "$$(date): Failed attempt $$I/$$max_attempts"
            [ $$i -lt $max_attempts ] && sleep $$delay
        fi
    done

    echo "$$(date): ERROR: Command failed after $$max_attempts attempts: $$command"
    return 1
}

# Wait for ECS agent to be ready
echo "$(date): Waiting for ECS agent to be ready"
retry_command 10 30 "curl -s http://localhost:51678/v1/metadata >/dev/null"

# Create config files
echo "$(date): Creating configuration files"
mkdir -p /opt/optinist/optinist-for-cloud/studio/config/auth

# Create .env file
cat > /opt/optinist/optinist-for-cloud/studio/config/.env << 'CONFIG_ENV'
SECRET_KEY='${var.optinist_secret_key}'
USE_FIREBASE_TOKEN=True
MYSQL_SERVER=${aws_db_instance.main.endpoint}
MYSQL_DATABASE=${var.mysql_database}
MYSQL_USER=${var.mysql_user}
MYSQL_PASSWORD=${var.mysql_password}
S3_DEFAULT_BUCKET_NAME=${aws_s3_bucket.app_storage.id}
CONFIG_ENV

# Create Firebase config files
cat > /opt/optinist/optinist-for-cloud/studio/config/auth/firebase_config.json << 'FIREBASE_CONFIG'
${var.firebase_config_json}
FIREBASE_CONFIG

cat > /opt/optinist/optinist-for-cloud/studio/config/auth/firebase_private.json << 'FIREBASE_PRIVATE'
${var.firebase_private_json}
FIREBASE_PRIVATE

# Database initialization
echo "$(date): Starting database initialization"

# Install MySQL client for database initialization
echo "$(date): Installing MySQL client"
apt-get update
apt-get install -y mysql-client-core-8.0

retry_command 30 10 "nc -z ${replace(aws_db_instance.main.endpoint, ":3306", "")} 3306"

# Initialize database tables and users
echo "$(date): Initializing database tables"
cat > /tmp/init_optinist_db.sql << 'INIT_SQL'
USE ${var.mysql_database};

-- Insert initial data
INSERT IGNORE INTO organization (name) VALUES ('${var.optinist_org_name}');
INSERT IGNORE INTO roles (id, role) VALUES (1, 'admin'), (10, 'data manager'), (20, 'operator'), (30, 'guest operator');

-- Default admin user with S3 bucket info
INSERT IGNORE INTO users (uid, organization_id, name, email, active, attributes)
VALUES ('${var.optinist_admin_uid}', 1, '${var.optinist_admin_name}', '${var.optinist_admin_email}', true, '{"remote_bucket_name": "${aws_s3_bucket.app_storage.id}"}');

INSERT IGNORE INTO user_roles (user_id, role_id) VALUES (1, 1);

UPDATE users SET attributes = JSON_MERGE_PATCH(IFNULL(attributes,'{}'), '{"remote_bucket_name": "${aws_s3_bucket.app_storage.id}"}') WHERE id = 1;

INIT_SQL

chmod 644 /tmp/init_optinist_db.sql

# Wait for database to be ready and execute initialization
max_attempts=10
attempt=1
while [ $attempt -le $max_attempts ]; do
  echo "$(date): Attempting to initialize database (attempt $attempt/$max_attempts)"
  if mysql -h ${replace(aws_db_instance.main.endpoint, ":3306", "")} -P 3306 -u ${var.mysql_user} -p'${var.mysql_password}' ${var.mysql_database} < /tmp/init_optinist_db.sql; then
    echo "$(date): Database initialization successful"
    break
  else
    echo "$(date): Database initialization attempt $attempt failed, waiting to retry..."
    sleep 30
    attempt=$((attempt+1))
  fi
done

if [ $attempt -gt $max_attempts ]; then
  echo "$(date): ERROR: Failed to initialize the database after $max_attempts attempts"
fi

echo "$(date): Application setup completed successfully"
EOF

  filename = "${path.module}/app_setup.sh"
}

# Upload the setup script to S3 so SSM can download it
resource "aws_s3_object" "app_setup_script" {
  bucket = aws_s3_bucket.app_storage.id
  key    = "scripts/app_setup.sh"
  source = local_file.app_setup_script.filename
  etag   = local_file.app_setup_script.content_md5

  depends_on = [local_file.app_setup_script]

  tags = {
    Name = "OptiNiSt App Setup Script"
  }
}

# SSM document to run setup script
resource "aws_ssm_document" "app_setup" {
  name          = "subscr-optinist-app-setup"
  document_type = "Command"
  document_format = "YAML"

  depends_on = [aws_s3_object.app_setup_script]

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Application setup for OptiNiSt instances"
    parameters = {}
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
            "/tmp/app_setup.sh"
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
  max_concurrency    = "1"
  max_errors         = "0"

  compliance_severity = "HIGH"
}


# ===================
# ECS Task Definition
# ===================
resource "aws_ecs_task_definition" "autoscaling" {
  family                   = "subscr-optinist-cloud-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode            = "bridge"
  cpu                     = 2048
  memory                  = 6144
  task_role_arn          = aws_iam_role.ecs_task.arn
  execution_role_arn     = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name                  = "subscr-optinist-cloud-container"
      image                 = "${var.ecr_repository_url}:latest"
      cpu                   = 1536
      memory                = 5120
      memoryReservation     = 3072
      essential             = true
      workingDirectory      = "/app"
      entryPoint            = ["/bin/sh", "-c"]
      command               = ["./cloud-startup.sh"]

      portMappings = [
        {
          name           = "subscr-optinist-cloud-container-port-8000"
          containerPort  = 8000
          hostPort       = 8000
          protocol       = "tcp"
        }
      ]

      environment = [
        {
          name  = "CLOUDWATCH_LOG_GROUP"
          value = "/ecs/subscr-optinist-cloud-taskdef"
        },
        {
          name  = "PYTHONPATH"
          value = "/app/"
        },
        {
          name  = "TZ"
          value = "Asia/Tokyo"
        },
        {
          name  = "DB_HOST"
          value = split(":", aws_db_instance.main.endpoint)[0]
        },
        {
          name  = "DB_PORT"
          value = split(":", aws_db_instance.main.endpoint)[1]
        },
        {
          name  = "DB_USER"
          value = var.mysql_user
        },
        {
          name  = "DB_NAME"
          value = var.mysql_database
        },
        {
          name  = "DB_PASSWORD"
          value = var.mysql_password
        },
        {
          name  = "BACKEND_HOST"
          value = "0.0.0.0"
        },
        {
          name  = "BACKEND_PORT"
          value = "8000"
        },
        {
          name  = "FRONTEND_SERVER_HOST"
          value = aws_lb.autoscaling.dns_name
        },
        {
          name  = "FRONTEND_SERVER_PORT"
          value = "80"
        },
        {
          name  = "FRONTEND_SERVER_PROTO"
          value = "http"
        },
        {
          name  = "INITIAL_FIREBASE_UID"
          value = var.optinist_admin_uid
        },
        {
          name  = "INITIAL_USER_NAME"
          value = var.optinist_admin_name
        },
        {
          name  = "INITIAL_USER_EMAIL"
          value = var.optinist_admin_email
        },
        {
          name  = "ADMIN_STORAGE_QUOTA_BYTES"
          value = "107374182400"
        },
        {
          name  = "SECRET_KEY"
          value = var.optinist_secret_key
        },
        {
          name = "S3_DEFAULT_BUCKET_NAME"
          value = aws_s3_bucket.app_storage.id
        },
        {
          name = "REMOTE_STORAGE_TYPE"
          value = "2"
        },
        {
          name  = "USE_AWS_BATCH"
          value = "false"
        },
        {
          name  = "LOG_LEVEL"
          value = "DEBUG"
        },
        {
          name  = "UVICORN_ACCESS_LOG"
          value = "1"
        },
        {
          name  = "CORS_ORIGINS"
          value = "*"
        },
        {
          name  = "PYTHONUNBUFFERED"
          value = "1"
        },
        {
          name  = "OPTINIST_DIR"
          value = "/app/studio_data"
        },
        {
          name  = "TEST_USERS_CONFIG"
          value = jsonencode(var.test_users)
        },
        {
          name  = "STRIPE_CALLBACK_URL"
          value = "http://${aws_lb.autoscaling.dns_name}"
        },
        {
          name  = "STRIPE_WEBHOOK_SECRET"
          value = var.stripe_webhook_secret
        },
      ]
      secrets = [
        {
          name      = "AWS_ACCESS_KEY_ID"
          valueFrom = "${aws_secretsmanager_secret.aws_credentials.arn}:AWS_ACCESS_KEY_ID::"
        },
        {
          name      = "AWS_SECRET_ACCESS_KEY"
          valueFrom = "${aws_secretsmanager_secret.aws_credentials.arn}:AWS_SECRET_ACCESS_KEY::"
        },
      ]

      mountPoints = [
        {
          sourceVolume  = "subscr-optinist-cloud-snmk-volume"
          containerPath = "/app/.snakemake"
          readOnly      = false
        },
        {
          sourceVolume  = "subscr-optinist-cloud-studio-data-volume"
          containerPath = "/app/studio_data"
          readOnly      = false
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -v http://127.0.0.1:8000/health"]
        interval    = 300
        timeout     = 5
        retries     = 3
        startPeriod = 300
      }

      dockerLabels = {
        "health.check.enabled" = "true"
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/subscr-optinist-cloud-taskdef"
          "mode"                  = "non-blocking"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"       = "25m"
          "awslogs-region"        = "ap-northeast-1"
          "awslogs-create-group"  = "true"
          "awslogs-stream-prefix" = "ecs"
          "mode"                  = "non-blocking"
        }
      }
    }
  ])

  volume {
    name = "subscr-optinist-cloud-studio-data-volume"
  }

  volume {
    name = "subscr-optinist-cloud-snmk-volume"
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.snmk.id
      root_directory = "/"
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.snmk.id
        iam            = "DISABLED"
      }
    }
  }

  tags = {
    Name = "subscr-optinist-cloud-taskdef"
  }
}

resource "aws_ecs_task_definition" "batch" {
  family                   = "subscr-batch-optinist-cloud-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode            = "bridge"
  cpu                     = 2048
  memory                  = 6144
  task_role_arn          = aws_iam_role.ecs_task.arn
  execution_role_arn     = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name                  = "subscr-batch-optinist-cloud-container"
      image                 = "${var.ecr_batch_repository_url}:latest"
      cpu                   = 1536
      memory                = 5120
      memoryReservation     = 3072
      essential             = true
      workingDirectory      = "/app"
      entryPoint            = ["/bin/sh", "-c"]
      command               = ["./cloud-startup.sh"]

      portMappings = [
        {
          name           = "subscr-optinist-cloud-container-port-8000"
          containerPort  = 8000
          hostPort       = 8000
          protocol       = "tcp"
        }
      ]

      environment = [
        {
          name  = "CLOUDWATCH_LOG_GROUP"
          value = "/ecs/subscr-optinist-batch-cloud-taskdef"
        },
        {
          name  = "PYTHONPATH"
          value = "/app/"
        },
        {
          name  = "TZ"
          value = "Asia/Tokyo"
        },
        {
          name  = "DB_HOST"
          value = split(":", aws_db_instance.batch.endpoint)[0]
        },
        {
          name  = "DB_PORT"
          value = split(":", aws_db_instance.batch.endpoint)[1]
        },
        {
          name  = "DB_USER"
          value = var.mysql_user
        },
        {
          name  = "DB_NAME"
          value = var.mysql_database
        },
        {
          name  = "DB_PASSWORD"
          value = var.mysql_password
        },
        {
          name  = "BACKEND_HOST"
          value = "0.0.0.0"
        },
        {
          name  = "BACKEND_PORT"
          value = "8000"
        },
        {
          name  = "FRONTEND_SERVER_HOST"
          value = aws_lb.batch.dns_name
        },
        {
          name  = "FRONTEND_SERVER_PORT"
          value = "80"
        },
        {
          name  = "FRONTEND_SERVER_PROTO"
          value = "http"
        },
        {
          name  = "INITIAL_FIREBASE_UID"
          value = var.optinist_admin_uid
        },
        {
          name  = "INITIAL_USER_NAME"
          value = var.optinist_admin_name
        },
        {
          name  = "INITIAL_USER_EMAIL"
          value = var.optinist_admin_email
        },
        {
          name  = "ADMIN_STORAGE_QUOTA_BYTES"
          value = "107374182400"
        },
        {
          name  = "SECRET_KEY"
          value = var.optinist_secret_key
        },
        {
          name = "S3_DEFAULT_BUCKET_NAME"
          value = aws_s3_bucket.app_storage_batch.id
        },
        {
          name = "REMOTE_STORAGE_TYPE"
          value = "2"
        },
        {
          name  = "USE_AWS_BATCH"
          value = "true"
        },
        {
          name = "AWS_BATCH_S3_BUCKET_NAME"
          value = aws_s3_bucket.app_storage_batch.id
        },
        {
          name = "AWS_DEFAULT_PROVIDER"
          value = "S3"
        },
        {
          name  = "AWS_BATCH_JOB_ROLE"
          value = aws_iam_role.batch_job.arn
        },
        {
          name  = "AWS_BATCH_JOB_DEFINITION"
          value = "subscr-optinist-snakemake-batch-job-definition"
        },
        {
          name  = "AWS_DEFAULT_REGION"
          value = var.aws_region
        },
        {
          name  = "AWS_BATCH_FREE_QUEUE"
          value = aws_batch_job_queue.free_plan.name
        },
        {
          name  = "AWS_BATCH_PAID_QUEUE"
          value = aws_batch_job_queue.paid_plan.name
        },
        {
          name  = "AWS_BATCH_LOG_STREAM_PREFIX"
          value = "subscr-optinist-for-cloud"
        },
        {
          name  = "AWS_ECR_REPOSITORY"
          value = "${var.ecr_snakemake_batch_repository_url}:latest"
        },
        {
          name  = "AWS_BATCH_LOG_GROUP"
          value = "/aws/batch/job"
        },
        {
          name  = "AWS_BATCH_JOB_TIMEOUT"
          value = "3600"
        },
        {
          name  = "LOG_LEVEL"
          value = "DEBUG"
        },
        {
          name  = "UVICORN_ACCESS_LOG"
          value = "1"
        },
        {
          name  = "CORS_ORIGINS"
          value = "*"
        },
        {
          name  = "PYTHONUNBUFFERED"
          value = "1"
        },
        {
          name  = "OPTINIST_DIR"
          value = "/app/studio_data"
        },
        {
          name  = "TEST_USERS_CONFIG"
          value = jsonencode(var.test_users)
        },
        {
          name  = "STRIPE_CALLBACK_URL"
          value = "http://${aws_lb.batch.dns_name}"
        },
        {
          name  = "STRIPE_WEBHOOK_SECRET"
          value = var.stripe_webhook_secret
        },
      ]
      secrets = [
        {
          name      = "AWS_ACCESS_KEY_ID"
          valueFrom = "${aws_secretsmanager_secret.aws_credentials.arn}:AWS_ACCESS_KEY_ID::"
        },
        {
          name      = "AWS_SECRET_ACCESS_KEY"
          valueFrom = "${aws_secretsmanager_secret.aws_credentials.arn}:AWS_SECRET_ACCESS_KEY::"
        },
      ]

      mountPoints = [
        {
          sourceVolume  = "subscr-batch-optinist-cloud-snmk-volume"
          containerPath = "/app/.snakemake"
          readOnly      = false
        },
        {
          sourceVolume  = "subscr-batch-optinist-cloud-studio-data-volume"
          containerPath = "/app/studio_data"
          readOnly      = false
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -v http://127.0.0.1:8000/health"]
        interval    = 300
        timeout     = 5
        retries     = 3
        startPeriod = 300
      }

      dockerLabels = {
        "health.check.enabled" = "true"
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/subscr-optinist-batch-cloud-taskdef"
          "mode"                  = "non-blocking"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"       = "25m"
          "awslogs-region"        = "ap-northeast-1"
          "awslogs-create-group"  = "true"
          "awslogs-stream-prefix" = "ecs"
          "mode"                  = "non-blocking"
        }
      }
    }
  ])

  volume {
    name = "subscr-batch-optinist-cloud-studio-data-volume"
  }

  volume {
    name = "subscr-batch-optinist-cloud-snmk-volume"
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.batch.id
      root_directory = "/"
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.batch.id
        iam            = "DISABLED"
      }
    }
  }

  tags = {
    Name = "subscr-batch-optinist-cloud-taskdef"
  }
}

# Premium ECS Task Definition - Pre-warmed containers for instant access
resource "aws_ecs_task_definition" "premium" {
  family                   = "subscr-premium-optinist-cloud-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode            = "bridge"
  cpu                     = 2048
  memory                  = 6144
  task_role_arn          = aws_iam_role.ecs_task.arn
  execution_role_arn     = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name                  = "subscr-premium-optinist-cloud-container"
      image                 = "${var.ecr_repository_url}:latest"
      cpu                   = 1536
      memory                = 5120
      memoryReservation     = 3072
      essential             = true
      workingDirectory      = "/app"
      entryPoint            = ["/bin/sh", "-c"]
      command               = ["./cloud-startup.sh"]

      portMappings = [
        {
          name           = "subscr-premium-optinist-cloud-container-port-8000"
          containerPort  = 8000
          hostPort       = 8000
          protocol       = "tcp"
        }
      ]

      environment = [
        {
          name  = "CLOUDWATCH_LOG_GROUP"
          value = "/ecs/subscr-premium-optinist-cloud-taskdef"
        },
        {
          name  = "PYTHONPATH"
          value = "/app/"
        },
        {
          name  = "USER_TIER"
          value = "premium"
        },
        {
          name  = "TZ"
          value = "Asia/Tokyo"
        },
        {
          name  = "DB_HOST"
          value = split(":", aws_db_instance.main.endpoint)[0]
        },
        {
          name  = "DB_PORT"
          value = split(":", aws_db_instance.main.endpoint)[1]
        },
        {
          name  = "DB_USER"
          value = var.mysql_user
        },
        {
          name  = "DB_NAME"
          value = var.mysql_database
        },
        {
          name  = "DB_PASSWORD"
          value = var.mysql_password
        },
        {
          name  = "BACKEND_HOST"
          value = "0.0.0.0"
        },
        {
          name  = "BACKEND_PORT"
          value = "8000"
        },
        {
          name  = "FRONTEND_SERVER_HOST"
          value = aws_lb.autoscaling.dns_name
        },
        {
          name  = "FRONTEND_SERVER_PORT"
          value = "80"
        },
        {
          name  = "FRONTEND_SERVER_PROTO"
          value = "http"
        },
        {
          name  = "INITIAL_FIREBASE_UID"
          value = var.optinist_admin_uid
        },
        {
          name  = "INITIAL_USER_NAME"
          value = var.optinist_admin_name
        },
        {
          name  = "INITIAL_USER_EMAIL"
          value = var.optinist_admin_email
        },
        {
          name  = "ADMIN_STORAGE_QUOTA_BYTES"
          value = "107374182400"
        },
        {
          name  = "SECRET_KEY"
          value = var.optinist_secret_key
        },
        {
          name  = "S3_DEFAULT_BUCKET_NAME"
          value = aws_s3_bucket.app_storage.id
        },
        {
          name  = "REMOTE_STORAGE_TYPE"
          value = "2"
        },
        {
          name  = "USE_AWS_BATCH"
          value = "false"
        },
        {
          name  = "LOG_LEVEL"
          value = "DEBUG"
        },
        {
          name  = "UVICORN_ACCESS_LOG"
          value = "1"
        },
        {
          name  = "CORS_ORIGINS"
          value = "*"
        },
        {
          name  = "PYTHONUNBUFFERED"
          value = "1"
        },
        {
          name  = "OPTINIST_DIR"
          value = "/app/studio_data"
        },
        {
          name  = "STRIPE_CALLBACK_URL"
          value = "http://${aws_lb.autoscaling.dns_name}"
        },
        {
          name  = "STRIPE_WEBHOOK_SECRET"
          value = var.stripe_webhook_secret
        }
      ]

      mountPoints = [
        {
          sourceVolume  = "subscr-premium-optinist-cloud-studio-data-volume"
          containerPath = "/opt/studio/dataset"
          readOnly      = false
        },
        {
          sourceVolume  = "subscr-premium-optinist-cloud-snmk-volume"
          containerPath = "/efs"
          readOnly      = false
        }
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/subscr-premium-optinist-cloud-taskdef"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"       = "25m"
          "awslogs-region"        = "ap-northeast-1"
          "awslogs-create-group"  = "true"
          "awslogs-stream-prefix" = "ecs"
          "mode"                  = "non-blocking"
        }
      }
    }
  ])

  volume {
    name = "subscr-premium-optinist-cloud-studio-data-volume"
  }

  volume {
    name = "subscr-premium-optinist-cloud-snmk-volume"
    efs_volume_configuration {
      file_system_id = aws_efs_file_system.snmk.id
      root_directory = "/"
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.snmk.id
        iam            = "DISABLED"
      }
    }
  }

  tags = {
    Name = "subscr-premium-optinist-cloud-taskdef"
    Tier = "premium"
  }
}

# ===========
# ECS Service
# ===========
resource "aws_ecs_service" "autoscaling" {
  name             = "subscr-optinist-cloud-service"
  cluster          = aws_ecs_cluster.main.id
  task_definition  = aws_ecs_task_definition.autoscaling.arn
  desired_count    = 1
  deployment_maximum_percent        = 200
  deployment_minimum_healthy_percent = 0

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.main.name
    weight           = 1
    base            = 0
  }

  enable_execute_command = true

  load_balancer {
    target_group_arn = aws_lb_target_group.autoscaling.arn
    container_name   = "subscr-optinist-cloud-container"
    container_port   = 8000
  }

  depends_on = [
    aws_autoscaling_group.main,
    aws_db_instance.main,
    aws_lb.autoscaling,
    aws_lb_listener.autoscaling
  ]

  placement_constraints {
    type = "distinctInstance"  # Force different instances
  }

  health_check_grace_period_seconds = 900

  tags = {
    Name = "subscr-optinist-cloud-service"
  }
}

resource "aws_ecs_service" "batch" {
  name             = "subscr-batch-optinist-cloud-service"
  cluster          = aws_ecs_cluster.main.id
  task_definition  = aws_ecs_task_definition.batch.arn
  desired_count    = 1
  deployment_maximum_percent        = 200
  deployment_minimum_healthy_percent = 0
  launch_type      = "EC2"

  enable_execute_command = true

  placement_constraints {
    type       = "memberOf"
    expression = "attribute:ecs.instance-type =~ t3.large and ec2InstanceId == '${aws_instance.batch.id}'"
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.batch.arn
    container_name   = "subscr-batch-optinist-cloud-container"
    container_port   = 8000
  }
}

# Premium ECS Service for pre-warmed containers
resource "aws_ecs_service" "premium" {
  name             = "subscr-premium-optinist-cloud-service"
  cluster          = aws_ecs_cluster.main.id
  task_definition  = aws_ecs_task_definition.premium.arn
  desired_count    = 1
  deployment_maximum_percent        = 200
  deployment_minimum_healthy_percent = 50
  launch_type      = "EC2"

  enable_execute_command = true

  # Target premium spot fleet instances only
  placement_constraints {
    type       = "memberOf"
    expression = "attribute:tier == premium"
  }


  depends_on = [
    aws_instance.premium
  ]
}


# =====================================================
# Dedicated Batch Instance (separate from ASG)
# =====================================================

# Launch template for batch instances
resource "aws_launch_template" "batch" {
  name_prefix   = "subscr-optinist-batch-"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = "t3.large"
  key_name      = aws_key_pair.subscr_optinist_cloud_key_pair.key_name

  vpc_security_group_ids = [aws_security_group.ecs.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.ecs_instance_profile.name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 30
      volume_type = "gp3"
      encrypted   = true
    }
  }

  monitoring {
    enabled = true
  }

user_data = base64encode(<<-EOF
    #!/bin/bash
    set -e
    exec > /var/log/ecs-setup.log 2>&1

    echo "$(date): Starting ECS setup for BATCH instance with OptiNiSt configuration"

    # ECS Configuration
    echo ECS_CLUSTER=${aws_ecs_cluster.main.name} >> /etc/ecs/ecs.config
    echo ECS_ENABLE_CONTAINER_METADATA=true >> /etc/ecs/ecs.config
    echo ECS_ENABLE_TASK_IAM_ROLE=true >> /etc/ecs/ecs.config
    echo ECS_INSTANCE_ATTRIBUTES='{"tier":"free"}' >> /etc/ecs/ecs.config

    # Install packages
    yum update -y
    yum install -y amazon-ssm-agent mysql amazon-efs-utils nc mysql-client git docker amazon-cloudwatch-agent awscli

    # Start SSM agent
    if ! systemctl is-active --quiet amazon-ssm-agent; then
        systemctl enable amazon-ssm-agent
        systemctl start amazon-ssm-agent
    fi

    # Create CloudWatch agent config
    cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'CW_CONFIG'
    {
      "logs": {
        "logs_collected": {
          "files": {
            "collect_list": [
              {
                "file_path": "/var/log/ecs/ecs-init.log",
                "log_group_name": "/aws/batch/subscr-optinist",
                "log_stream_name": "{instance_id}/ecs-init"
              },
              {
                "file_path": "/var/log/ecs/ecs-agent.log.*",
                "log_group_name": "/aws/batch/subscr-optinist",
                "log_stream_name": "{instance_id}/ecs-agent"
              },
              {
                "file_path": "/var/log/docker",
                "log_group_name": "/aws/batch/subscr-optinist",
                "log_stream_name": "{instance_id}/docker"
              }
            ]
          }
        }
      }
    }
CW_CONFIG

    # Start CloudWatch agent
    /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
        -a fetch-config \
        -m ec2 \
        -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
        -s

    # Create OptiNiSt directories
    mkdir -p /opt/optinist-for-cloud
    cd /opt

    # Clone repository if not exists
    if [ ! -d "optinist-for-cloud" ]; then
        echo "$(date): Cloning OptiNiSt repository"
        git clone ${var.git_repo} optinist-for-cloud || {
            echo "ERROR: Git clone failed!"
            exit 1
        }
    fi
    cd optinist-for-cloud

    # Create Firebase configuration files on the host
    echo "$(date): Creating Firebase configuration files"
    mkdir -p /opt/optinist-for-cloud/studio/config/auth

    # Create firebase_config.json
    cat > /opt/optinist-for-cloud/studio/config/auth/firebase_config.json << 'FIREBASE_CONFIG'
    ${var.firebase_config_json}
    FIREBASE_CONFIG

    # Create firebase_private.json
    cat > /opt/optinist-for-cloud/studio/config/auth/firebase_private.json << 'FIREBASE_PRIVATE'
    ${var.firebase_private_json}
    FIREBASE_PRIVATE

    # Set proper permissions
    chmod 644 /opt/optinist-for-cloud/studio/config/auth/firebase_*.json

    # ECR login and pull pre-built batch image
    echo "$(date): Logging into ECR and pulling pre-built batch image"
    aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin ${split("/", var.ecr_batch_repository_url)[0]}
    echo "$(date): Pulling OptiNiSt Batch Docker image from ECR"
    docker pull "${var.ecr_batch_repository_url}:latest" || {
        echo "ERROR: Docker pull failed!"
        exit 1
    }

    # EFS setup (batch-specific)
    mkdir -p /mnt/efs
    echo "${aws_efs_file_system.batch.id}.efs.ap-northeast-1.amazonaws.com:/ /mnt/efs efs tls,_netdev" >> /etc/fstab
    mount -a || echo "EFS will retry"

    # Test DB connection (batch-specific database)
    nc -z ${replace(aws_db_instance.batch.endpoint, ":3306", "")} 3306 && echo "DB accessible" || echo "DB will be available"
    EOF
  )
  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "subscr-optinist-batch-instance"
      Type = "ECS-Batch"
      Service = "batch"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Dedicated EC2 instance for batch service
resource "aws_instance" "batch" {
  launch_template {
    id      = aws_launch_template.batch.id
    version = "$Latest"
  }

  subnet_id = aws_subnet.private1.id

  tags = {
    Name = "subscr-optinist-batch-instance"
    Type = "ECS-Batch-Dedicated"
    Service = "batch"
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_ecs_cluster.main,
    aws_launch_template.batch
  ]
}

# ========================
# AWS batch job definition
# ========================
resource "aws_batch_job_definition" "optinist" {
  name = "subscr-optinist-snakemake-batch-job-definition"
  type = "container"

  # Force new revision when container properties change
  lifecycle {
    create_before_destroy = true
  }

  container_properties = jsonencode({
    image = "${var.ecr_snakemake_batch_repository_url}:latest"
    vcpus = 2
    memory = 4096
    jobRoleArn = aws_iam_role.batch_job.arn


    mountPoints = [
        {
          sourceVolume = "tmp"
          containerPath = "/tmp"
          readOnly = false
        },
        {
          sourceVolume = "efs"
          containerPath = "/mnt/efs"
          readOnly = false
        }
      ]

    volumes = [
        {
          name = "tmp"
          host = {
            sourcePath = "/tmp"
          }
        },
        {
          name = "efs"
          efsVolumeConfiguration = {
            fileSystemId = "${aws_efs_file_system.snmk.id}"
            rootDirectory = "/"
            transitEncryption = "ENABLED"
            authorizationConfig = {
              accessPointId = "${aws_efs_access_point.snmk.id}"
              iam = "DISABLED"
            }
          }
        }
      ]

    environment = [
      {
        name = "DB_HOST"
        value = split(":", aws_db_instance.main.endpoint)[0]
      },
      {
        name = "DB_PORT"
        value = split(":", aws_db_instance.main.endpoint)[1]
      },
      {
        name = "DB_USER"
        value = var.mysql_user
      },
      {
        name = "DB_PASSWORD"
        value = var.mysql_password
      },
      {
        name = "DB_NAME"
        value = var.mysql_database
      },
      {
        name = "AWS_DEFAULT_REGION"
        value = var.aws_region
      },
      {
        name = "S3_DEFAULT_BUCKET_NAME"
        value = aws_s3_bucket.app_storage.id
      },
      {
        name = "USE_AWS_BATCH"
        value = "false"
      },
      {
        name = "PYTHONPATH"
        value = "/app"
      },
      {
        name = "EFS_MOUNT_TARGET"
        value = "/mnt/efs"
      },
      {
        name = "TMPDIR"
        value = "/tmp"
      },
      {
        name = "TMP"
        value = "/tmp"
      },
      {
        name = "IS_STANDALONE"
        value = "true"
      },
      {
        name = "USE_FIREBASE_TOKEN"
        value = "false"
      },
      {
        name = "REMOTE_STORAGE_TYPE"
        value = "2"
      },
      {
        name = "TZ"
        value = "Asia/Tokyo"
      },
      {
        name = "PYTHONUNBUFFERED"
        value = "1"
      },
      {
        name = "OPTINIST_DIR"
        value = "/app/studio_data"
      },
      {
        name = "IN_SNAKEMAKE_BATCH"
        value = "true"
      },
      {
        name = "AWS_BATCH_S3_BUCKET_NAME"
        value = aws_s3_bucket.app_storage.id
      },
    ]
  })
}

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

output "rds_endpoint_batch" {
  description = "RDS instance endpoint (batch)"
  value       = aws_db_instance.batch.endpoint
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

output "alb_dns_name_batch" {
  description = "ALB batch DNS name"
  value       = aws_lb.batch.dns_name
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name_autoscaling" {
  description = "Name of the ECS service"
  value       = aws_ecs_service.autoscaling.name
}

output "ecs_service_name_batch" {
  description = "Name of the ECS batch service"
  value       = aws_ecs_service.batch.name
}

output "efs_id" {
  description = "ID of the EFS file system"
  value       = aws_efs_file_system.snmk.id
}

output "app_storage_bucket" {
  description = "S3 bucket for application storage (autoscaling)"
  value       = aws_s3_bucket.app_storage.id
}

output "app_storage_bucket_batch" {
  description = "S3 bucket for application storage (batch)"
  value       = aws_s3_bucket.app_storage_batch.id
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
    REACT_APP_SERVER_HOST = aws_lb.autoscaling.dns_name
    REACT_APP_SERVER_PORT = "80"
    REACT_APP_SERVER_PROTO = "http"
    REACT_APP_EXPDB_METADATA_EDITABLE=true
  }
}

output "frontend_config_batch" {
  description = "Configuration values for frontend/.env.production"
  value = {
    REACT_APP_SERVER_HOST = aws_lb.batch.dns_name
    REACT_APP_SERVER_PORT = "80"
    REACT_APP_SERVER_PROTO = "http"
    REACT_APP_EXPDB_METADATA_EDITABLE=true
  }
}

output "backend_config" {
  description = "Configuration values for studio/auth/config/.env"
  value = {
    S3_DEFAULT_BUCKET_NAME = aws_s3_bucket.app_storage.id
  }
}

output "aws_batch_config" {
  description = "AWS Batch configuration values for batch_config"
  value = {
    AWS_BATCH_JOB_ROLE = aws_iam_role.batch_job.arn
    AWS_BATCH_S3_BUCKET_NAME = aws_s3_bucket.app_storage_batch.id
    AWS_BATCH_FREE_QUEUE = aws_batch_job_queue.free_plan.name
    AWS_BATCH_PAID_QUEUE = aws_batch_job_queue.paid_plan.name
    AWS_BATCH_JOB_DEFINITION = aws_batch_job_definition.optinist.name
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

output "batch_instance_id" {
  description = "Instance ID of the dedicated batch instance"
  value       = aws_instance.batch.id
}

output "batch_instance_private_ip" {
  description = "Private IP of the dedicated batch instance"
  value       = aws_instance.batch.private_ip
}

output "alb_arn" {
  description = "ARN of the main ALB for premium instance routing"
  value       = aws_lb.autoscaling.arn
}

output "alb_listener_arn" {
  description = "ARN of the main ALB listener for premium routing rules"
  value       = aws_lb_listener.autoscaling.arn
}

output "premium_instance_ids" {
  description = "IDs of the premium standby instances"
  value       = aws_instance.premium[*].id
}

output "premium_api_gateway_url" {
  description = "URL of the premium management API Gateway"
  value       = "https://${aws_api_gateway_rest_api.premium_management.id}.execute-api.${var.aws_region}.amazonaws.com/${aws_api_gateway_stage.premium_management_v1.stage_name}/premium"
}

output "premium_manager_lambda_arn" {
  description = "ARN of the premium manager Lambda function"
  value       = aws_lambda_function.premium_manager.arn
}

output "premium_cleanup_lambda_name" {
  description = "Name of the premium cleanup Lambda function"
  value       = aws_lambda_function.premium_cleanup.function_name
}

output "test_users" {
  description = "Test user configuration for load testing (includes Firebase UIDs)"
  value       = var.test_users
  sensitive   = true
}

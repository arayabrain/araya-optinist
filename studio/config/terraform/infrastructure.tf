# =================
# Network Resources
# =================

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
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.1.0.0/20"
  availability_zone       = "ap-northeast-1a"
  map_public_ip_on_launch = true

  tags = {
    Name = "subscr-optinist-cloud-subnet-public1-ap-northeast-1a"
  }
}

resource "aws_subnet" "public2" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.1.16.0/20"
  availability_zone       = "ap-northeast-1c"
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
  instance_type          = "t3.nano"
  subnet_id              = aws_subnet.public1.id
  vpc_security_group_ids = [aws_security_group.nat_instance.id]
  source_dest_check      = false

  iam_instance_profile = aws_iam_instance_profile.nat_instance.name

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

# Second NAT Instance in AZ 1c
resource "aws_instance" "nat2" {
  ami                    = data.aws_ami.nat_instance.id
  instance_type          = "t3.nano"
  subnet_id              = aws_subnet.public2.id
  vpc_security_group_ids = [aws_security_group.nat_instance.id]
  source_dest_check      = false

  iam_instance_profile = aws_iam_instance_profile.nat_instance.name

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
    Name = "subscr-optinist-nat-instance-2"
  }
}

# Elastic IP for NAT Instance 1
resource "aws_eip" "nat_instance" {
  domain   = "vpc"
  instance = aws_instance.nat.id

  tags = {
    Name = "subscr-optinist-nat-instance-eip"
  }
}

# Elastic IP for NAT Instance 2
resource "aws_eip" "nat_instance2" {
  domain   = "vpc"
  instance = aws_instance.nat2.id

  tags = {
    Name = "subscr-optinist-nat-instance-2-eip"
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

data "aws_network_interface" "nat2" {
  depends_on = [aws_instance.nat2]

  filter {
    name   = "attachment.instance-id"
    values = [aws_instance.nat2.id]
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
    cidr_block           = "0.0.0.0/0"
    network_interface_id = data.aws_network_interface.nat.id
  }

  tags = {
    Name = "subscr-optinist-cloud-rtb-private1-ap-northeast-1a"
  }
}

resource "aws_route_table" "private2" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block           = "0.0.0.0/0"
    network_interface_id = data.aws_network_interface.nat2.id
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


# ECR API endpoint
resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id             = aws_vpc.main.id
  service_name       = "com.amazonaws.ap-northeast-1.ecr.api"
  vpc_endpoint_type  = "Interface"
  subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
  security_group_ids = [aws_security_group.vpc_endpoints.id]

  tags = {
    Name = "subscr-optinist-ecr-api-endpoint"
  }
}

# ECR Docker endpoint
resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id             = aws_vpc.main.id
  service_name       = "com.amazonaws.ap-northeast-1.ecr.dkr"
  vpc_endpoint_type  = "Interface"
  subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
  security_group_ids = [aws_security_group.vpc_endpoints.id]

  tags = {
    Name = "subscr-optinist-ecr-dkr-endpoint"
  }
}

# CloudWatch Logs endpoint
resource "aws_vpc_endpoint" "logs" {
  vpc_id             = aws_vpc.main.id
  service_name       = "com.amazonaws.ap-northeast-1.logs"
  vpc_endpoint_type  = "Interface"
  subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
  security_group_ids = [aws_security_group.vpc_endpoints.id]

  tags = {
    Name = "subscr-optinist-logs-endpoint"
  }
}

# Secrets Manager VPC Endpoint (required for RDS Proxy)
resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id             = aws_vpc.main.id
  service_name       = "com.amazonaws.ap-northeast-1.secretsmanager"
  vpc_endpoint_type  = "Interface"
  subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
  security_group_ids = [aws_security_group.vpc_endpoints.id]

  tags = {
    Name = "subscr-optinist-secretsmanager-endpoint"
  }
}


# =================================
# S3 bucket for application storage
# =================================

resource "aws_s3_bucket" "app_storage" {
  bucket        = "subscr-optinist-app-storage"
  force_destroy = true

  tags = {
    Name        = "Subscr OptiNiSt Application Storage"
    Environment = "Production"
  }
}



resource "aws_s3_bucket_versioning" "app_storage" {
  bucket = aws_s3_bucket.app_storage.id
  versioning_configuration {
    status = "Enabled"
  }
}


# Block all public access to S3
resource "aws_s3_bucket_public_access_block" "app_storage" {
  bucket                  = aws_s3_bucket.app_storage.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}


# ===============
# EFS File System
# ===============
resource "aws_efs_file_system" "snmk" {
  creation_token = "subscr-optinist-cloud-snmk-volume"

  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"

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

# =========
# Databases
# =========

# DynamoDB Table for Terraform State Locking
# This table must be created first before enabling locking in the backend config above
resource "aws_dynamodb_table" "terraform_state_lock" {
  name         = "terraform-state-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

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

# ===
# RDS
# ===
resource "aws_db_subnet_group" "main" {
  name = "subscr-optinist-rds-subnet-group"
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

resource "aws_db_instance" "main" {
  identifier                      = "subscr-optinist-cloud-rds"
  allocated_storage               = 20
  storage_type                    = "gp3"
  engine                          = "mysql"
  engine_version                  = "8.0"
  instance_class                  = "db.t4g.small"
  parameter_group_name            = "default.mysql8.0"
  db_name                         = var.mysql_database
  username                        = var.mysql_user
  password                        = var.mysql_password
  skip_final_snapshot             = true
  final_snapshot_identifier       = "${var.mysql_database}-final-snapshot"
  backup_retention_period         = 7
  monitoring_interval             = 60
  monitoring_role_arn             = aws_iam_role.rds_monitoring.arn
  publicly_accessible             = false
  enabled_cloudwatch_logs_exports = ["error", "general", "slowquery"]
  network_type                    = "IPV4"
  port                            = 3306
  vpc_security_group_ids          = [aws_security_group.rds.id]
  db_subnet_group_name            = aws_db_subnet_group.main.name
  multi_az                        = false
  storage_encrypted               = true

  tags = {
    Name = "subscr-optinist-cloud-rds"
  }
}

resource "aws_db_proxy" "main" {
  name          = "subscr-optinist-rds-proxy"
  engine_family = "MYSQL"
  auth {
    auth_scheme               = "SECRETS"
    secret_arn                = aws_secretsmanager_secret.rds_credentials.arn
    client_password_auth_type = "MYSQL_NATIVE_PASSWORD"
    iam_auth                  = "DISABLED"
  }
  role_arn               = aws_iam_role.rds_proxy.arn
  vpc_subnet_ids         = [aws_subnet.private1.id, aws_subnet.private2.id]
  vpc_security_group_ids = [aws_security_group.rds.id]
  require_tls            = false

  tags = {
    Name = "subscr-optinist-rds-proxy"
  }
}

resource "aws_db_proxy_default_target_group" "main" {
  db_proxy_name = aws_db_proxy.main.name

  connection_pool_config {
    max_connections_percent      = 100
    max_idle_connections_percent = 50
    connection_borrow_timeout    = 120
  }
}

resource "aws_db_proxy_target" "main" {
  db_proxy_name          = aws_db_proxy.main.name
  target_group_name      = aws_db_proxy_default_target_group.main.name
  db_instance_identifier = aws_db_instance.main.identifier
}

# IAM role for RDS Proxy
resource "aws_iam_role" "rds_proxy" {
  name = "subscr-rds-proxy-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "rds.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "rds_proxy_secrets" {
  role = aws_iam_role.rds_proxy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue"
      ]
      Resource = aws_secretsmanager_secret.rds_credentials.arn
    }]
  })
}

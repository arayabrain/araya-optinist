
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
          # COMMENTED OUT - Batch resources disabled
          # aws_s3_bucket.app_storage_batch.arn,
          # "${aws_s3_bucket.app_storage_batch.arn}/*"
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
        Sid    = "AllowECSTaskAccess"
        Effect = "Allow"
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
      # COMMENTED OUT - Batch resources disabled
      # {
      #   Sid    = "AllowBatchJobAccess"
      #   Effect = "Allow"
      #   Principal = {
      #     AWS = aws_iam_role.batch_job.arn
      #   }
      #   Action = [
      #     "s3:GetObject",
      #     "s3:PutObject",
      #     "s3:ListBucket",
      #     "s3:DeleteObject"
      #   ]
      #   Resource = [
      #     aws_s3_bucket.app_storage.arn,
      #     "${aws_s3_bucket.app_storage.arn}/*"
      #   ]
      # },
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
          # COMMENTED OUT - Batch resources disabled
          # "batch:SubmitJob",
          # "batch:DescribeJobs",
          # "batch:ListJobs",
          # "batch:CancelJob",
          # "batch:TerminateJob",
          # "batch:RegisterJobDefinition",
          # "batch:DeregisterJobDefinition",
          # "batch:DescribeJobQueues",
          # "batch:DescribeComputeEnvironments",
          # "batch:UpdateComputeEnvironment",
          # "batch:TagResource",
          # "batch:UntagResource",
          # "batch:DescribeJobDefinitions",
          "logs:GetLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "iam:PassRole",
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:CreateBucket",
          "s3:DeleteBucket",
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

  # Allow RDS Proxy (which uses this same security group) to connect to RDS
  ingress {
    from_port = 3306
    to_port   = 3306
    protocol  = "tcp"
    self      = true
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


# ==============================
# AWS Secrets Manager for storing
# ==============================
# Store AWS credentials in Secrets Manager
resource "aws_secretsmanager_secret" "aws_credentials" {
  name        = "subscr-optinist-cloud-credentials"
  description = "AWS credentials for optinist cloud user"
}

resource "aws_secretsmanager_secret_version" "aws_credentials" {
  secret_id = aws_secretsmanager_secret.aws_credentials.id
  secret_string = jsonencode({
    AWS_ACCESS_KEY_ID     = aws_iam_access_key.subscr_optinist_cloud_user_access_key.id
    AWS_SECRET_ACCESS_KEY = aws_iam_access_key.subscr_optinist_cloud_user_access_key.secret
  })
}

# Store RDS credentials in Secrets Manager
resource "aws_secretsmanager_secret" "rds_credentials" {
  name = "subscr-rds-credentials"
}

resource "aws_secretsmanager_secret_version" "rds_credentials" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id
  secret_string = jsonencode({
    username = var.mysql_user
    password = var.mysql_password
  })
}

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
  public_key = tls_private_key.subscr_optinist_cloud_key.public_key_openssh # Fixed reference

  tags = {
    Name = "subscr-optinist-cloud-key"
  }
}

# Save private key to local file
resource "local_file" "private_key" {
  content         = tls_private_key.subscr_optinist_cloud_key.private_key_pem # Fixed reference
  filename        = "${path.module}/subscr-optinist-cloud-private-key.pem"
  file_permission = "0400"
}

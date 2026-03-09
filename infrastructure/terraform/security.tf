
# ====================
# IAM ROLES & POLICIES
# ====================

# ECS Task Execution Role (for ECS agent to pull images, etc.)
# --------------------------------------------------------------
resource "aws_iam_role" "ecs_task_execution" {
  name = "${local.env_prefix}-cloud-task-execution-role"

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
  name = "${local.env_prefix}-cloud-task-role"

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

# Custom scoped policies for ECS Task Role
resource "aws_iam_role_policy" "ecs_task_efs" {
  name = "${var.environment}-ecs-task-efs-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
          "elasticfilesystem:DescribeFileSystems",
          "elasticfilesystem:DescribeMountTargets"
        ]
        Resource = [
          aws_efs_file_system.snmk.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task_cloudwatch" {
  name = "${var.environment}-ecs-task-cloudwatch-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task_ecr" {
  name = "${var.environment}-ecs-task-ecr-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = "arn:aws:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/*"
      }
    ]
  })
}
resource "aws_iam_role_policy_attachment" "ecs_instance_ecr" {
  role       = aws_iam_role.ecs_instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser"
}

# Custom policy for ECS Exec (SSM)
resource "aws_iam_role_policy" "ecs_task_ssm_exec" {
  name = "${var.environment}-ecs-task-ssm-exec"
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
  name = "${local.env_prefix}-ecs-instance-role"

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
  name = "${local.env_prefix}-ecs-instance-profile"
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
  name = "${var.environment}-ecs-instance-enhanced-monitoring"
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
  name = "${var.environment}-ecs-instance-ssm-complex"
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
  name = "${var.environment}-ecs-instance-s3-access"
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
        ]
      }
    ]
  })
}

# NAT Instance Role (for NAT gateway instances)
# -----------------------------------------------
resource "aws_iam_role" "nat_instance" {
  name = "${var.environment}-nat-instance-role"

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
  name = "${var.environment}-nat-instance-profile"
  role = aws_iam_role.nat_instance.name
}

# RDS monitoring role
# -------------------
resource "aws_iam_role" "rds_monitoring" {
  name = "${var.environment}-rds-monitoring-role"

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
      {
        Sid    = "AllowALBLogsAccess"
        Effect = "Allow"
        Principal = {
          AWS = data.aws_elb_service_account.main.arn
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



# Cloudwatch
# ----------
resource "aws_iam_role_policy" "ecs_task_execution_cloudwatch" {
  name = "${var.environment}-cloudwatch-logs"
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
  name = "${var.environment}-ecs-instance-detailed-monitoring"
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
  name = "${local.env_prefix}-cloud-user"
  path = "/"
}

# IAM Policy for this OptiNiSt Cloud User
resource "aws_iam_policy" "subscr_optinist_cloud_user_policy" {
  name        = "${local.env_prefix}-cloud-user-policy"
  description = "Policy for this OptiNiSt Cloud project"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ecr:GetAuthorizationToken",
          "ecr:DescribeRepositories",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
          "ecr:GetRepositoryPolicy",
          "cloudwatch:ListMetrics",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:DescribeAlarms",
          "autoscaling:DescribeAutoScalingGroups",
          "ecs:ListClusters",
          "ecs:ListContainerInstances"
        ]
        Resource = "*"
      },
      # S3: Allow CRUD only on this environment's buckets
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:CreateBucket",
          "s3:DeleteBucket"
        ]
        Resource = [
          "arn:aws:s3:::${local.env_prefix}-*",
          "arn:aws:s3:::${local.env_prefix}-*/*"
        ]
      },
      # S3: Explicitly deny CRUD on other environments' buckets
      # This ensures no other attached policy can grant cross-environment S3 access
      {
        Sid    = "DenyS3CrossEnvironment"
        Effect = "Deny"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:CreateBucket",
          "s3:DeleteBucket"
        ]
        NotResource = [
          "arn:aws:s3:::${local.env_prefix}-*",
          "arn:aws:s3:::${local.env_prefix}-*/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeTasks",
          "ecs:DescribeContainerInstances",
          "ecs:ListTasks",
          "ecs:DescribeClusters",
          "ecs:DescribeServices",
          "ecs:UpdateService"
        ]
        Resource = [
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/${local.env_prefix}-*",
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${local.env_prefix}-*/*",
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task/${local.env_prefix}-*/*",
          "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:container-instance/${local.env_prefix}-*/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams"
        ]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${var.environment}-*",
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${var.environment}-*:*",
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.environment}-*",
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.environment}-*:*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "autoscaling:SetDesiredCapacity",
          "autoscaling:SuspendProcesses",
          "autoscaling:ResumeProcesses"
        ]
        Resource = "arn:aws:autoscaling:${var.aws_region}:${data.aws_caller_identity.current.account_id}:autoScalingGroup:*:autoScalingGroupName/${local.env_prefix}-*"
      },
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.environment}-*"
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.environment}-*"
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

# S3 access for ECS tasks (scoped to app storage bucket)
resource "aws_iam_role_policy" "ecs_task_s3_access" {
  name = "${var.environment}-ecs-task-s3-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:CreateBucket",
          "s3:DeleteBucket"

        ]
        Resource = [
          aws_s3_bucket.app_storage.arn,
          "${aws_s3_bucket.app_storage.arn}/*",
          "arn:aws:s3:::${var.s3_user_bucket_prefix}-*",
          "arn:aws:s3:::${var.s3_user_bucket_prefix}-*/*"
        ]
      }
    ]
  })
}

# Lambda invocation for premium assignment
resource "aws_iam_role_policy" "ecs_task_lambda_invoke" {
  name = "${var.environment}-ecs-task-lambda-invoke"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = [
          aws_lambda_function.premium_manager.arn,
          aws_lambda_function.free_manager.arn
        ]
      }
    ]
  })
}

# Secrets Manager access for routing HMAC key
resource "aws_iam_role_policy" "ecs_task_routing_secret" {
  name = "${var.environment}-ecs-task-routing-secret"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.routing_hmac_key.arn
        ]
      }
    ]
  })
}

# Secrets Manager access for all OptiNiSt application secrets
# This allows ECS instances to read secrets for app_setup.sh
resource "aws_iam_role_policy" "ecs_instance_secrets_access" {
  name = "${var.environment}-ecs-instance-secrets-access"
  role = aws_iam_role.ecs_instance_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = [
          aws_secretsmanager_secret.firebase_config.arn,
          aws_secretsmanager_secret.firebase_private_key.arn,
          aws_secretsmanager_secret.database_config.arn,
          aws_secretsmanager_secret.app_config.arn,
          aws_secretsmanager_secret.stripe_config.arn
        ]
      }
    ]
  })
}

# ECS metadata access for instance ID retrieval
# Required by cloud-startup.sh to get EC2 instance ID via ECS API fallback
# when EC2 metadata service is unavailable
resource "aws_iam_role_policy" "ecs_task_metadata" {
  name = "${var.environment}-ecs-task-metadata-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeTasks",
          "ecs:DescribeContainerInstances"
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
  name        = "${var.environment}-ecs-optinist-cloud-security-group"
  description = "Created by Terraform for ECS"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.env_prefix}-cloud-sg-ecs"
  }
}

# Allow internal VPC traffic (ECS-to-ECS, Lambda-to-ECS, NAT routing)
resource "aws_security_group_rule" "ecs_ingress_vpc" {
  type              = "ingress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = [aws_vpc.main.cidr_block]
  security_group_id = aws_security_group.ecs.id
  description       = "Allow all traffic from within VPC"
}

# Allow ALB to reach ECS on dynamic ports (required for ECS dynamic port mapping)
resource "aws_security_group_rule" "ecs_ingress_dynamic_ports" {
  type                     = "ingress"
  from_port                = 32768
  to_port                  = 65535
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb.id
  security_group_id        = aws_security_group.ecs.id
  description              = "ALB to ECS dynamic port mapping"
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
  name        = "${local.env_prefix}-alb-security-group"
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

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.env_prefix}-alb-sg"
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
  name        = "${local.env_prefix}-rds-security-group"
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
    Name = "${local.env_prefix}-cloud-sg-rds"
  }

  lifecycle {
    ignore_changes = [ingress]
  }
}

resource "aws_security_group" "efs" {
  name        = "${local.env_prefix}-cloud-efs-sg"
  description = "Security group for EFS mount targets"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  tags = {
    Name = "${local.env_prefix}-cloud-efs-sg"
  }
}

resource "aws_security_group" "nat_instance" {
  name        = "${var.environment}-nat-instance-sg"
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
    Name = "${local.env_prefix}-nat-instance-sg"
  }
}

# Security Group for VPC Endpoints
resource "aws_security_group" "vpc_endpoints" {
  name        = "${local.env_prefix}-vpc-endpoints-sg"
  description = "Security group for VPC endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  tags = {
    Name = "${local.env_prefix}-vpc-endpoints-sg"
  }
}


# ==============================
# AWS Secrets Manager for storing
# ==============================
# Store AWS credentials in Secrets Manager
resource "aws_secretsmanager_secret" "aws_credentials" {
  name        = "${local.env_prefix}-cloud-credentials"
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
  name = "${var.environment}-rds-credentials"
}

resource "aws_secretsmanager_secret_version" "rds_credentials" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id
  secret_string = jsonencode({
    username = var.mysql_user
    password = var.mysql_password
  })
}

# Store HMAC secret for routing token verification
resource "random_password" "routing_hmac_key" {
  length  = 64
  special = true
}

resource "aws_secretsmanager_secret" "routing_hmac_key" {
  name        = "${var.environment}-premium-routing-hmac-key"
  description = "HMAC secret key for premium routing token verification"
}

resource "aws_secretsmanager_secret_version" "routing_hmac_key" {
  secret_id = aws_secretsmanager_secret.routing_hmac_key.id
  secret_string = jsonencode({
    key = random_password.routing_hmac_key.result
  })
}

# Internal API secret for Lambda-to-backend communication
# Used to authenticate internal sync endpoints called by Lambda managers
resource "random_password" "internal_api_secret" {
  length  = 64
  special = false # Avoid special chars for easier URL/header handling
}

resource "aws_secretsmanager_secret" "internal_api_secret" {
  name        = "${var.environment}-internal-api-secret"
  description = "Secret for internal API authentication between Lambda and backend"
}

resource "aws_secretsmanager_secret_version" "internal_api_secret" {
  secret_id = aws_secretsmanager_secret.internal_api_secret.id
  secret_string = jsonencode({
    key = random_password.internal_api_secret.result
  })
}

# ============================================================================
# Firebase and Application Secrets
# ============================================================================
# These secrets enable deployment without terraform.tfvars access
# Once created by Terraform, they can be read via AWS CLI by team members

# Firebase web application configuration
resource "aws_secretsmanager_secret" "firebase_config" {
  name        = "${local.env_prefix}/firebase/config"
  description = "Firebase web application configuration for OptiNiSt"

  tags = {
    Name = "OptiNiSt Firebase Config"
  }
}

resource "aws_secretsmanager_secret_version" "firebase_config" {
  secret_id     = aws_secretsmanager_secret.firebase_config.id
  secret_string = var.firebase_config_json
}

# Firebase service account private key
resource "aws_secretsmanager_secret" "firebase_private_key" {
  name        = "${local.env_prefix}/firebase/private-key"
  description = "Firebase service account private key for OptiNiSt"

  tags = {
    Name = "OptiNiSt Firebase Private Key"
  }
}

resource "aws_secretsmanager_secret_version" "firebase_private_key" {
  secret_id     = aws_secretsmanager_secret.firebase_private_key.id
  secret_string = var.firebase_private_json
}

# Database credentials (consolidated for app_setup.sh)
resource "aws_secretsmanager_secret" "database_config" {
  name        = "${local.env_prefix}/database/config"
  description = "MySQL/MariaDB database configuration for OptiNiSt"

  tags = {
    Name = "OptiNiSt Database Config"
  }
}

resource "aws_secretsmanager_secret_version" "database_config" {
  secret_id = aws_secretsmanager_secret.database_config.id
  secret_string = jsonencode({
    username = var.mysql_user
    password = var.mysql_password
    database = var.mysql_database
  })
}

# Application configuration secrets
resource "aws_secretsmanager_secret" "app_config" {
  name        = "${local.env_prefix}/app/config"
  description = "OptiNiSt application configuration secrets"

  tags = {
    Name = "OptiNiSt App Config"
  }
}

resource "aws_secretsmanager_secret_version" "app_config" {
  secret_id = aws_secretsmanager_secret.app_config.id
  secret_string = jsonencode({
    secret_key         = var.optinist_secret_key
    routing_secret_key = var.routing_secret_key
    org_name           = var.optinist_org_name
    admin_name         = var.optinist_admin_name
    admin_email        = var.optinist_admin_email
    admin_uid          = var.optinist_admin_uid
  })
}

# Stripe configuration
resource "aws_secretsmanager_secret" "stripe_config" {
  name        = "${local.env_prefix}/stripe/config"
  description = "Stripe API keys and webhook secrets for OptiNiSt"

  tags = {
    Name = "OptiNiSt Stripe Config"
  }
}

resource "aws_secretsmanager_secret_version" "stripe_config" {
  secret_id = aws_secretsmanager_secret.stripe_config.id
  secret_string = jsonencode({
    secret_key     = var.stripe_secret_key
    webhook_secret = var.stripe_webhook_secret
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
  key_name = var.key_name != "" ? var.key_name : "${local.env_prefix}-cloud-${random_id.key_suffix.hex}"
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
    Name = "${local.env_prefix}-cloud-key"
  }
}

# Save private key to local file
resource "local_file" "private_key" {
  content         = tls_private_key.subscr_optinist_cloud_key.private_key_pem # Fixed reference
  filename        = "${path.module}/${local.env_prefix}-cloud-private-key.pem"
  file_permission = "0400"
}

# ============================================================================
# Outputs for Secrets Manager
# ============================================================================
# These outputs help team members find and access secrets via AWS CLI

output "secrets_manager_secret_names" {
  description = "Names of Secrets Manager secrets (use with AWS CLI: aws secretsmanager get-secret-value --secret-id <name>)"
  value = {
    firebase_config      = aws_secretsmanager_secret.firebase_config.name
    firebase_private_key = aws_secretsmanager_secret.firebase_private_key.name
    database_config      = aws_secretsmanager_secret.database_config.name
    app_config           = aws_secretsmanager_secret.app_config.name
    stripe_config        = aws_secretsmanager_secret.stripe_config.name
  }
}

output "secrets_manager_secret_arns" {
  description = "ARNs of all Secrets Manager secrets"
  value = {
    firebase_config      = aws_secretsmanager_secret.firebase_config.arn
    firebase_private_key = aws_secretsmanager_secret.firebase_private_key.arn
    database_config      = aws_secretsmanager_secret.database_config.arn
    app_config           = aws_secretsmanager_secret.app_config.arn
    stripe_config        = aws_secretsmanager_secret.stripe_config.arn
  }
}

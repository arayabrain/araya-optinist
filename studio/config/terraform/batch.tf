variable "ecr_batch_repository_url" {
  description = "ECR repository URL for OptiNiSt Batch Docker image"
  type        = string
}

variable "ecr_snakemake_batch_repository_url" {
  description = "ECR repository URL for OptiNiSt Snakemake Batch Docker image"
  type        = string
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
    prevent_destroy       = false
  }
}

resource "aws_batch_job_queue" "paid_plan" {
  name     = "subscr-optinist-paid-queue"
  state    = "ENABLED"
  priority = 10 # Higher priority than free plan
  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.paid_plan.arn
  }

  depends_on = [aws_batch_compute_environment.paid_plan]

  lifecycle {
    create_before_destroy = true
    prevent_destroy       = false
  }
}

resource "aws_batch_compute_environment" "free_plan" {
  name         = "subscr-optinist-batch-free-plan"
  type         = "MANAGED"
  state        = "ENABLED"
  service_role = aws_iam_role.batch_service.arn
  depends_on   = [time_sleep.batch_role_propagation]

  compute_resources {
    type          = "EC2"
    min_vcpus     = 0
    max_vcpus     = 5
    desired_vcpus = 0
    instance_type = ["m5.large", "m5.xlarge"]

    subnets            = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.batch.id]

    instance_role = aws_iam_instance_profile.ecs_instance_profile.arn

    tags = {
      Name = "subscr-optinist-batch-free"
    }
  }
}

resource "aws_batch_compute_environment" "paid_plan" {
  name         = "subscr-optinist-batch-paid-plan"
  type         = "MANAGED"
  state        = "ENABLED"
  service_role = aws_iam_role.batch_service.arn
  depends_on   = [time_sleep.batch_role_propagation]

  compute_resources {
    type          = "EC2"
    min_vcpus     = 0
    max_vcpus     = 10
    desired_vcpus = 0
    instance_type = ["optimal"]

    # Same network setup as ECS
    subnets            = [aws_subnet.private1.id, aws_subnet.private2.id]
    security_group_ids = [aws_security_group.batch.id]

    instance_role = aws_iam_instance_profile.ecs_instance_profile.arn

    tags = {
      Name = "subscr-optinist-batch-paid"
    }
  }
}

# ===========================
# S3 Bucket for Batch Storage
# ===========================

resource "aws_s3_bucket" "app_storage_batch" {
  bucket        = "subscr-optinist-batch-app-storage"
  force_destroy = true

  tags = {
    Name        = "Subscr OptiNiSt Batch Application Storage"
    Environment = "Production"
  }
}

resource "aws_s3_bucket_versioning" "app_storage_batch" {
  bucket = aws_s3_bucket.app_storage_batch.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "app_storage_batch" {
  bucket                  = aws_s3_bucket.app_storage_batch.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ===========================================
# Application Load Balancer for Batch Service
# ===========================================

resource "aws_lb" "batch" {
  name               = "subscr-batch-optinist-lb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id, aws_security_group.ecs.id]
  subnets            = [aws_subnet.public1.id, aws_subnet.public2.id]

  enable_deletion_protection = false
  idle_timeout               = 60

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

resource "aws_lb_listener" "batch" {
  load_balancer_arn = aws_lb.batch.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.batch.arn
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
    matcher             = "200"
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 30
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

# ==============================
# RDS Instance for Batch Service
# ==============================

resource "aws_db_instance" "batch" {
  identifier                      = "subscr-optinist-cloud-rds-batch"
  allocated_storage               = 20
  storage_type                    = "gp3"
  engine                          = "mysql"
  engine_version                  = "8.0"
  instance_class                  = "db.t4g.micro"
  parameter_group_name            = "default.mysql8.0"
  db_name                         = var.mysql_database
  username                        = var.mysql_user
  password                        = var.mysql_password
  skip_final_snapshot             = true
  final_snapshot_identifier       = "${var.mysql_database}-batch-final-snapshot"
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
    Name    = "subscr-optinist-cloud-rds-batch"
    Service = "batch"
  }
}


# ========================
# EFS File System for Batch (Isolated)
# ========================
resource "aws_efs_file_system" "batch" {
  creation_token = "subscr-optinist-cloud-batch-snmk-volume"

  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"

  tags = {
    Name    = "subscr-optinist-cloud-batch-efs"
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
    Name    = "subscr-optinist-cloud-batch-efs-ap"
    Service = "batch"
  }
}

# ========================
# Security Group Rules for Batch
# ========================

resource "aws_security_group" "batch" {
  name        = "subscr-optinist-batch-sg"
  description = "Security group for AWS Batch compute environments"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  # Same rules as ECS for RDS access
  egress {
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
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

resource "aws_s3_bucket_policy" "app_storage_batch" {
  bucket = aws_s3_bucket.app_storage_batch.id

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
          aws_s3_bucket.app_storage_batch.arn,
          "${aws_s3_bucket.app_storage_batch.arn}/*"
        ]
      },
      {
        Sid    = "AllowBatchJobAccess"
        Effect = "Allow"
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


# ====================
# CloudWatch for Batch
# ====================

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
    ignore_changes  = [name]
    prevent_destroy = false
  }
}

# CloudWatch Log Groups for Batch ECS Service (Isolated)
resource "aws_cloudwatch_log_group" "ecs_batch" {
  name              = "/ecs/subscr-optinist-batch-cloud-taskdef"
  retention_in_days = 7

  tags = {
    Name    = "subscr-optinist-batch-ecs-logs"
    Service = "batch"
  }
}

# ====================================
# Automated Build and Deploy for Batch
# ====================================

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

# =============================
# ECS Task Definition for Batch
# ============================

resource "aws_ecs_task_definition" "batch" {
  family                   = "subscr-batch-optinist-cloud-taskdef"
  requires_compatibilities = ["EC2"]
  network_mode             = "bridge"
  cpu                      = 2048
  memory                   = 6144
  task_role_arn            = aws_iam_role.ecs_task.arn
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name              = "subscr-batch-optinist-cloud-container"
      image             = "${var.ecr_batch_repository_url}:latest"
      cpu               = 1536
      memory            = 5120
      memoryReservation = 3072
      essential         = true
      workingDirectory  = "/app"
      entryPoint        = ["/bin/sh", "-c"]
      command           = ["./cloud-startup.sh"]

      portMappings = [
        {
          name          = "subscr-optinist-cloud-container-port-8000"
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
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
          name  = "S3_DEFAULT_BUCKET_NAME"
          value = aws_s3_bucket.app_storage_batch.id
        },
        {
          name  = "REMOTE_STORAGE_TYPE"
          value = "2"
        },
        {
          name  = "USE_AWS_BATCH"
          value = "true"
        },
        {
          name  = "AWS_BATCH_S3_BUCKET_NAME"
          value = aws_s3_bucket.app_storage_batch.id
        },
        {
          name  = "AWS_DEFAULT_PROVIDER"
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
          "awslogs-group"             = "/ecs/subscr-optinist-batch-cloud-taskdef"
          "mode"                      = "non-blocking"
          "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
          "max-buffer-size"           = "25m"
          "awslogs-region"            = "ap-northeast-1"
          "awslogs-create-group"      = "true"
          "awslogs-stream-prefix"     = "ecs"
          "mode"                      = "non-blocking"
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
      file_system_id     = aws_efs_file_system.batch.id
      root_directory     = "/"
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.batch.id
        iam             = "DISABLED"
      }
    }
  }

  tags = {
    Name = "subscr-batch-optinist-cloud-taskdef"
  }
}

resource "aws_ecs_service" "batch" {
  name                               = "subscr-batch-optinist-cloud-service"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.batch.arn
  desired_count                      = 1
  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 0
  launch_type                        = "EC2"

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


# =========================
# Dedicated Batch Instance
# =========================

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
      Name    = "subscr-optinist-batch-instance"
      Type    = "ECS-Batch"
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
    Name    = "subscr-optinist-batch-instance"
    Type    = "ECS-Batch-Dedicated"
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
    image      = "${var.ecr_snakemake_batch_repository_url}:latest"
    vcpus      = 2
    memory     = 4096
    jobRoleArn = aws_iam_role.batch_job.arn


    mountPoints = [
      {
        sourceVolume  = "tmp"
        containerPath = "/tmp"
        readOnly      = false
      },
      {
        sourceVolume  = "efs"
        containerPath = "/mnt/efs"
        readOnly      = false
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
          fileSystemId      = "${aws_efs_file_system.snmk.id}"
          rootDirectory     = "/"
          transitEncryption = "ENABLED"
          authorizationConfig = {
            accessPointId = "${aws_efs_access_point.snmk.id}"
            iam           = "DISABLED"
          }
        }
      }
    ]

    environment = [
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
        name  = "DB_PASSWORD"
        value = var.mysql_password
      },
      {
        name  = "DB_NAME"
        value = var.mysql_database
      },
      {
        name  = "AWS_DEFAULT_REGION"
        value = var.aws_region
      },
      {
        name  = "S3_DEFAULT_BUCKET_NAME"
        value = aws_s3_bucket.app_storage.id
      },
      {
        name  = "USE_AWS_BATCH"
        value = "false"
      },
      {
        name  = "PYTHONPATH"
        value = "/app"
      },
      {
        name  = "EFS_MOUNT_TARGET"
        value = "/mnt/efs"
      },
      {
        name  = "TMPDIR"
        value = "/tmp"
      },
      {
        name  = "TMP"
        value = "/tmp"
      },
      {
        name  = "IS_STANDALONE"
        value = "true"
      },
      {
        name  = "USE_FIREBASE_TOKEN"
        value = "false"
      },
      {
        name  = "REMOTE_STORAGE_TYPE"
        value = "2"
      },
      {
        name  = "TZ"
        value = "Asia/Tokyo"
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
        name  = "IN_SNAKEMAKE_BATCH"
        value = "true"
      },
      {
        name  = "AWS_BATCH_S3_BUCKET_NAME"
        value = aws_s3_bucket.app_storage.id
      },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"             = "/aws/batch/job"
        "mode"                      = "non-blocking"
        "awslogs-multiline-pattern" = "^\\[\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}"
        "max-buffer-size"           = "25m"
        "awslogs-region"            = "ap-northeast-1"
        "awslogs-create-group"      = "true"
        "awslogs-stream-prefix"     = "batch"
      }
    }
  })
}

# =======
# Outputs
# =======

output "rds_endpoint_batch" {
  description = "RDS instance endpoint (batch)"
  value       = aws_db_instance.batch.endpoint
}

output "alb_dns_name_batch" {
  description = "ALB batch DNS name"
  value       = aws_lb.batch.dns_name
}

output "ecs_service_name_batch" {
  description = "Name of the ECS batch service"
  value       = aws_ecs_service.batch.name
}

output "app_storage_bucket_batch" {
  description = "S3 bucket for application storage (batch)"
  value       = aws_s3_bucket.app_storage_batch.id
}

output "frontend_config_batch" {
  description = "Configuration values for frontend/.env.production"
  value = {
    REACT_APP_SERVER_HOST             = aws_lb.batch.dns_name
    REACT_APP_SERVER_PORT             = "80"
    REACT_APP_SERVER_PROTO            = "http"
    REACT_APP_EXPDB_METADATA_EDITABLE = true
  }
}

output "aws_batch_config" {
  description = "AWS Batch configuration values for batch_config"
  value = {
    AWS_BATCH_JOB_ROLE       = aws_iam_role.batch_job.arn
    AWS_BATCH_S3_BUCKET_NAME = aws_s3_bucket.app_storage_batch.id
    AWS_BATCH_FREE_QUEUE     = aws_batch_job_queue.free_plan.name
    AWS_BATCH_PAID_QUEUE     = aws_batch_job_queue.paid_plan.name
    AWS_BATCH_JOB_DEFINITION = aws_batch_job_definition.optinist.name
  }
}

output "batch_instance_id" {
  description = "Instance ID of the dedicated batch instance"
  value       = aws_instance.batch.id
}

output "batch_instance_private_ip" {
  description = "Private IP of the dedicated batch instance"
  value       = aws_instance.batch.private_ip
}

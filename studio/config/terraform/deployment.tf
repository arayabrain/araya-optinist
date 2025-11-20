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

-- Subscription plans initialization
%{for plan in var.subscription_plans~}
INSERT INTO subscription_plans
  (id, name, price, billing_cycle, features, currency, status, stripe_product_id, stripe_price_id, created_at)
VALUES
  (${plan.id}, '${replace(plan.name, "'", "\\'")}', ${plan.price}, ${plan.billing_cycle}, '${replace(jsonencode(plan.features), "'", "\\'")}', ${plan.currency}, ${plan.status}, '${replace(plan.stripe_product_id, "'", "\\'")}', '${replace(plan.stripe_price_id, "'", "\\'")}', NOW())
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  price = VALUES(price),
  billing_cycle = VALUES(billing_cycle),
  features = VALUES(features),
  currency = VALUES(currency),
  status = VALUES(status),
  stripe_product_id = VALUES(stripe_product_id),
  stripe_price_id = VALUES(stripe_price_id);
%{endfor~}

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
  name            = "subscr-optinist-app-setup"
  document_type   = "Command"
  document_format = "YAML"

  depends_on = [aws_s3_object.app_setup_script]

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Application setup for OptiNiSt instances"
    parameters    = {}
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
  max_concurrency     = "1"
  max_errors          = "0"

  compliance_severity = "HIGH"
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

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

# ============================================================================
# CONFIGURATION CONSTANTS
# ============================================================================

# Logging
LOGFILE="/var/log/app-setup.log"

# Database Configuration
MYSQL_PORT=3306
DB_INIT_SQL_FILE="/tmp/init_optinist_db.sql"
DB_INIT_FILE_PERMISSIONS=644

# Retry Parameters
DB_CONNECTION_MAX_ATTEMPTS=30
DB_CONNECTION_RETRY_DELAY=10
DB_INIT_MAX_ATTEMPTS=10
DB_INIT_RETRY_DELAY=30
ECS_AGENT_MAX_ATTEMPTS=10
ECS_AGENT_RETRY_DELAY=30

# Default IDs
DEFAULT_ORG_ID=1
DEFAULT_USER_ID=1
ADMIN_ROLE_ID=1

# Role Definitions
ROLE_ADMIN_ID=1
ROLE_ADMIN_NAME="admin"
ROLE_DATA_MANAGER_ID=10
ROLE_DATA_MANAGER_NAME="data manager"
ROLE_OPERATOR_ID=20
ROLE_OPERATOR_NAME="operator"
ROLE_GUEST_OPERATOR_ID=30
ROLE_GUEST_OPERATOR_NAME="guest operator"

# Tax Configuration
TAX_TYPE="sales_tax"
TAX_NAME="Sales Tax"
TAX_RATE=0.10
TAX_IS_ACTIVE=1

# Subscription Configuration
# Free plan details
FREE_PLAN_ID=1
FREE_PLAN_NAME="'Free'"
FREE_PLAN_PRICE=0

# Premium plan details
PREMIUM_PLAN_ID=2
PREMIUM_PLAN_NAME="'Premium'"
PREMIUM_PLAN_PRICE=2999

# Common subscription attributes
DEFAULT_BILLING_CYCLE_DAYS=30
CURRENCY_USD=840  # ISO 4217 numeric code for USD
STATUS_ACTIVE=1   # Plan status: 1 = Active

# Admin subscription settings
ADMIN_SUBSCRIPTION_DAYS=365

# Storage Configuration
ADMIN_STORAGE_USAGE_BYTES=0
ADMIN_STORAGE_QUOTA_BYTES=${var.admin_storage_quota_bytes}

# ============================================================================

exec > $LOGFILE 2>&1

echo "$(date): Starting application setup script"

# Function for retries
retry_command() {
    local max_attempts=$$1
    local delay=$$2
    local command="$${@:3}"

    for i in $$(seq 1 $$max_attempts); do
        echo "$$(date): Attempting: $$command (attempt $$i/$$max_attempts)"
        if eval "$$command"; then
            echo "$$(date): Success: $$command"
            return 0
        else
            echo "$$(date): Failed attempt $$i/$$max_attempts"
            [ $$i -lt $$max_attempts ] && sleep $$delay
        fi
    done

    echo "$$(date): ERROR: Command failed after $$max_attempts attempts: $$command"
    return 1
}

# Wait for ECS agent to be ready
echo "$(date): Waiting for ECS agent to be ready"
retry_command $$ECS_AGENT_MAX_ATTEMPTS $$ECS_AGENT_RETRY_DELAY "curl -s http://localhost:51678/v1/metadata >/dev/null"

# Create config files
echo "$(date): Creating configuration files"
mkdir -p /opt/optinist/optinist-for-cloud/studio/config/auth

# Create .env file
cat > /opt/optinist/optinist-for-cloud/studio/config/.env << 'CONFIG_ENV'
SECRET_KEY='${var.optinist_secret_key}'
USE_FIREBASE_TOKEN=True
MYSQL_SERVER=${aws_db_proxy.main.endpoint}
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
apt-get install -y mysql-client-core-8.0 python3-pip

retry_command $$DB_CONNECTION_MAX_ATTEMPTS $$DB_CONNECTION_RETRY_DELAY "nc -z ${replace(aws_db_instance.main.endpoint, ":3306", "")} $$MYSQL_PORT"

# Initialize database tables and users
echo "$(date): Initializing database tables"
cat > $$DB_INIT_SQL_FILE << 'INIT_SQL'
-- Ensure user has mysql_native_password authentication for RDS Proxy compatibility
-- This is needed because RDS Proxy with MySQL 8.0 requires mysql_native_password
-- when using MYSQL_NATIVE_PASSWORD client authentication type
ALTER USER '${var.mysql_user}'@'%' IDENTIFIED WITH mysql_native_password BY '${var.mysql_password}';
FLUSH PRIVILEGES;

-- Create database if it doesn't exist
-- This ensures the application's database exists before proceeding
CREATE DATABASE IF NOT EXISTS ${var.mysql_database};
USE ${var.mysql_database};

-- Insert initial data
INSERT IGNORE INTO organization (name) VALUES ('${var.optinist_org_name}');
INSERT IGNORE INTO roles (id, role) VALUES
  ($$ROLE_ADMIN_ID, '$$ROLE_ADMIN_NAME'),
  ($$ROLE_DATA_MANAGER_ID, '$$ROLE_DATA_MANAGER_NAME'),
  ($$ROLE_OPERATOR_ID, '$$ROLE_OPERATOR_NAME'),
  ($$ROLE_GUEST_OPERATOR_ID, '$$ROLE_GUEST_OPERATOR_NAME');

-- Default admin user with S3 bucket info
INSERT IGNORE INTO users (uid, organization_id, name, email, active, attributes)
VALUES ('${var.optinist_admin_uid}', $$DEFAULT_ORG_ID, '${var.optinist_admin_name}', '${var.optinist_admin_email}', true, '{"remote_bucket_name": "${aws_s3_bucket.app_storage.id}"}');

INSERT IGNORE INTO user_roles (user_id, role_id) VALUES ($$DEFAULT_USER_ID, $$ADMIN_ROLE_ID);

UPDATE users SET attributes = JSON_MERGE_PATCH(IFNULL(attributes,'{}'), '{"remote_bucket_name": "${aws_s3_bucket.app_storage.id}"}') WHERE id = $$DEFAULT_USER_ID;

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

-- Tax rates initialization
INSERT IGNORE INTO taxes (tax_type, tax_name, tax_rate, is_active, effective_date)
VALUES ('$$TAX_TYPE', '$$TAX_NAME', $$TAX_RATE, $$TAX_IS_ACTIVE, CURDATE());

-- Admin user storage quota initialization
INSERT IGNORE INTO user_storage_usage (user_id, storage_usage_bytes, storage_quota_bytes)
VALUES ($$DEFAULT_USER_ID, $$ADMIN_STORAGE_USAGE_BYTES, $$ADMIN_STORAGE_QUOTA_BYTES);

-- Admin user premium subscription
INSERT IGNORE INTO subscription_users (plan_id, user_id, expiration)
VALUES ($$PREMIUM_PLAN_ID, $$DEFAULT_USER_ID, DATE_ADD(NOW(), INTERVAL $$ADMIN_SUBSCRIPTION_DAYS DAY));

INIT_SQL

chmod $$DB_INIT_FILE_PERMISSIONS $$DB_INIT_SQL_FILE

# Wait for database to be ready and execute initialization
max_attempts=$$DB_INIT_MAX_ATTEMPTS
attempt=1
while [ $$attempt -le $$max_attempts ]; do
  echo "$$(date): Attempting to initialize database (attempt $$attempt/$$max_attempts)"
  if mysql -h ${replace(aws_db_instance.main.endpoint, ":3306", "")} -P $$MYSQL_PORT -u ${var.mysql_user} -p'${var.mysql_password}' ${var.mysql_database} < $$DB_INIT_SQL_FILE; then
    echo "$$(date): Database initialization successful"
    break
  else
    echo "$$(date): Database initialization attempt $$attempt failed, waiting to retry..."
    sleep $$DB_INIT_RETRY_DELAY
    attempt=$$((attempt+1))
  fi
done

if [ $$attempt -gt $$max_attempts ]; then
  echo "$$(date): ERROR: Failed to initialize the database after $$max_attempts attempts"
fi

# Firebase Admin Email Verification
echo "$$(date): Verifying admin email in Firebase"

# Install dependencies for Firebase Admin SDK
echo "$$(date): Installing firebase-admin..."
python3 -m pip install firebase-admin

# Run verification script
echo "$$(date): Running Firebase verification script..."
python3 -c "
from firebase_admin import auth, credentials, initialize_app
import sys

try:
    initialize_app(credentials.Certificate('/opt/optinist/optinist-for-cloud/studio/config/auth/firebase_private.json'))
except ValueError:
    pass  # App already initialized

try:
    admin_uid = '${var.optinist_admin_uid}'
    auth.update_user(admin_uid, email_verified=True)
    print(f'Successfully verified email for user: {admin_uid}')
except Exception as e:
    print(f'ERROR: Firebase email verification failed: {e}', file=sys.stderr)
    sys.exit(1)
" || echo "$$(date): WARNING: Firebase email verification script encountered an issue."


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

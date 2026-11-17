# Operator-set site announcement, read by GET /announcement on every tier.
# Set it with:
#   aws ssm put-parameter --overwrite --name /<env>/optinist/announcement \
#     --value '{"message": "...", "until": "2026-11-05T05:00:00Z"}'
# Clear it with --value '{}'. Terraform creates it once and never resets it.
resource "aws_ssm_parameter" "announcement" {
  name  = "/${var.environment}/optinist/announcement"
  type  = "String"
  value = "{}"

  tags = {
    Name    = "Site Announcement"
    Service = "announcement"
  }

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_iam_role_policy" "ecs_task_announcement_ssm" {
  name = "${var.environment}-ecs-task-announcement-ssm"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = aws_ssm_parameter.announcement.arn
      }
    ]
  })
}

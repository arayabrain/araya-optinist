# Plan-time guards on the workflow tiers' sizes (TIER_RESIZE_RUNBOOK.md,
# "What the Plan Checks"). Every provider is mocked: no AWS call, no
# credentials, no backend, no state. From infrastructure/terraform:
#
#   export TF_DATA_DIR=/tmp/optinist-tftest   # keeps .terraform/ out of the tree
#   terraform init -backend=false -lockfile=readonly
#   terraform test
#
# The data sources the guards read are overridden to today's shapes (t3.large,
# a 30 GiB root snapshot); each run changes one input. A rule's message is not
# checked here: expect_failures passes on any failure of the named object.

mock_provider "aws" {}
mock_provider "archive" {}
mock_provider "external" {}
mock_provider "local" {}
mock_provider "null" {}
mock_provider "random" {}
mock_provider "tls" {}

variables {
  environment           = "development"
  enable_custom_domain  = false
  mysql_database        = "studio"
  key_name              = "test"
  git_repo              = "test"
  git_branch            = "test"
  monthly_budget_usd    = 100
  s3_user_bucket_secret = "test"
  optinist_org_name     = "test"
  optinist_admin_name   = "test"
  optinist_admin_email  = "test@example.com"
  optinist_admin_uid    = "test"
  optinist_secret_key   = "test"
  stripe_secret_key     = "test"
  stripe_webhook_secret = "test"
  routing_secret_key    = "test"
  firebase_config_json  = "{}"
  firebase_private_json = "{}"
}

# Read by tags and a deployment's revision; the mock returns an empty map
override_data {
  target = data.external.tf_build_info
  values = {
    result = { git_commit = "0000000", git_branch = "test", git_tag = "" }
  }
}

# The NAT AMI freshness check compares creation dates
override_data {
  target = data.aws_ami.nat_pinned
  values = { creation_date = "2026-01-01T00:00:00.000Z" }
}

override_data {
  target = data.aws_ami.nat_ami_latest
  values = { creation_date = "2026-01-01T00:00:00.000Z" }
}

override_data {
  target = data.aws_ami.effective
  values = {
    id               = "ami-00000000000000000"
    root_device_name = "/dev/xvda"
    block_device_mappings = [{
      device_name  = "/dev/xvda"
      ebs          = { volume_size = "30", volume_type = "gp3" }
      no_device    = ""
      virtual_name = ""
    }]
  }
}

override_data {
  target = data.aws_ec2_instance_type.free
  values = { memory_size = 8192, default_vcpus = 2 }
}

override_data {
  target = data.aws_ec2_instance_type.premium
  values = { memory_size = 8192, default_vcpus = 2 }
}

# An unset IOPS or throughput is unknown until apply (the provider computes
# it), so only a set value can be asserted at plan
run "defaults_plan" {
  command = plan
}

# The runbook's phase 2 sets, at their types (16 GiB; 2 and 4 vCPUs)
run "free_prepared_set_plans_at_its_type" {
  command = plan

  variables {
    free_instance_type = "r7i.large"
    free_tier_sizing = {
      task_memory_mib              = 14848
      container_memory_mib         = 14336
      root_volume_iops             = 3000
      root_volume_throughput_mibps = 125
      swap_volume_iops             = 3000
      swap_volume_throughput_mibps = 125
    }
  }

  override_data {
    target = data.aws_ec2_instance_type.free
    values = { memory_size = 16384, default_vcpus = 2 }
  }

  assert {
    condition = alltrue([
      for device in aws_launch_template.ecs.block_device_mappings :
      device.ebs[0].iops == 3000 && device.ebs[0].throughput == 125
    ])
    error_message = "The free template does not carry the set's IOPS and throughput."
  }
}

run "premium_prepared_set_plans_at_its_type" {
  command = plan

  variables {
    premium_instance_type = "m7i.xlarge"
    premium_tier_sizing = {
      task_cpu                     = 4096
      container_cpu                = 3072
      task_memory_mib              = 14848
      container_memory_mib         = 14336
      root_volume_iops             = 3000
      root_volume_throughput_mibps = 150
      swap_volume_iops             = 3000
      swap_volume_throughput_mibps = 150
    }
  }

  override_data {
    target = data.aws_ec2_instance_type.premium
    values = { memory_size = 16384, default_vcpus = 4 }
  }
}

# Phase 2 before phase 1: the next size's task on today's type
run "free_task_above_todays_type_fails" {
  command = plan

  variables {
    free_tier_sizing = { task_memory_mib = 14848, container_memory_mib = 14336 }
  }

  expect_failures = [aws_ecs_task_definition.autoscaling]
}

# 15/16 of 8,192 MiB is 7,680
run "premium_task_memory_above_fifteen_sixteenths_fails" {
  command = plan

  variables {
    premium_tier_sizing = { task_memory_mib = 7681 }
  }

  expect_failures = [aws_ecs_task_definition.premium]
}

run "premium_task_memory_at_fifteen_sixteenths_plans" {
  command = plan

  variables {
    premium_tier_sizing = { task_memory_mib = 7680 }
  }
}

run "premium_task_cpu_above_the_vcpus_fails" {
  command = plan

  variables {
    premium_tier_sizing = { task_cpu = 4096, container_cpu = 3072 }
  }

  expect_failures = [aws_ecs_task_definition.premium]
}

# Between the two tiers' default root volumes: only the premium one is below it
run "ami_root_snapshot_above_the_root_volume_fails" {
  command = plan

  override_data {
    target = data.aws_ami.effective
    values = {
      id               = "ami-00000000000000000"
      root_device_name = "/dev/xvda"
      block_device_mappings = [{
        device_name  = "/dev/xvda"
        ebs          = { volume_size = "70", volume_type = "gp3" }
        no_device    = ""
        virtual_name = ""
      }]
    }
  }

  expect_failures = [aws_launch_template.premium]
}

run "ami_without_an_ebs_root_fails_at_the_lookup" {
  command = plan

  override_data {
    target = data.aws_ami.effective
    values = {
      id               = "ami-00000000000000000"
      root_device_name = "/dev/xvda"
      block_device_mappings = [{
        device_name  = "/dev/sdb"
        ebs          = { volume_size = "30", volume_type = "gp3" }
        no_device    = ""
        virtual_name = ""
      }]
    }
  }

  expect_failures = [data.aws_ami.effective]
}

# gp3: throughput at most IOPS / 4, with 3,000 IOPS when unset
run "swap_throughput_above_the_baseline_iops_fails" {
  command = plan

  variables {
    free_tier_sizing = { swap_volume_throughput_mibps = 751 }
  }

  expect_failures = [aws_launch_template.ecs]
}

run "swap_throughput_with_enough_iops_plans" {
  command = plan

  variables {
    free_tier_sizing = { swap_volume_throughput_mibps = 751, swap_volume_iops = 3004 }
  }
}

# gp3: at most 500 IOPS per GiB; the default swap volume is 32 GiB
run "swap_iops_above_500_per_gib_fails" {
  command = plan

  variables {
    free_tier_sizing = { swap_volume_iops = 16001 }
  }

  expect_failures = [aws_launch_template.ecs]
}

run "swap_iops_at_500_per_gib_plans" {
  command = plan

  variables {
    free_tier_sizing = { swap_volume_iops = 16000 }
  }
}

run "misspelled_field_fails" {
  command = plan

  variables {
    premium_tier_sizing = { root_volume_iop = 4000 }
  }

  expect_failures = [var.premium_tier_sizing]
}

run "iops_below_gp3_baseline_fails" {
  command = plan

  variables {
    premium_tier_sizing = { root_volume_iops = 2999 }
  }

  expect_failures = [var.premium_tier_sizing]
}

run "throughput_above_gp3_maximum_fails" {
  command = plan

  variables {
    premium_tier_sizing = { root_volume_throughput_mibps = 2001, root_volume_iops = 8004 }
  }

  expect_failures = [var.premium_tier_sizing]
}

run "swappiness_above_100_fails" {
  command = plan

  variables {
    free_tier_sizing = { container_swappiness = 101 }
  }

  expect_failures = [var.free_tier_sizing]
}

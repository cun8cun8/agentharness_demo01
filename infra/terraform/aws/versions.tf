terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge(var.tags, {
      Project   = "ResearchForge"
      ManagedBy = "Terraform"
    })
  }
}

provider "aws" {
  alias  = "dr"
  region = coalesce(var.dr_region, var.region)

  default_tags {
    tags = merge(var.tags, {
      Project   = "ResearchForge"
      ManagedBy = "Terraform"
      Role      = "DisasterRecovery"
    })
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

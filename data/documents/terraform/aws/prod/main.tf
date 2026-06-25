# Production AWS Infrastructure — CIVITAS Platform
terraform {
  required_version = ">= 1.8.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket = "civitas-terraform-state-prod"
    key    = "prod/terraform.tfstate"
    region = "eu-west-3"
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Environment = "production"
      Project     = "civitas"
      ManagedBy   = "terraform"
    }
  }
}

module "vpc" {
  source = "../../modules/vpc"
  environment   = "prod"
  cidr_block    = var.vpc_cidr
  az_count      = 3
}

module "rds" {
  source = "../../modules/rds"
  environment      = "prod"
  instance_class   = "db.t3.medium"
  db_name          = "civitas_knowledge"
  db_username      = var.db_username
  db_password      = var.db_password
  subnet_ids       = module.vpc.private_subnet_ids
  security_group_ids = [module.vpc.db_security_group_id]
  multi_az         = true
  backup_retention = 30
}

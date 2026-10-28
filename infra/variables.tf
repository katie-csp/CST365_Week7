variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-west-2"
}

variable "project" {
  description = "Project name used in resource names and tags."
  type        = string
  default     = "healthqueue"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.project))
    error_message = "project must be 3-21 lowercase letters, numbers or hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "owner" {
  description = "Team or person responsible for these resources (used in tags)."
  type        = string
  default     = "cst365-student"
}

variable "vpc_cidr" {
  description = "IPv4 CIDR block for the VPC."
  type        = string
  default     = "10.20.0.0/16"

  validation {
    # cidrnetmask() only accepts IPv4; AWS allows VPC sizes /16 to /28, and /24 leaves room for /28 subnets.
    condition     = can(cidrnetmask(var.vpc_cidr)) && try(tonumber(split("/", var.vpc_cidr)[1]) >= 16 && tonumber(split("/", var.vpc_cidr)[1]) <= 24, false)
    error_message = "vpc_cidr must be a valid IPv4 CIDR block between /16 and /24."
  }
}

variable "az_count" {
  description = "Number of Availability Zones (one private subnet per AZ)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3 for high availability."
  }
}

variable "artifact_noncurrent_version_days" {
  description = "Days to keep old versions of objects in the artifacts bucket."
  type        = number
  default     = 30

  validation {
    condition     = var.artifact_noncurrent_version_days >= 1
    error_message = "artifact_noncurrent_version_days must be at least 1."
  }
}

variable "extra_tags" {
  description = "Additional tags merged into the default tags."
  type        = map(string)
  default     = {}
}

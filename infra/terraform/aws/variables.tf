variable "region" {
  type        = string
  description = "AWS region for all resources."
}

variable "name" {
  type        = string
  description = "Short globally distinguishable deployment name."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "name must be 3-31 lowercase letters, numbers, or hyphens and start with a letter."
  }
}

variable "kubernetes_version" {
  type        = string
  description = "EKS Kubernetes version supported by the selected AWS region."
  default     = "1.31"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR for the dedicated ResearchForge VPC."
  default     = "10.80.0.0/16"
}

variable "availability_zone_count" {
  type        = number
  description = "Number of availability zones for EKS and EFS."
  default     = 3

  validation {
    condition     = var.availability_zone_count >= 2 && var.availability_zone_count <= 3
    error_message = "availability_zone_count must be 2 or 3."
  }
}

variable "artifact_bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name for ResearchForge artifacts."
}

variable "database_backup_bucket_name" {
  type        = string
  description = "Optional globally unique S3 bucket for CNPG base backups and WAL. Defaults to <name>-database-backups."
  default     = null
  nullable    = true
}

variable "enable_cross_region_backup_replication" {
  type        = bool
  description = "Replicate CNPG backup objects to a dedicated S3 bucket in dr_region."
  default     = false
}

variable "dr_region" {
  type        = string
  description = "AWS region for the disaster-recovery backup bucket when replication is enabled."
  default     = null
  nullable    = true
}

variable "dr_backup_bucket_name" {
  type        = string
  description = "Globally unique S3 destination bucket name for cross-region CNPG backup replication."
  default     = null
  nullable    = true
}

variable "dr_artifact_bucket_name" {
  type        = string
  description = "Globally unique S3 destination bucket for cross-region artifact replication."
  default     = null
  nullable    = true
}

variable "enable_dns_failover" {
  type        = bool
  description = "Create Route 53 primary/secondary HTTPS health checks and CNAME failover records."
  default     = false
}

variable "route53_zone_id" {
  type        = string
  description = "Hosted zone ID that owns dns_record_name when DNS failover is enabled."
  default     = null
  nullable    = true
}

variable "dns_record_name" {
  type        = string
  description = "Non-apex CNAME record name used for application failover, for example app.example.com."
  default     = null
  nullable    = true
}

variable "primary_endpoint_fqdn" {
  type        = string
  description = "Primary HTTPS ingress FQDN, without protocol, used by Route 53 health checks."
  default     = null
  nullable    = true
}

variable "dr_endpoint_fqdn" {
  type        = string
  description = "Standby HTTPS ingress FQDN, without protocol, used by Route 53 health checks."
  default     = null
  nullable    = true
}

variable "cluster_endpoint_public_access" {
  type        = bool
  description = "Whether EKS API is reachable from approved public CIDRs. Keep false for private runner access."
  default     = false
}

variable "cluster_public_access_cidrs" {
  type        = list(string)
  description = "Approved CIDRs for EKS API when public access is enabled."
  default     = []
}

variable "node_instance_types" {
  type        = list(string)
  description = "On-demand EKS node instance type fallback list."
  default     = ["m6i.large"]
}

variable "node_min_size" {
  type    = number
  default = 3
}

variable "node_desired_size" {
  type    = number
  default = 3
}

variable "node_max_size" {
  type    = number
  default = 12
}

variable "gpu_enabled" {
  type        = bool
  description = "Create a dedicated tainted NVIDIA GPU EKS node group for training jobs."
  default     = false
}

variable "gpu_node_instance_types" {
  type        = list(string)
  description = "NVIDIA-capable EC2 instance types for the GPU node group."
  default     = ["g5.xlarge"]
}

variable "gpu_node_min_size" {
  type    = number
  default = 0
}

variable "gpu_node_desired_size" {
  type    = number
  default = 0
}

variable "gpu_node_max_size" {
  type    = number
  default = 4
}

variable "create_managed_redis" {
  type        = bool
  description = "Create a Multi-AZ ElastiCache Redis replication group. Otherwise bring a managed Redis endpoint."
  default     = false
}

variable "redis_auth_token" {
  type        = string
  description = "Redis AUTH token when create_managed_redis=true. This is sensitive and is stored in Terraform state."
  default     = null
  sensitive   = true

  validation {
    condition     = !var.create_managed_redis || (var.redis_auth_token != null && length(var.redis_auth_token) >= 16)
    error_message = "create_managed_redis requires a redis_auth_token of at least 16 characters."
  }
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

variable "deletion_protection" {
  type        = bool
  description = "Enable deletion protection for stateful AWS services after first successful deployment."
  default     = true
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to all supported resources."
  default     = {}
}

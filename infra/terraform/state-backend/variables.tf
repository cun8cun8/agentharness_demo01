variable "region" {
  type        = string
  description = "AWS region for the Terraform state bucket and lock table."
}

variable "bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name for encrypted Terraform state."
}

variable "lock_table_name" {
  type        = string
  description = "DynamoDB table name used by the S3 backend state lock."
}

variable "tags" {
  type        = map(string)
  description = "Additional tags applied to state backend resources."
  default     = {}
}

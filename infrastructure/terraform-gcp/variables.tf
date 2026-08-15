variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone for Compute Engine instances"
  type        = string
  default     = "us-central1-a"
}

variable "environment" {
  description = "Environment name (e.g. prod, staging)"
  type        = string
  default     = "prod"
}

variable "app_image" {
  description = "Container image for the FastAPI Cloud Run service"
  type        = string
  default     = ""
}

variable "app_cpu" {
  description = "Cloud Run container CPU (millicores)"
  type        = string
  default     = "1"
}

variable "app_memory" {
  description = "Cloud Run container memory"
  type        = string
  default     = "2Gi"
}

variable "app_min_instances" {
  description = "Minimum Cloud Run instances"
  type        = number
  default     = 1
}

variable "app_max_instances" {
  description = "Maximum Cloud Run instances"
  type        = number
  default     = 10
}

variable "vpc_cidr" {
  description = "CIDR for the self-hosted VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "db_password" {
  description = "PostgreSQL superuser password"
  type        = string
  sensitive   = true
}

variable "db_machine_type" {
  description = "Compute Engine machine type for PostgreSQL"
  type        = string
  default     = "n2-standard-2"
}

variable "redis_machine_type" {
  description = "Compute Engine machine type for Redis"
  type        = string
  default     = "n2-standard-2"
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID for R2"
  type        = string
  default     = ""
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token with R2 edit permissions"
  type        = string
  sensitive   = true
  default     = ""
}

variable "r2_bucket_name" {
  description = "Globally unique R2 bucket name for uploads"
  type        = string
  default     = "ai-kb-uploads"
}

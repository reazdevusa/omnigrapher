variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project prefix for resource names"
  type        = string
  default     = "ai-kb"
}

variable "environment" {
  description = "Environment name (e.g. staging, production)"
  type        = string
  default     = "prod"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "db_username" {
  description = "PostgreSQL master username"
  type        = string
  default     = "kb_admin"
}

variable "db_password" {
  description = "PostgreSQL master password"
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
  default     = "cache.t3.micro"
}

variable "app_container_image" {
  description = "Docker image URI for the FastAPI app (ECR or external)"
  type        = string
  default     = ""
}

variable "app_cpu" {
  description = "Fargate task CPU units"
  type        = number
  default     = 512
}

variable "app_memory" {
  description = "Fargate task memory (MiB)"
  type        = number
  default     = 1024
}

variable "app_count" {
  description = "Number of Fargate tasks to run"
  type        = number
  default     = 2
}

variable "common_tags" {
  description = "Tags applied to all resources"
  type        = map(string)
  default = {
    Project     = "ai-knowledge-base"
    ManagedBy   = "terraform"
  }
}

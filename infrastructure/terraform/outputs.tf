output "alb_dns_name" {
  description = "Public DNS name of the application load balancer"
  value       = aws_lb.main.dns_name
}

output "s3_bucket_name" {
  description = "S3 bucket for uploaded documents"
  value       = aws_s3_bucket.uploads.id
}

output "db_endpoint" {
  description = "PostgreSQL endpoint"
  value       = aws_db_instance.main.address
}

output "redis_endpoint" {
  description = "Redis endpoint"
  value       = try(aws_elasticache_cluster.main.cache_nodes[0].address, "")
}

output "ecr_repository_url" {
  description = "URL of the ECR repository for the FastAPI app"
  value       = aws_ecr_repository.app.repository_url
}

output "cloud_run_url" {
  description = "URL of the deployed FastAPI Cloud Run service"
  value       = google_cloud_run_v2_service.app.uri
}

output "postgres_private_ip" {
  description = "Private IP of the self-hosted PostgreSQL instance"
  value       = google_compute_instance.postgres.network_interface[0].network_ip
}

output "redis_private_ip" {
  description = "Private IP of the self-hosted Redis instance"
  value       = google_compute_instance.redis.network_interface[0].network_ip
}

output "artifact_registry_repository" {
  description = "Artifact Registry repository URI for the FastAPI image"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"
}

output "r2_bucket_name" {
  description = "Cloudflare R2 bucket name for uploads"
  value       = var.r2_bucket_name
}

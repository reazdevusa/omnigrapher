terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
  required_version = ">= 1.5.0"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token != "" ? var.cloudflare_api_token : null
}

locals {
  name_prefix = "${var.project_id}-${var.environment}"
}

# -----------------------------------------------------------------------------
# Enable GCP APIs
# -----------------------------------------------------------------------------
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "run.googleapis.com",
    "vpcaccess.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# -----------------------------------------------------------------------------
# VPC + subnets
# -----------------------------------------------------------------------------
resource "google_compute_network" "vpc" {
  name                    = "${local.name_prefix}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "public" {
  name          = "${local.name_prefix}-public"
  network       = google_compute_network.vpc.id
  ip_cidr_range = "10.0.0.0/24"
  region        = var.region
  private_ip_google_access = true
}

resource "google_compute_subnetwork" "private" {
  name          = "${local.name_prefix}-private"
  network       = google_compute_network.vpc.id
  ip_cidr_range = "10.0.10.0/24"
  region        = var.region
  private_ip_google_access = true
}

resource "google_compute_subnetwork" "vpc_connector" {
  name          = "${local.name_prefix}-vpc-connector"
  network       = google_compute_network.vpc.id
  ip_cidr_range = "10.0.80.0/28"
  region        = var.region
  private_ip_google_access = true
}

resource "google_vpc_access_connector" "serverless" {
  name          = "${local.name_prefix}-connector"
  region        = var.region
  network       = google_compute_network.vpc.id
  subnet {
    name       = google_compute_subnetwork.vpc_connector.name
    project_id = var.project_id
  }
  min_throughput = 200
  max_throughput = 300
}

# -----------------------------------------------------------------------------
# Firewall rules
# -----------------------------------------------------------------------------
resource "google_compute_firewall" "allow_internal" {
  name        = "${local.name_prefix}-allow-internal"
  network     = google_compute_network.vpc.id
  direction   = "INGRESS"
  source_ranges = [var.vpc_cidr]

  allow {
    protocol = "tcp"
    ports    = ["22", "5432", "6379"]
  }
}

# -----------------------------------------------------------------------------
# Self-hosted PostgreSQL with pgvector
# -----------------------------------------------------------------------------
resource "google_compute_instance" "postgres" {
  name         = "${local.name_prefix}-postgres"
  machine_type = var.db_machine_type
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = 100
      type  = "pd-ssd"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.private.id
    access_config {} # Ephemeral external IP for Docker pulls; replace with Cloud NAT for production
  }

  metadata_startup_script = templatefile("${path.module}/startup-postgres.sh", {
    db_password = var.db_password
  })

  tags = ["private"]
  depends_on = [google_project_service.apis]
  allow_stopping_for_update = true
}

# -----------------------------------------------------------------------------
# Self-hosted Redis
# -----------------------------------------------------------------------------
resource "google_compute_instance" "redis" {
  name         = "${local.name_prefix}-redis"
  machine_type = var.redis_machine_type
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = 30
      type  = "pd-ssd"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.private.id
    access_config {}
  }

  metadata_startup_script = file("${path.module}/startup-redis.sh")

  tags = ["private"]
  depends_on = [google_project_service.apis]
  allow_stopping_for_update = true
}

# -----------------------------------------------------------------------------
# Artifact Registry for FastAPI Docker image
# -----------------------------------------------------------------------------
resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "${local.name_prefix}-app"
  format        = "DOCKER"
  description   = "FastAPI app container images"
  depends_on    = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# Cloud Run service
# -----------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "app" {
  name     = "${local.name_prefix}-app"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instances = var.app_min_instances
      max_instances = var.app_max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.serverless.id
      egress    = "ALL_TRAFFIC"
    }

    containers {
      image = var.app_image != "" ? var.app_image : "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}/app:latest"

      ports {
        container_port = 8000
      }

      resources {
        cpu_idle = true
        limits = {
          cpu    = var.app_cpu
          memory = var.app_memory
        }
      }

      env {
        name  = "PG_DATABASE_URL"
        value = "postgresql://${var.db_username}:${var.db_password}@${google_compute_instance.postgres.network_interface[0].network_ip}:5432/knowledge_base"
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${google_compute_instance.redis.network_interface[0].network_ip}:6379"
      }
      env {
        name  = "R2_BUCKET_NAME"
        value = var.r2_bucket_name
      }
      env {
        name  = "LOCAL_STORAGE_PATH"
        value = "/tmp/kb"
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_service_iam_member" "public" {
  service  = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -----------------------------------------------------------------------------
# Cloudflare R2 bucket for uploads (S3-compatible)
# -----------------------------------------------------------------------------
resource "cloudflare_r2_bucket" "uploads" {
  count       = var.cloudflare_account_id != "" ? 1 : 0
  account_id  = var.cloudflare_account_id
  name        = var.r2_bucket_name
  location    = "ENAM"
}

# -----------------------------------------------------------------------------
# Secret placeholders (use Secret Manager in production)
# -----------------------------------------------------------------------------
# TODO: store GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, R2 credentials in
# Google Secret Manager and mount them as secret_environment_variables in Cloud Run.

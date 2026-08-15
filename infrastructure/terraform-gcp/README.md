# Self-Hosted GCP Phase 1 Terraform

## What it provisions
- **VPC** with public/private subnets and a serverless VPC connector for Cloud Run.
- **Self-hosted PostgreSQL + pgvector** on Compute Engine.
- **Self-hosted Redis** on Compute Engine for Celery/RQ and caching.
- **Cloudflare R2 bucket** for S3-compatible document storage.
- **Artifact Registry** for the FastAPI Docker image.
- **Cloud Run** service with auto-scaling.

## Usage

1. Install Terraform and the `gcloud` CLI, then authenticate:
   ```bash
   gcloud auth application-default login
   ```
2. Copy the example tfvars and fill in real values:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```
3. Initialize and apply:
   ```bash
   terraform init
   terraform plan
   terraform apply
   ```
4. Build and push the FastAPI image to Artifact Registry:
   ```bash
   gcloud auth configure-docker <region>-docker.pkg.dev
   docker build -t <region>-docker.pkg.dev/<project>/ai-kb-prod-app/app:latest .
   docker push <region>-docker.pkg.dev/<project>/ai-kb-prod-app/app:latest
   ```
5. Set the Cloud Run environment secrets (GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, R2 credentials) via Google Secret Manager for production.

## Notes
- The Postgres/Redis instances are given ephemeral external IPs to pull Docker images; switch to Cloud NAT for hardening.
- Replace `access_config {}` blocks with Cloud NAT when ready for production.

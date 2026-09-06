# GCP Terraform Deployment Runbook

This guide walks through the exact steps to deploy the AI Knowledge Base / OmniGrapher backend on GCP using the existing `infrastructure/terraform-gcp` Terraform module.

> **Note:** This environment does not have `gcloud` or `terraform` installed, so the commands below must be run on a machine (or cloud shell) where both CLI tools are available and authenticated.

---

## 1. Pre-requisites

* A GCP project with billing enabled.
* `gcloud` CLI installed and logged in.
* `terraform` >= 1.5.0 installed.
* Docker installed (for building the FastAPI image).

```bash
# Confirm gcloud and terraform
gcloud version
terraform -v

# Set your project
export GCP_PROJECT="your-gcp-project-id"
gcloud config set project $GCP_PROJECT

# Generate local Application Default Credentials (ADC) for Terraform
gcloud auth application-default login
```

---

## 2. Configure `terraform.tfvars`

The file `terraform.tfvars` has already been generated. Open it and change at least the following three fields:

```hcl
project_id  = "your-gcp-project-id"       # replace
db_password = "a-very-strong-password"    # replace
```

Optional but recommended:

```hcl
db_machine_type    = "e2-small"   # keep low-costedis_machine_type = "e2-micro"   # keep low-cost
```

---

## 3. Initialize Terraform

```bash
cd infrastructure/terraform-gcp
terraform init
```

---

## 4. Deploy the base infrastructure first

We first create the Artifact Registry and VPC so the Docker image can be pushed, then we create the GCE VMs and Cloud Run service in the next step.

```bash
# Step 4a: create the base network, registry, and APIs
terraform apply -target=google_project_service.apis \
                -target=google_compute_network.vpc \
                -target=google_compute_subnetwork.public \
                -target=google_compute_subnetwork.private \
                -target=google_compute_subnetwork.vpc_connector \
                -target=google_vpc_access_connector.serverless \
                -target=google_artifact_registry_repository.app \
                -auto-approve
```

After this succeeds, the Artifact Registry repository exists and the VPC connector is ready.

---

## 5. Build and push the FastAPI Docker image

The backend image lives in `knowledge_base_pilot/`. It exposes port `8001`, matching the `container_port` in the Terraform Cloud Run resource.

```bash
cd ../..   # back to repo root

# Set the same values from terraform.tfvars
export GCP_PROJECT="your-gcp-project-id"
export GCP_REGION="us-central1"
export AR_REPO="${GCP_PROJECT}-prod-app"

# Configure Docker to use gcloud auth
gcloud auth configure-docker ${GCP_REGION}-docker.pkg.dev

# Build and push the backend image
docker build \
  -t ${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/app:latest \
  ./knowledge_base_pilot

docker push ${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/app:latest
```

> **Optional: include the AEO Studio module**
>
> If you want `/api/v1/aeo-studio` routes, the backend image must contain the `omnigrapher` Python package. The current `knowledge_base_pilot/Dockerfile` only copies `knowledge_base_pilot/`. To enable AEO, build from the repo root using a `Dockerfile` that copies both `knowledge_base_pilot/` and `omnigrapher/`. See the `docs/GCP_DEPLOYMENT_BLUEPRINT.md` for details.

---

## 6. Deploy the rest of the infrastructure

Now that the image is in Artifact Registry, run the full Terraform apply:

```bash
cd infrastructure/terraform-gcp

export APP_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/app:latest"

terraform apply -var="app_image=${APP_IMAGE}" -auto-approve
```

This creates:

* Firewall rules
* GCE VM for PostgreSQL + pgvector + ChromaDB
* GCE VM for Redis
* Cloud Run service for the FastAPI backend
* Public invoker permission on the Cloud Run service

---

## 7. Verify the deployment

```bash
# Get the backend URL
gcloud run services describe "${GCP_PROJECT}-prod-app" \
  --region=us-central1 \
  --format='value(status.url)'

# Example output:
# https://your-project-prod-app-abc123-uc.a.run.app
```

Open that URL in a browser or run:

```bash
curl https://your-project-prod-app-...a.run.app/api/v1/health
```

(Replace the URL with the actual one.)

---

## 8. Set required secrets and remaining env vars

The Terraform module provisions the base connection strings. You still need to add secrets for auth and managed LLM API keys.

### 8.1 Create secrets in Secret Manager

```bash
# JWT signing key (must be a long random string)
openssl rand -hex 32 > /tmp/secret_key.txt
gcloud secrets create KB_SECRET_KEY --replication-policy="automatic"
gcloud secrets versions add KB_SECRET_KEY --data-file=/tmp/secret_key.txt
rm /tmp/secret_key.txt

# Chunk HMAC key
openssl rand -hex 32 > /tmp/chunk_key.txt
gcloud secrets create KB_CHUNK_HMAC_KEY --replication-policy="automatic"
gcloud secrets versions add KB_CHUNK_HMAC_KEY --data-file=/tmp/chunk_key.txt
rm /tmp/chunk_key.txt

# Optional but recommended: managed LLM keys
# Create the secret only if you have a value to set
for key in GEMINI_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY XAI_API_KEY DEEPSEEK_API_KEY; do
  gcloud secrets create KB_${key} --replication-policy="automatic" 2>/dev/null || true
done

# To add a value, e.g. Gemini:
# echo -n "AIza..." | gcloud secrets versions add KB_GEMINI_API_KEY --data-file=-
```

### 8.2 Mount secrets and set CORS on the Cloud Run service

Get the frontend URL (or `https://*` for local testing) and run:

```bash
export SERVICE="${GCP_PROJECT}-prod-app"
export REGION="us-central1"
export FRONTEND_URL="https://your-frontend-url.a.run.app"   # replace

gcloud run services update $SERVICE \
  --region=$REGION \
  --set-env-vars "CORS_ORIGINS=$FRONTEND_URL" \
  --set-env-vars "SECURE_COOKIES=true" \
  --set-env-vars "USE_PEFT_ADAPTERS=false" \
  --set-env-vars "INGESTION_WORKER_ENABLED=false" \
  --update-secrets "SECRET_KEY=KB_SECRET_KEY:latest" \
  --update-secrets "CHUNK_HMAC_KEY=KB_CHUNK_HMAC_KEY:latest" \
  --update-secrets "GEMINI_API_KEY=KB_GEMINI_API_KEY:latest"
```

> **Important:** Only add the LLM secret lines for keys you actually have. If a key is not set, do not mount it or the revision will fail.

---

## 9. Initialize the database

The PostgreSQL VM runs a startup script that creates the `knowledge_base` database and `kb_admin` user. After the VM is up, SSH in and verify:

```bash
export DB_VM="${GCP_PROJECT}-prod-postgres"
gcloud compute ssh $DB_VM --zone=us-central1-a --command 'sudo docker logs pgvector'
```

Then create the `pgvector` extension if it is not already present (the `pgvector/pgvector` image should create it automatically, but confirm):

```bash
gcloud compute ssh $DB_VM --zone=us-central1-a --command \
  "sudo docker exec -it pgvector psql -U kb_admin -d knowledge_base -c 'CREATE EXTENSION IF NOT EXISTS vector;'"
```

Run Alembic migrations from a local backend container or from the Cloud Run service:

```bash
# Run from the deployed image against the private DB
gcloud run services update $SERVICE \
  --region=$REGION \
  --command bash \
  --args="-c,alembic upgrade head" \
  --set-env-vars "PG_DATABASE_URL=postgresql://kb_admin:YOUR_DB_PASSWORD@YOUR_DB_PRIVATE_IP:5432/knowledge_base" \
  --no-traffic
```

(Replace `YOUR_DB_PASSWORD` and `YOUR_DB_PRIVATE_IP`. You can get the IP from the Terraform output or `gcloud compute instances list`.)

---

## 10. Deploy the Next.js frontend (optional, but needed for the UI)

The Terraform module does not deploy the frontend. Use `gcloud` directly:

```bash
cd web_app_nextjs

# Make sure the Dockerfile uses a production build (CMD ["npm", "start"])
gcloud builds submit \
  --tag ${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/frontend:latest .

export BACKEND_URL=$(gcloud run services describe $SERVICE --region=$REGION --format='value(status.url)')

gcloud run deploy "${GCP_PROJECT}-prod-frontend" \
  --image ${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${AR_REPO}/frontend:latest \
  --region=$REGION \
  --port=3000 \
  --memory=1Gi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=5 \
  --set-env-vars "INTERNAL_API_URL=$BACKEND_URL" \
  --set-env-vars "NEXT_PUBLIC_BACKEND_URL=$BACKEND_URL" \
  --allow-unauthenticated
```

Then update the backend `CORS_ORIGINS` with the real frontend URL (see step 8.2).

---

## 11. Cleanup if needed

To avoid ongoing charges, run:

```bash
cd infrastructure/terraform-gcp
terraform destroy -auto-approve
```

> **Warning:** `terraform destroy` will delete the GCE VMs and the Cloud Run service, including any data stored on the local persistent disks. Back up first.

---

## Changes already made to the Terraform module

Before writing this guide, the following small fixes were applied to `infrastructure/terraform-gcp/` so the deployment actually works:

1. `main.tf` container port changed from `8000` to `8001` to match the backend `Dockerfile`.
2. `main.tf` now passes `CHROMA_HOST`, `CHROMA_PORT`, and `CHROMA_SSL` to the Cloud Run service.
3. `startup-postgres.sh` now starts a `chromadb:0.6.3` container on the same VM (port `8000`).
4. Firewall `allow_internal` now allows port `8000` for ChromaDB.
5. `variables.tf` now includes `db_username` (default: `kb_admin`).
6. `terraform.tfvars` was generated with low-cost machine types and placeholders.

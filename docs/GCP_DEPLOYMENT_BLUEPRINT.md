# OmniGrapher / AI Knowledge Base Suite — GCP Deployment Readiness & Architecture Blueprint

> **Goal:** Deploy the full stack on GCP using low-cost, serverless-first services (Cloud Run, Artifact Registry, Cloud Storage, Compute Engine free-tier for stateful components, and Secret Manager). This document is intended as a runbook: read the architecture, collect the secrets, copy the `gcloud` commands, and execute.

---

## 1. Project Architecture & Service Breakdown

### 1.1 High-level runtime architecture

```
                                 ┌─────────────────────────────────────┐
                                 │         Cloud Load Balancer         │
                                 │     (optional, HTTPS frontend)      │
                                 └──────────────┬──────────────────────┘
                                                │
                       ┌────────────────────────┼────────────────────────┐
                       │                        │                        │
                       ▼                        ▼                        ▼
            ┌──────────────────┐    ┌────────────────────┐    ┌──────────────────┐
            │  Next.js Frontend  │    │ FastAPI Backend    │    │ Celery Worker    │
            │  (Cloud Run)       │    │ (Cloud Run)        │    │ (Cloud Run)      │
            │  Port 3000         │    │ Port 8001          │    │ Ingestion queue  │
            └────────┬───────────┘    └────────┬───────────┘    └────────┬─────────┘
                     │                         │                          │
                     │                         │     ┌──────────────────────┘
                     │                         │     │
                     ▼                         ▼     ▼
            ┌──────────────────┐    ┌──────────────────────────────────────┐
            │  Cloud Storage     │    │  PostgreSQL + pgvector (Cloud SQL or │
            │  / R2 (uploads)    │    │  small GCE with pgvector image)      │
            └──────────────────┘    └──────────────────────────────────────┘
                                                │
                       ┌────────────────────────┼────────────────────────┐
                       │                        │                        │
                       ▼                        ▼                        ▼
            ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
            │  ChromaDB          │    │  Redis           │    │  Ollama (opt.)   │
            │  (Compute Engine)  │    │  (Memorystore    │    │  (Compute Engine │
            │  or managed VM     │    │   for Redis or   │    │   or Cloud Run   │
            │  Port 8000         │    │   GCE)           │    │   GPU)           │
            └──────────────────┘    └──────────────────┘    └──────────────────┘
                       │                        │
                       └──────────────┬─────────┘
                                      ▼
                          ┌─────────────────────┐
                          │   Gemini / OpenAI   │
                          │   (managed LLM APIs)│
                          └─────────────────────┘
```

### 1.2 Components, Dockerfiles, ports, and GCP targets

| # | Service | Source Folder | Dockerfile | Local Port | Role | GCP Target |
|---|---------|---------------|------------|------------|------|------------|
| 1 | **Next.js Web Frontend** | `web_app_nextjs/` | `web_app_nextjs/Dockerfile` | `3000` | Main chat/document/AEO Studio UI. | **Cloud Run** (containerized) or Firebase Hosting (after static export) |
| 2 | **FastAPI Backend** | `knowledge_base_pilot/` | `knowledge_base_pilot/Dockerfile` | `8001` | REST API, RAG, auth, billing, AEO routes. | **Cloud Run** |
| 3 | **Celery Worker** | `knowledge_base_pilot/` | same as backend | n/a (runs inside container) | Background document ingestion (OCR, chunk, embed, index). | **Cloud Run** (single instance, low concurrency) or GCE VM |
| 4 | **PostgreSQL + pgvector** | n/a (uses `pgvector/pgvector:pg16` image) | n/a | `5432` mapped to `5433` | Relational DB: users, sessions, documents, credits, AEO. | **Cloud SQL for PostgreSQL** with `pgvector` extension enabled, or a low-cost **GCE e2-micro** |
| 5 | **Redis** | n/a (uses `redis:7-alpine`) | n/a | `6379` | Celery broker, caching, rate-limiting. | **Memorystore for Redis** (paid) or low-cost **GCE e2-micro** |
| 6 | **ChromaDB** | n/a (uses `chromadb/chroma:0.6.3`) | n/a | `8000` mapped to `8002` | Vector store for document embeddings. | **GCE e2-small/e2-medium** with persistent disk (Chroma is not serverless) |
| 7 | **Ollama** | n/a (uses `ollama/ollama:latest`) | n/a | `11434` | Local LLM/embedding inference (CPU/GPU). | **Optional** — use **Gemini** on Cloud Run instead. If needed, deploy on a **GCE n1-standard with GPU** or **Cloud Run GPU preview**. |
| 8 | **PEFT Engine** | `peft_engine/` | *none yet* | `8002` (default) | Fine-tuned LoRA adapter inference. | **Optional** — a dedicated **GCE n1-standard-2+T4** or **Vertex AI** endpoint. Not required for initial serverless deployment. |
| 9 | **AEO Studio module** | `omnigrapher/modules/aeo_studio/` | *none* (Python module) | n/a | Adds `/api/v1/aeo-studio` routes to the FastAPI backend. | Must be **copied or installed into the backend image** before pushing to Artifact Registry. |

> **Note on `web_app/` and `desktop_app/`:** These are older or standalone clients. They are **not** part of the containerized runtime and can be ignored for the GCP deployment unless you specifically want to build them. This blueprint focuses on `web_app_nextjs`, `knowledge_base_pilot`, and the stateful backing services.

### 1.3 Local `docker-compose.yml` mapping

The `docker-compose.yml` at the repo root (`ai-knowledge-base-suite`) is the source of truth for local orchestration. For GCP, decompose it as follows:

```yaml
# Local service -> GCP equivalent
postgres   -> Cloud SQL PostgreSQL (or GCE VM)
chromadb   -> GCE VM with persistent disk (cannot run reliably on Cloud Run)
redis      -> Memorystore for Redis (or GCE VM)
ollama     -> Optional GCE VM / Cloud Run GPU / replaced by Gemini API
backend    -> Cloud Run service
frontend   -> Cloud Run service
celery     -> Cloud Run service (worker) OR GCE VM
```

### 1.4 Stateful services that cannot be pure Cloud Run

Cloud Run is stateless and ephemeral. The following services must remain on GCP Compute Engine (or use managed alternatives):

* **PostgreSQL:** Use **Cloud SQL for PostgreSQL** with the `pgvector` extension (or **AlloyDB** for larger vector needs). Cloud SQL is managed but is **not free** after the trial.
* **Redis:** Use **Memorystore for Redis** (fully managed) or a **GCE e2-micro** VM.
* **ChromaDB:** No managed serverless equivalent on GCP today. Run on a **GCE e2-small/e2-medium** with a persistent SSD.
* **Ollama / PEFT Engine:** Requires GPU/CPU persistence. Run on a **GCE VM** (n1-standard-2 or larger) or use **Vertex AI** / **Gemini API** instead.

---

## 2. Environment Variables & Secrets Inventory

### 2.1 Variable placement legend

| Symbol | Meaning |
|--------|---------|
| 🔐 | **Put in Google Secret Manager** and mount as `secret_environment_variables` in Cloud Run |
| 🏷️ | Plain environment variable in Cloud Run / GCE metadata startup-script |
| ⚙️ | Build-time or runtime config (can be hard-coded in Terraform or gcloud flags) |

### 2.2 Backend (`knowledge_base_pilot`) variables

| Variable | Sample Value | Purpose | Placement |
|----------|--------------|---------|-----------|
| `SECRET_KEY` | `change-me-in-production` | JWT signing / auth | 🔐 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | JWT access token TTL | 🏷️ |
| `REFRESH_TOKEN_DAYS` | `7` | JWT refresh token TTL | 🏷️ |
| `CORS_ORIGINS` | `https://YOUR-FRONTEND-URL.a.run.app` | Allowed browser origins | 🏷️ |
| `OLLAMA_BASE_URL` | `http://localhost:11434` or `http://OLLAMA-VM:11434` | Local LLM endpoint | 🏷️ |
| `LLM_MODEL` | `llama3.2:latest` | Default Ollama LLM | 🏷️ |
| `EMBED_MODEL` | `nomic-embed-text:latest` | Default Ollama embedder | 🏷️ |
| `GEMINI_API_KEY` | `AIza...` | Managed LLM provider | 🔐 |
| `OPENAI_API_KEY` | `sk-...` | Managed LLM provider | 🔐 |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Managed LLM provider | 🔐 |
| `XAI_API_KEY` | `xai-...` | Managed LLM provider | 🔐 |
| `DEEPSEEK_API_KEY` | `...` | Managed LLM provider | 🔐 |
| `DATABASE_URL` | `sqlite:///tmp/kb.db` | Fallback SQLite (local) | 🏷️ |
| `PG_DATABASE_URL` | `postgresql://awap_user:PASS@SQL-IP:5432/knowledge_base` | Production relational DB | 🔐 |
| `PGVECTOR_DIMENSION` | `384` | Vector dimension for pgvector | 🏷️ |
| `REDIS_URL` | `redis://REDIS-IP:6379/0` | Celery/caching | 🔐 |
| `CHROMA_HOST` | `CHROMA-IP` | ChromaDB server | 🏷️ |
| `CHROMA_PORT` | `8000` | ChromaDB port | 🏷️ |
| `CHROMA_SSL` | `false` | SSL for Chroma (keep `false` inside VPC) | 🏷️ |
| `LOCAL_STORAGE_PATH` | `/tmp/kb` | Temporary local upload storage | 🏷️ |
| `R2_ENDPOINT_URL` | `https://...r2.cloudflarestorage.com` | S3-compatible object storage | 🔐 |
| `R2_ACCESS_KEY_ID` | `...` | R2 access key | 🔐 |
| `R2_SECRET_ACCESS_KEY` | `...` | R2 secret | 🔐 |
| `R2_BUCKET_NAME` | `ai-kb-uploads` | Object storage bucket | 🏷️ |
| `CHUNK_HMAC_KEY` | `change-me-in-production` | Chunk integrity HMAC | 🔐 |
| `SECURE_COOKIES` | `true` | HTTPS-only cookies | 🏷️ |
| `AEO_DATABASE_URL` | `sqlite:////data/aeo/aeo_studio.db` or `postgresql://...` | AEO Studio DB | 🔐 |
| `USE_PEFT_ADAPTERS` | `false` | Route to PEFT engine | 🏷️ |
| `PEFT_ENGINE_URL` | `http://localhost:8002` | PEFT engine endpoint | 🏷️ |
| `PEFT_ENGINE_CONNECT_TIMEOUT` | `5.0` | PEFT connect timeout | 🏷️ |
| `PEFT_ENGINE_READ_TIMEOUT` | `60.0` | PEFT read timeout | 🏷️ |
| `PEFT_ADAPTER_MAP` | `{"security":"security-guard",...}` | Domain → adapter mapping | 🏷️ |

### 2.3 Frontend (`web_app_nextjs`) variables

| Variable | Sample Value | Purpose | Placement |
|----------|--------------|---------|-----------|
| `INTERNAL_API_URL` | `https://YOUR-BACKEND-URL.a.run.app` | Backend URL used by Next.js server-side | 🏷️ |
| `NEXT_PUBLIC_BACKEND_URL` | `https://YOUR-BACKEND-URL.a.run.app` | Backend URL used by the browser | 🏷️ |

### 2.4 PEFT Engine (`peft_engine`) — optional

| Variable | Sample Value | Purpose | Placement |
|----------|--------------|---------|-----------|
| `PEFT_HOST` | `0.0.0.0` | Listen host | 🏷️ |
| `PEFT_PORT` | `8002` | Listen port | 🏷️ |
| `PEFT_BASE_MODEL` | `unsloth/Llama-3.2-3B-Instruct` | Base model | 🏷️ |
| `PEFT_PRELOAD_ON_STARTUP` | `true` | Pre-load model into VRAM | 🏷️ |
| `OMNIGRAPHER_STORAGE_BASE` | `/data/omnigrapher` | External storage path | 🏷️ |

---

## 3. GCP Deployment Strategy & Cost Optimization

### 3.1 Service-to-GCP mapping

| Service | GCP Product | Why this choice | Free-tier / Cost guardrail |
|---------|-------------|-----------------|---------------------------|
| **Frontend** | **Cloud Run** (container) | Full-stack Next.js app, serverless scaling | Always Free: 2M requests/month; pay only when active |
| **Backend API** | **Cloud Run** (container) | FastAPI, auto-scale to zero | Always Free: 2M requests/month; set `min-instances=0` for dev |
| **Celery Worker** | **Cloud Run** or **GCE VM** | Long-running ingestion jobs | For long jobs, a **GCE e2-medium** is often cheaper than Cloud Run |
| **PostgreSQL** | **Cloud SQL PostgreSQL** with `pgvector` | Managed, backups, HA option | Use the smallest shared-core `db-f1-micro` for dev; monitor free $300 credits |
| **Redis** | **Memorystore for Redis M1** or **GCE VM** | Celery broker + cache | Use the smallest M1 tier or a free `e2-micro` VM |
| **ChromaDB** | **GCE e2-small/e2-medium** + SSD persistent disk | Needs persistent local storage | `e2-micro` (1 vCPU, 1 GB) is free 750 hrs/month; use 30-50 GB pd-balanced |
| **Ollama** | **GCE with optional GPU** or **Vertex AI** | GPU for local inference | Skip for free tier; use **Gemini Flash 1.5** (free quota) as default |
| **Object Storage** | **Cloud Storage** (or keep R2) | Document uploads and exports | Always Free: 5 GB/month Standard storage in the US |
| **Images** | **Artifact Registry** | Push Docker images | Always Free: 500 MB storage + egress charges |
| **Secrets** | **Secret Manager** | API keys, DB passwords, JWT keys | Always Free: 6 secret versions + 10K operations/month |
| **Load Balancer / HTTPS** | **Cloud Load Balancing + Cloud Armor** (optional) | Custom domain + HTTPS | Free tier does not include load balancers; use Cloud Run built-in HTTPS URLs for zero cost |

### 3.2 Cost-optimized deployment shape

For the **lowest bill** while remaining on GCP:

1. **Database + Vector + Cache on GCE free tier**
   * Spin up **one `e2-micro` VM** (free 750 hrs/mo) in the `us-central1` region.
   * Install **PostgreSQL 16 + pgvector**, **Redis**, and **ChromaDB** on the same VM (separate by port only; not production-HA, but fits free tier).
   * Attach a **50 GB standard persistent disk** for uploads and vector data.
   * Make sure the VM is in a VPC with `private-ip-google-access` enabled so Cloud Run can reach it through a **Serverless VPC Connector**.

2. **Backend + Frontend on Cloud Run**
   * Deploy the FastAPI image with `min-instances=0` and `concurrency=80`.
   * Deploy the Next.js image with `min-instances=0`.
   * Use the built-in `*.a.run.app` HTTPS URL to avoid load balancer charges.

3. **Celery on Cloud Run or GCE**
   * If documents are small and infrequent, run Celery as a second Cloud Run service (set `command` to the celery worker start).
   * If you process many/large files, run Celery on the same GCE VM or a dedicated `e2-small`.

4. **Ollama / PEFT**
   * **Skip entirely in the first deploy** and rely on `GEMINI_API_KEY` for free-tier managed inference.
   * Add later as a GCE GPU VM when budget allows.

### 3.3 Required container build / run flags

#### Backend / Celery image

* **Base image:** `python:3.11` (uses `libgl1`, etc.)
* **Expose port:** `8001`
* **AEO module:** If you need `/api/v1/aeo-studio`, ensure the `omnigrapher/` folder is in the Docker build context and `PYTHONPATH` is set to `/workspace` or `/app`.
* **Cloud Run flags:**
  * `--port 8001`
  * `--memory 2Gi` (minimum for Chroma + OCR + embeddings; try `1Gi` only for dev)
  * `--cpu 1`
  * `--concurrency 80`
  * `--max-instances 5`
  * `--min-instances 0` (dev) or `1` (prod to avoid cold starts)
  * `--vpc-connector your-connector` (to reach Postgres/Redis/Chroma)
  * `--vpc-egress all-traffic`

#### Frontend image

The current `web_app_nextjs/Dockerfile` is for development (`npm run dev`). Replace it with a production build for Cloud Run:

```dockerfile
# web_app_nextjs/Dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"
CMD ["npm", "start"]
```

> If you keep `npm run dev`, Cloud Run will work for testing but is not suitable for production.

* **Expose port:** `3000`
* **Cloud Run flags:**
  * `--port 3000`
  * `--memory 1Gi`
  * `--cpu 1`
  * `--min-instances 0`
  * `--max-instances 5`

---

## 4. Automated GCP Deployment Guide

### 4.1 One-time: authenticate and create project

```bash
# 1. Install gcloud CLI and authenticate
#    https://cloud.google.com/sdk/docs/install

# 2. Log in
#    Replace with YOUR-PROJECT-ID
export GCP_PROJECT="your-gcp-project-id"
export GCP_REGION="us-central1"
export GCP_ZONE="us-central1-a"
export AR_REPO="ai-kb-prod-app"

gcloud auth login
gcloud config set project $GCP_PROJECT
gcloud config set run/region $GCP_REGION

# 3. Confirm billing is enabled
gcloud billing projects describe $GCP_PROJECT
```

### 4.2 Enable required APIs

```bash
gcloud services enable run.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable sqladmin.googleapis.com
gcloud services enable redis.googleapis.com
gcloud services enable compute.googleapis.com
gcloud services enable vpcaccess.googleapis.com
gcloud services enable cloudbuild.googleapis.com
```

### 4.3 Build and push Docker images to Artifact Registry

```bash
# 1. Create the Artifact Registry repository
gcloud artifacts repositories create $AR_REPO \
  --repository-format=docker \
  --location=$GCP_REGION \
  --description="AI Knowledge Base containers"

# 2. Configure Docker to use gcloud auth
gcloud auth configure-docker $GCP_REGION-docker.pkg.dev

# 3. Build and push backend
cd knowledge_base_pilot
# IMPORTANT: include the omnigrapher module if you need AEO Studio
# cp -r ../omnigrapher .  # or add a .dockerignore exception and build from the repo root
docker build -t $GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$AR_REPO/backend:latest .
docker push $GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$AR_REPO/backend:latest
cd ..

# 4. Build and push frontend (after updating Dockerfile to production, see 3.3)
cd web_app_nextjs
docker build -t $GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$AR_REPO/frontend:latest .
docker push $GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$AR_REPO/frontend:latest
cd ..
```

> **Using Cloud Build instead of local Docker:**
>
> ```bash
> gcloud builds submit knowledge_base_pilot \
>   --tag $GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$AR_REPO/backend:latest
> gcloud builds submit web_app_nextjs \
>   --tag $GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$AR_REPO/frontend:latest
> ```

### 4.4 Store secrets in Secret Manager

```bash
# Create the backend API secret (JWT)
gcloud secrets create KB_SECRET_KEY --replication-policy="automatic"
echo -n "$(openssl rand -hex 32)" | gcloud secrets versions add KB_SECRET_KEY --data-file=-

# Add managed API keys (repeat for each one you have)
echo -n "AIza..." | gcloud secrets versions add GEMINI_API_KEY --data-file=-

# Database password
gcloud secrets create DB_PASSWORD --replication-policy="automatic"
echo -n "your-strong-db-password" | gcloud secrets versions add DB_PASSWORD --data-file=-

# Chunk HMAC key
gcloud secrets create CHUNK_HMAC_KEY --replication-policy="automatic"
echo -n "$(openssl rand -hex 32)" | gcloud secrets versions add CHUNK_HMAC_KEY --data-file=-

# R2 credentials (if using Cloudflare R2)
gcloud secrets create R2_SECRET_ACCESS_KEY --replication-policy="automatic"
echo -n "..." | gcloud secrets versions add R2_SECRET_ACCESS_KEY --data-file=-
```

> Reference a secret in Cloud Run like this:
>
> `--update-secrets=SECRET_KEY=KB_SECRET_KEY:latest`

### 4.5 Create a VPC and serverless connector

Cloud Run must reach Postgres/Redis/Chroma over a private VPC. If you use **Cloud SQL** and **Memorystore**, this is handled automatically by their Cloud connectors. If you use **GCE**, create a VPC connector:

```bash
# Create the VPC and private subnet
gcloud compute networks create ai-kb-vpc --subnet-mode=custom
gcloud compute networks subnets create ai-kb-subnet \
  --network=ai-kb-vpc \
  --range=10.0.0.0/24 \
  --region=$GCP_REGION \
  --enable-private-ip-google-access

# Create a serverless VPC connector
gcloud compute networks vpc-access connectors create ai-kb-connector \
  --network=ai-kb-vpc \
  --region=$GCP_REGION \
  --range=10.8.0.0/28 \
  --min-throughput=200 \
  --max-throughput=300
```

### 4.6 Deploy backend to Cloud Run

```bash
export BACKEND_IMAGE="$GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$AR_REPO/backend:latest"

export PG_IP="10.0.0.10"       # replace with Cloud SQL IP or GCE Postgres IP
export REDIS_IP="10.0.0.10"    # same VM or Memorystore IP
export CHROMA_IP="10.0.0.10"   # same VM or dedicated Chroma IP
export FRONTEND_URL="https://ai-kb-frontend-xxx.a.run.app"  # update after frontend deploy

gcloud run deploy ai-kb-backend \
  --image $BACKEND_IMAGE \
  --region $GCP_REGION \
  --port 8001 \
  --memory 2Gi \
  --cpu 1 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 5 \
  --vpc-connector ai-kb-connector \
  --vpc-egress all-traffic \
  --set-env-vars "CORS_ORIGINS=$FRONTEND_URL" \
  --set-env-vars "PG_DATABASE_URL=postgresql://awap_user:@$PG_IP:5432/knowledge_base" \
  --set-env-vars "REDIS_URL=redis://$REDIS_IP:6379/0" \
  --set-env-vars "CHROMA_HOST=$CHROMA_IP" \
  --set-env-vars "CHROMA_PORT=8000" \
  --set-env-vars "CHROMA_SSL=false" \
  --set-env-vars "LLM_MODEL=llama3.2:latest" \
  --set-env-vars "EMBED_MODEL=nomic-embed-text:latest" \
  --set-env-vars "LOCAL_STORAGE_PATH=/tmp/kb" \
  --set-env-vars "SECURE_COOKIES=true" \
  --set-env-vars "USE_PEFT_ADAPTERS=false" \
  --set-env-vars "INGESTION_WORKER_ENABLED=false" \
  --set-env-vars "AEO_DATABASE_URL=postgresql://awap_user:@$PG_IP:5432/knowledge_base" \
  --update-secrets "SECRET_KEY=KB_SECRET_KEY:latest" \
  --update-secrets "CHUNK_HMAC_KEY=CHUNK_HMAC_KEY:latest" \
  --update-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest" \
  --allow-unauthenticated \
  --service-account ai-kb-backend@$GCP_PROJECT.iam.gserviceaccount.com

# Note: you must also create the service account and grant Secret Manager access:
# gcloud iam service-accounts create ai-kb-backend --display-name="AI KB Backend"
# gcloud projects add-iam-policy-binding $GCP_PROJECT \
#   --member="serviceAccount:ai-kb-backend@$GCP_PROJECT.iam.gserviceaccount.com" \
#   --role="roles/secretmanager.secretAccessor"
```

> **If using Google Cloud Storage instead of R2**, set `STORAGE_BACKEND=gcs` (if supported by the app) and pass a service-account with `roles/storage.objectAdmin`.

### 4.7 Deploy frontend to Cloud Run

```bash
export FRONTEND_IMAGE="$GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$AR_REPO/frontend:latest"

# Get the backend URL from the previous step
export BACKEND_URL=$(gcloud run services describe ai-kb-backend --region=$GCP_REGION --format='value(status.url)')

gcloud run deploy ai-kb-frontend \
  --image $FRONTEND_IMAGE \
  --region $GCP_REGION \
  --port 3000 \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 5 \
  --set-env-vars "INTERNAL_API_URL=$BACKEND_URL" \
  --set-env-vars "NEXT_PUBLIC_BACKEND_URL=$BACKEND_URL" \
  --allow-unauthenticated
```

### 4.8 Deploy Celery worker

The backend image already contains Celery. Deploy a second Cloud Run service with a different startup command, or run it on the same backend service with a separate revision. The simplest approach is a **dedicated Cloud Run job/service**:

```bash
# Option A: Cloud Run service (continuous worker)
gcloud run deploy ai-kb-celery \
  --image $BACKEND_IMAGE \
  --region $GCP_REGION \
  --port 8001 \
  --memory 2Gi \
  --cpu 1 \
  --concurrency 1 \
  --min-instances 1 \
  --max-instances 1 \
  --no-allow-unauthenticated \
  --command celery \
  --args="-A,app.celery_app,worker,-Q,ingestion,-l,info,--pool=solo" \
  --vpc-connector ai-kb-connector \
  --vpc-egress all-traffic \
  --set-env-vars "PG_DATABASE_URL=postgresql://awap_user:@$PG_IP:5432/knowledge_base" \
  --set-env-vars "REDIS_URL=redis://$REDIS_IP:6379/0" \
  --set-env-vars "CHROMA_HOST=$CHROMA_IP" \
  --set-env-vars "CHROMA_PORT=8000" \
  --set-env-vars "INGESTION_WORKER_ENABLED=true" \
  --update-secrets "SECRET_KEY=KB_SECRET_KEY:latest"
```

> **Option B (recommended for large PDFs):** Run Celery on the same GCE VM that hosts Postgres/Redis/Chroma. This avoids Cloud Run request/response limits and is cheaper for long ingestion.

### 4.9 Optional: deploy the PEFT engine

Only needed if `USE_PEFT_ADAPTERS=true`.

1. Build a Docker image for `peft_engine/` (you must add a `Dockerfile` first).
2. Push to Artifact Registry.
3. Deploy on a **GCE VM with a T4 GPU** or a **Cloud Run GPU** service (preview).
4. Expose port `8002` and set `PEFT_ENGINE_URL` in the backend.

```bash
# Example GCE GPU VM (not free tier)
gcloud compute instances create ai-kb-peft \
  --zone=$GCP_ZONE \
  --machine-type=n1-standard-2 \
  --accelerator=type=nvidia-tesla-t4,count=1 \
  --image-family=pytorch-latest-gpu \
  --image-project=deeplearning-platform-release \
  --boot-disk-size=50GB \
  --metadata-from-file startup-script=peft_engine/startup-gpu.sh
```

---

## 5. Post-Deploy Checklist

1. **Create the database and enable `pgvector`**
   ```sql
   CREATE DATABASE knowledge_base;
   CREATE USER awap_user WITH PASSWORD 'your-password';
   GRANT ALL PRIVILEGES ON DATABASE knowledge_base TO awap_user;
   \c knowledge_base
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

2. **Run migrations / create tables**
   Inside the `knowledge_base_pilot` container:
   ```bash
   alembic upgrade head
   ```
   Or call the backend health-check endpoint to verify table creation.

3. **Set `CORS_ORIGINS` to the final frontend URL**
   After `ai-kb-frontend` is deployed, take the `*.a.run.app` URL and update the backend env var `CORS_ORIGINS`.

4. **Seed a user**
   Use the registration endpoint or a backend seed script to create the first admin user.

5. **Test the full pipeline**
   * Upload a document.
   * Watch Celery process it.
   * Run a document chat and an `Ask AI Freely` query.

---

## 6. Terraform Alternative (Already Provided)

The repo already contains `infrastructure/terraform-gcp/`. It is a working Phase 1 Terraform that provisions:

* GCP VPC + Serverless VPC Connector
* Cloud Run service for the FastAPI backend
* Artifact Registry repository
* Self-hosted Postgres and Redis on GCE
* Cloudflare R2 bucket for object storage

You can use it as the infrastructure foundation and layer the frontend + Celery Cloud Run services on top using the `gcloud` commands in this blueprint.

### Terraform quick-start

```bash
cd infrastructure/terraform-gcp
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your project_id, region, db_password, etc.
terraform init
terraform plan
terraform apply
```

---

## 7. Common Gotchas

1. **`pgvector` extension** — make sure it is created in the `knowledge_base` database *before* the app starts.
2. **ChromaDB persistence** — do not store Chroma data in the container; use a persistent disk and mount `PERSIST_DIRECTORY` to it.
3. **AEO Studio missing in the backend image** — the backend `Dockerfile` does not copy `omnigrapher/`. Add a `COPY ../omnigrapher /workspace/omnigrapher` step or build the backend from the repo root.
4. **CORS** — Cloud Run URLs are unpredictable; keep `CORS_ORIGINS` updated after the first frontend deploy.
5. **Cold starts** — Cloud Run `min-instances=0` is cheap but adds 2–5 seconds latency. Set `min-instances=1` on the backend for production.
6. **Secrets as plaintext** — never pass `SECRET_KEY`, `GEMINI_API_KEY`, DB passwords, or `CHUNK_HMAC_KEY` with `--set-env-vars`; always use `--update-secrets`.

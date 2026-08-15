# Self-Hosted GCP Production Roadmap (500K+ users)

## Goals
- Run the platform profitably at 500K+ users without paying for third-party AI/SaaS subscriptions beyond cloud compute.
- Keep unit economics strong by routing low-complexity traffic to the cheapest model (`Gemini 1.5 Flash`) and charging a margin on premium models.
- Minimize operational toil with Terraform-managed, self-hosted GCP infrastructure.

---

## Phase 1 — Self-hosted GCP foundation + multi-model abstraction (current)
**Goal:** Eliminate SaaS dependencies for core compute and data.

- **Terraform IaC** (`infrastructure/terraform/`)
  - GCP VPC with public/private subnets
  - Self-hosted PostgreSQL (with `pgvector`) on Compute Engine for metadata and vectors
  - Self-hosted Redis on Compute Engine for queues, rate-limiting and caching
  - Cloudflare R2 bucket (S3-compatible) for uploaded documents
  - Cloud Run/GKE auto-scaling FastAPI application
  - Artifact Registry for Docker images
- **Multi-provider LLM registry** (`app/providers/`)
  - Google Gemini 1.5 Flash as default free-tier model
  - OpenAI, Anthropic Claude, and optional local Ollama support
  - Per-request model selector
- **Cost & billing foundation** (`app/cost/`)
  - `CreditBalance` and `UsageLog` tables
  - Per-token cost + profit-margin pricing
  - Tier enforcement (free = Gemini Flash / Ollama only; paid = all models)
- **FastAPI integration**
  - `GET /api/ai/models` — available models for the current user tier
  - `POST /api/ai/chat` — unified chat endpoint with model selection, token logging, and credit deduction
- **R2 / S3-compatible storage module** (`app/storage/`)
  - Upload, download, and serve raw documents from Cloudflare R2
  - Local fallback for development

## Phase 2 — Async ingestion at scale
**Goal:** Move heavy OCR/chunking/embedding off the web server and onto dedicated workers.

- Replace the embedded worker with Celery/RQ workers backed by Redis.
- Add R2 pre-signed upload URLs so the web server does not proxy large files.
- Add MIG-based GPU/CPU worker autoscaling for OCR and embedding.
- Introduce document processing states and retry with dead-letter queues.

## Phase 3 — Vector search scaling
**Goal:** Scale vector search without managed vector-DB fees.

- Move from ChromaDB to `pgvector` inside the self-hosted PostgreSQL cluster.
- Separate embeddings per user/tenant and enforce collection quotas.
- Add hybrid search (BM25/keyword + vector) and result re-ranking.
- Cache common queries in Redis.

## Phase 4 — Multi-tenant monetization & operations
**Goal:** Make the platform self-service and profitable.

- Stripe/ Paddle integration for one-off credits and subscriptions.
- Admin dashboard for usage, margins, and model pricing.
- Feature flags, per-tenant quotas, and rate limits by tier.
- Security hardening: Cloud Armor/WAF, DDoS, SOC2 controls, encryption at rest/in transit.
- Observability: Prometheus/Grafana, Sentry, structured logs, distributed tracing.

---

## Profitability levers
1. **Default the free tier to Gemini Flash** — lowest API cost per token.
2. **Apply a flat markup** (`PROFIT_MARGIN`) on every paid request so each API call is profitable.
3. **Keep OCR/embedding on low-cost, spot/preemptible workers** while API servers stay stateless.
4. **Use Cloudflare R2 + CDN** for storage/bandwidth to avoid AWS egress fees.
5. **Cache extracted PDF text** so re-indexing never re-runs OCR.

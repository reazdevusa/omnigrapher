# OmniGrapher

<img src="https://raw.githubusercontent.com/reazdevusa/omnigrapher/master/omnigrapher/assets/logo/omnigrapher-logo-text.svg" alt="OmniGrapher" width="600">

> **Understand Everything. Connect Everything.**

A local-first, graph-native AI operating system for turning documents into structured knowledge, multi-step reasoning, and automated agent workflows.

OmniGrapher is the permanent home for the AI knowledge base, graph engine, multi-agent orchestration, web/desktop/CLI apps, and cloud infrastructure. It was rebuilt from the legacy `ai_knowledge_base_suite` and is now aligned to the `reazdevusa/omnigrapher` repository.

## Vision

**“Understand Everything. Connect Everything.”**

Raw information becomes useless without structure. OmniGrapher transforms documents, conversations, and data into an intelligent graph, then reasons over that graph with local and cloud language models to produce explainable, actionable answers.

## Mission

OmniGrapher turns scattered data into structured knowledge, applies intelligent reasoning, and automates workflows through a graph-first multi-agent architecture. Everything is a node. Meaning becomes structure; structure becomes intelligence; intelligence becomes automation.

## Core Philosophy

- **Everything is a node.** Documents, entities, facts, memories, and agents are nodes in a single graph.
- **Meaning → structure → intelligence → automation.**
- **Local-first, privacy-first.** Your data stays on your machine by default. Cloud is optional and explicit.

## What’s in the Repo

- **`peft_engine/`** — Standalone FastAPI PEFT engine for multi-LoRA adapter training and serving (4-bit NF4, SDPA, adapter caching)
- **`knowledge_base_pilot/`** — FastAPI + LlamaIndex + ChromaDB RAG backend with RBAC, LLM service with fallback, and Celery task orchestration
- **`web_app_nextjs/`** — Next.js 15 / React 19 web dashboard with persistent sidebar, streaming chat, and suggested questions
- **`web_app/`** — Streamlit lightweight web UI
- **`desktop_app/`** — PyQt5 desktop client
- **`omnigrapher/`** — OmniGrapher identity, agents, architecture, docs, scripts, and assets
- **`infrastructure/`** — Terraform (GCP, Cloudflare), Docker, deployment configs, GCP deployment blueprint
- **`docs/`** — Test case specification (87 manual cases), GCP deployment guide
- **`.devin/`** — Session metadata and workspace configuration

## Core Features

| Feature | Description |
|---|---|
| Document Ingestion | `.txt`, `.pdf`, `.md`, `.docx`, `.csv` → chunks → embeddings with PII redaction |
| Vector + Graph Hybrid | ChromaDB for semantic search; Kùzu-style graph engine for relationships |
| Multi-Modal Query | `/api/query` streaming and synchronous RAG endpoints |
| Multi-Agent Orchestrator | Indexer, Reasoner, Summarizer, Orchestrator, AI Studio/AEO module |
| Multi-LoRA PEFT Engine | Dedicated PEFT service for domain-specific adapters (security, indexer, reasoner) with 4-bit NF4 quantization and FlashAttention-2 |
| Resilient LLM Routing | PEFT → Ollama → cloud fallback with HTTP retries (3x), 5s connect / 60s read timeouts, and drive-disconnection guard |
| External Storage Isolation | HF_HOME, Torch cache, adapters, and temp directories redirected to external drive (G:) to protect system storage |
| RBAC & Privacy | Document-level visibility (public/private/restricted), role-based access, PII redaction, admin override |
| Three Frontends | Next.js (recommended) with persistent sidebar, Streamlit, PyQt5 desktop |
| Local LLM | Ollama + `llama3.2` / `nomic-embed-text` |
| Cloud-Ready Infra | Terraform → GCP → Cloudflare deployment pipeline |
| Auth + Admin | JWT auth, user profiles, admin health endpoints |
| Resilience | PowerShell watchdog, health checks, auto-restart |
| Test Coverage | 220 automated tests + 87 manual test cases for QA sign-off |

## Tech Stack

- **Backend:** Python 3.11, FastAPI, Uvicorn, LlamaIndex, SQLAlchemy, Celery, Redis
- **Vector / Graph:** ChromaDB, Kùzu, SQLite / PostgreSQL + pgvector
- **LLMs / Embeddings:** Ollama, Hugging Face Transformers, PEFT/LoRA, `llama3.2`, `nomic-embed-text`
- **PEFT Engine:** FastAPI, Unsloth, BitsAndBytes, FlashAttention-2, SDPA
- **Web Frontend:** React 19, Next.js 15, Tailwind CSS, TypeScript
- **Desktop:** PyQt5
- **Infra:** Docker, Docker Compose, Terraform, GCP (Cloud Run, Secret Manager, Artifact Registry), Cloudflare R2
- **Languages:** Python, TypeScript, JavaScript, PowerShell, HCL
- **Testing:** pytest, httpx, mock

## Quick Start

### 1. Start Ollama

```powershell
ollama serve
ollama pull llama3.2
ollama pull nomic-embed-text
```

If Ollama is missing or the `PATH` is broken, run the repair script:

```powershell
omnigrapher\scripts\ollama\repair-ollama.ps1
```

### 2. Start the PEFT Engine (optional)

```powershell
cd peft_engine
..\.venv\Scripts\python.exe -m uvicorn peft_engine.main:app --port 8002 --reload
```

### 3. Start the Backend

```powershell
cd knowledge_base_pilot
..\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8001 --reload
```

### 4. Start the Next.js Frontend

```powershell
cd web_app_nextjs
npm install
npm run dev -- --port 3002
```

Open `http://localhost:3002`.

### 5. Or run everything at once

```powershell
Start Knowledge Base Suite.cmd
```

## One-Click Launchers

| Action | File |
|---|---|
| Start the full stack | `Start Knowledge Base Suite.cmd` |
| Start PEFT engine only | `Start PEFT Engine.cmd` |
| Stop the full stack | `Stop Knowledge Base Suite.cmd` |
| Start with tabs | `scripts\start-all-services-tabs.ps1` |
| Stop | `scripts\stop-all-services.ps1` |
| Watchdog | `scripts\watchdog.ps1` |
| View live logs | `View Live Logs.cmd` |

## Deployment

### Local

Use the launchers or the individual service scripts.

### GCP / Cloudflare

```powershell
cd infrastructure/terraform-gcp
terraform init
terraform plan
terraform apply
```

See `infrastructure/terraform-gcp/main.tf` for the live blueprint, `infrastructure/terraform-gcp/DEPLOY.md` for deployment steps, and `docs/GCP_DEPLOYMENT_BLUEPRINT.md` for the full runbook.

**Note:** `terraform.tfvars` contains secrets and is gitignored. Copy `terraform.tfvars.example` and fill in your values before applying.

## Project Structure

```text
D:\Upwork\ai_knowledge_base_suite\  (workspace root)
│
├── .devin/                        # Devin session + workspace metadata
├── .git/                          # Git root → origin reazdevusa/omnigrapher
├── .gitignore                     # Excludes .env, models, sessions, backups, terraform.tfvars
├── peft_engine/                   # Standalone PEFT engine (multi-LoRA adapters)
├── knowledge_base_pilot/          # FastAPI RAG backend + LLM service + Celery
├── web_app/                       # Streamlit app
├── web_app_nextjs/                # Next.js app
├── desktop_app/                   # PyQt5 desktop app
├── infrastructure/                # Terraform + Docker
├── scripts/                       # PowerShell service launchers
├── docs/                          # Test case spec, GCP deployment blueprint
└── omnigrapher/                   # OmniGrapher brand, architecture, agents, backups
    ├── agents/personas/           # Agent directives
    ├── assets/logo/               # SVG logos
    ├── config/backup.yaml         # Backup configuration
    ├── docs/                      # Architecture, branding, UI, recovery
    ├── scripts/                   # Ollama, Devin, backup, diagnostics
    └── services/                  # Ollama and ChromaDB notes
```

## Configuration

| Component | File | Key variables |
|---|---|---|
| PEFT Engine | `peft_engine/.env` | `PEFT_BASE_MODEL`, `PEFT_ADAPTER_DIR`, `OMNIGRAPHER_STORAGE_BASE`, `HF_TOKEN` |
| Backend | `knowledge_base_pilot/.env` | `SECRET_KEY`, `DATABASE_URL`, `OLLAMA_BASE_URL`, `LLM_MODEL`, `EMBED_MODEL`, `PEFT_ENGINE_URL`, `USE_PEFT_ADAPTERS` |
| Next.js | `web_app_nextjs/.env.local` | `INTERNAL_API_URL` |
| Streamlit | `web_app/.env` | `API_URL`, `LOG_LEVEL` |
| Desktop | `desktop_app/.env` | `API_URL`, `LOG_LEVEL` |
| Backup | `omnigrapher/config/backup.yaml` | Local / external / GitHub / cloud targets |

**Important:** `OMNIGRAPHER_STORAGE_BASE` defaults to `G:/DO_NOT_DELETE/OmniGrapher_AI_Storage` for external-drive caching (protects C: drive). Configure via environment or let it fall back to local paths.

## Roadmap

1. **Stabilise** — Ollama, Devin, Git provider, backup automation
2. **Repackage** — Migrate services into `core/`, `agents/`, `apps/`, `services/`
3. **Graph Engine** — Add graph-node/edge CRUD and graph-based retrieval
4. **Agent Framework** — Indexer, Reasoner, Summarizer, Orchestrator run loops
5. **Cross-Platform CLI** — `omnigrapher` CLI for run, deploy, and backup
6. **Cloud Deploy** — CI/CD to GCP + Cloudflare, GitHub Actions for automated testing

## Branding

- **Tagline:** “Understand Everything. Connect Everything.”
- **Alt tagline:** “AI that thinks in graphs.”
- **Palette:** Neon Blue `#4D9FFF`, Cyber Purple `#A44DFF`, Deep Black `#0A0A0A`, Graph Cyan `#3FF0D1`, Soft Gray `#D0D0D0`
- **Assets:** `omnigrapher/assets/logo/`
- **Full brand docs:** `omnigrapher/docs/BRANDING.md`

## Testing

Run the automated test suite:

```powershell
cd knowledge_base_pilot
pytest tests/
```

**Coverage:** 220 automated tests passing (storage, resilience, RBAC, graph RAG, API integration). See `docs/TEST_CASE_SPECIFICATION.md` for 87 manual test cases for QA and client-acceptance sign-off.

## License

This project is for demonstration, research, and personal use.

---

*Rebuilt for the permanent home of everything — OmniGrapher.*

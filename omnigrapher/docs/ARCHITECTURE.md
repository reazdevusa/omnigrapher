# OmniGrapher Architecture Blueprint

## High-Level System

```text
┌────────────────────────────────────────────────────────────────────────────┐
│                              OmniGrapher                                     │
│  "Understand Everything. Connect Everything."                                │
└────────────────────────────────────────────────────────────────────────────┘

         ┌──────────┐    ┌──────────┐    ┌──────────┐
         │  Web App │    │ Desktop  │    │   CLI    │
         │ (Next.js)│    │ (PyQt5)  │    │(future)  │
         └────┬─────┘    └────┬─────┘    └────┬─────┘
              │               │               │
              └───────────────┼───────────────┘
                              │
                    ┌─────────▼──────────┐
                    │   FastAPI API      │
                    │ knowledge_base_pilot│
                    │   /api/*            │
                    └─────────┬──────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────▼─────────┐ ┌───▼────┐ ┌───────▼───────┐
    │   Core Engine     │ │ Graph  │ │ Agent         │
    │  (RAG Pipeline)   │ │ Engine │ │ Orchestrator  │
    │                   │ │        │ │               │
    │ LlamaIndex +      │ │  Kùzu  │ │ Indexer       │
    │ ChromaDB          │ │(future)│ │ Reasoner      │
    │                   │ │        │ │ Summarizer    │
    └─────────┬─────────┘ └────────┘ └───────────────┘
              │
    ┌─────────▼───────────────────────────────────────┐
    │           LLM Orchestrator                       │
    │  Ollama (llama3.2)  /  nomic-embed-text          │
    │  Local-first, replaceable with cloud endpoints    │
    └───────────────────────────────────────────────────┘
```

## Data Flow

```text
Documents
    │
    ▼
Ingestion Agent
    │   ── text extraction, chunking, metadata tagging
    ▼
Embeddings (nomic-embed-text)
    │
    ▼
ChromaDB (vector collection)
    │
    ▼
Graph Engine
    │   ── entities, relationships, confidence, sources
    ▼
Reasoner Agent
    │   ── multi-step, graph-aware chain-of-thought
    ▼
LLM Orchestrator (llama3.2)
    │
    ▼
Summarizer Agent
    │
    ▼
Output (web / desktop / CLI / API)
```

## App Flow

### Web App

```text
Browser → Next.js (React 19 + Tailwind)
                │
                ▼
        REST/Stream → FastAPI @ :8001
                │
                ▼
        Core Engine → ChromaDB → Ollama
```

### Desktop App

```text
PyQt5 Window → Local Runtime
                  │
                  ▼
          Ollama serve @ :11434
                  │
                  ▼
          FastAPI if available / direct on fallback
```

### CLI (future)

```text
omnigrapher ingest <path>
omnigrapher query "..."
omnigrapher agent run --reasoner
```

## Infra Flow

```text
Developer commit → GitHub (reazdevusa/omnigrapher)
        │
        ▼
CI / CD (GitHub Actions → Terraform)
        │
        ▼
Google Cloud Platform (Cloud Run / GKE / Compute)
        │
        ▼
Cloudflare (DNS + CDN + WAF)
        │
        ▼
Global endpoints
```

## Module Relationships

| Module | Responsibility | Current Code | Future Target |
|---|---|---|---|
| `knowledge_base_pilot/app/main.py` | FastAPI server, routers, auth, admin | current | `omnigrapher/core/api.py` |
| `knowledge_base_pilot/app/rag_engine.py` | Ingestion, embedding, query, chunking | current | `omnigrapher/core/rag_engine.py` |
| `knowledge_base_pilot/chroma_db/` | Vector persistence | current | `omnigrapher/services/chromadb/` |
| `knowledge_base_pilot/kuzu_db/` | Graph persistence | current | `omnigrapher/core/graph/` |
| `web_app_nextjs/` | React web frontend | current | `omnigrapher/apps/web/` |
| `web_app/` | Streamlit frontend | legacy | `omnigrapher/apps/web-legacy/` |
| `desktop_app/` | PyQt5 desktop | current | `omnigrapher/apps/desktop/` |
| `infrastructure/terraform-gcp/` | IaC | current | `omnigrapher/infra/terraform/` |
| `scripts/*.ps1` | Service orchestration | current | `omnigrapher/scripts/run/` |

## Technology Boundaries

- **Local data never leaves the machine unless explicitly pushed.**
- **LLM and embeddings are swappable** by changing `OLLAMA_BASE_URL` and model names.
- **The graph engine is optional at first**; the RAG pipeline works with vector search, then graph enrichment is layered in.
- **Backups are four-layer** (local, external HDD, GitHub, optional cloud) and never version model weights.

## Failure Modes

| Failure | Mitigation |
|---|---|
| Ollama not running | `repair-ollama.ps1`, watchdog restarts |
| ChromaDB corruption | `consistency-check.ps1` + rebuild from `kb` source |
| Devin session loss | `.devin/sessions` local landing zone + scheduled backup to `G:` |
| C: overflow | `OLLAMA_MODELS` moved to `D:`; backups to `G:` |
| Git provider disconnect | Manual reconnect steps in `omnigrapher/docs/devin_reconnect.md` |

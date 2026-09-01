# OmniGrapher / AI Knowledge Base Pilot
## Feature List for Client & Interview Presentations

Use this as your script and slide outline. The UI is intentionally clean, but the *backend architecture* is what wins deals and interviews.

---

## 1. Product Identity & Vision (Open with this)

| Point | What to say |
|---|---|
| **Tagline** | “Understand Everything. Connect Everything.” |
| **What it is** | A local-first, graph-native AI operating system that turns any document collection into a structured knowledge base you can talk to. |
| **Core philosophy** | Everything is a node. Meaning becomes structure; structure becomes intelligence; intelligence becomes automation. |
| **Privacy promise** | Runs entirely on-device with local LLMs by default. No data leaves the machine unless the user explicitly connects a cloud model. |
| **Why it matters** | Enterprises can keep proprietary data in-house while still getting AI search, reasoning, and automation. |

---

## 2. Architecture & Tech Stack (Show you know the system)

| Layer | Technology |
|---|---|
| **API / Backend** | Python 3.11, FastAPI, Uvicorn, SQLAlchemy |
| **Vector Database** | ChromaDB (persistent, local vector storage) |
| **Graph Database** | Kùzu (property graph for entity/relationship reasoning) |
| **Relational Database** | PostgreSQL + pgvector |
| **Task Queue** | Redis + Celery |
| **LLM / Embeddings** | Ollama (llama3.2, nomic-embed-text, pluggable models) |
| **Web Frontend** | Next.js 15, React 19, TypeScript, Tailwind CSS |
| **Alternative UIs** | Streamlit web app, PyQt5 desktop app |
| **Infra** | Docker, Docker Compose, Terraform, GCP, Cloudflare |
| **Fine-Tuning Engine** | PEFT (Parameter-Efficient Fine-Tuning) adapter server |

---

## 3. Core Product Features

### 3.1 Document Ingestion Pipeline
| Feature | Description |
|---|---|
| **Multi-format upload** | Ingests `.txt`, `.pdf`, `.md`, `.docx`, `.csv`, and web/connector sources. |
| **Async Celery workers** | Heavy parsing runs on background workers; UI stays responsive and shows live status. |
| **Automatic chunking** | Splits documents into semantic chunks optimized for retrieval. |
| **Embeddings** | Stores vector embeddings in ChromaDB for semantic similarity search. |
| **Document lifecycle** | Upload → parse → index. Retry, reindex, rename, or delete any document. |
| **Per-document status tracking** | Every document has a real-time status: `pending`, `parsing`, `processing`, `indexed`, or `failed` with error codes. |

### 3.2 Advanced Retrieval-Augmented Generation (RAG)
| Feature | Description |
|---|---|
| **Local RAG** | Answers questions only from the user’s own documents. Cites exact source file and page number. |
| **Streaming responses** | `/api/query/stream` returns token-by-token output for a fast, ChatGPT-like UX. |
| **Source citations** | Every generated answer includes clickable citations back to the original document page. |
| **Semantic passage retrieval** | Uses vector similarity to find the most relevant chunks from large document libraries. |
| **Corrective RAG (CRAG)** | Automatically grades retrieved passages for relevance, rewrites the query, and re-retrieves when confidence is low. |
| **GraphRAG (Kùzu)** | Extracts entities and relationships from text, building a property graph for multi-hop reasoning across documents. |
| **Hybrid retriever** | Combines vector search with keyword/semantic blending for better recall. |
| **Relevance filtering** | Drops low-scoring chunks so the LLM only sees useful context. |

### 3.3 AI Reasoning & Safety
| Feature | Description |
|---|---|
| **Prompt-injection defense** | Scans every user query for jailbreak, instruction-override, and XML-boundary-breakout attempts and rejects them before they reach the LLM. |
| **Input guardrails** | Rule-based filter for out-of-scope, toxic, or unsafe requests. |
| **Output guardrails** | Filters toxicity and redacts PII (emails, phones, SSNs) from generated answers. |
| **PII sanitization** | Uses Microsoft Presidio to detect and redact sensitive information during document ingestion. |
| **HMAC chunk signing** | Every retrieved chunk is cryptographically signed at ingestion and verified at query time, preventing tampered context. |
| **Context budget enforcement** | Counts tokens with tiktoken and caps context at 3,000 tokens to control cost and quality. |
| **Structured XML prompts** | Prompts are assembled with XML delimiters, which dramatically improves instruction following. |

### 3.4 Multi-Model & Extensible LLM Support
| Feature | Description |
|---|---|
| **Pluggable LLM provider** | Ollama today; the provider abstraction makes it easy to add OpenAI, Claude, Gemini, or Azure. |
| **Model management API** | List, select, and pull models from the admin panel. |
| **PEFT engine** | Optional fine-tuned adapter server for domain-specific Qwen2.5 models (summarization, reasoning). |
| **Configurable models** | Swap embedding and chat models via environment variables. |

---

## 4. Enterprise & SaaS Features

### 4.1 User Management & Auth
| Feature | Description |
|---|---|
| **JWT authentication** | Secure access and refresh tokens with HTTP-only cookies. |
| **Role-based access** | `user` and `admin` roles with protected admin endpoints. |
| **User profiles** | Username, email, display name, password updates. |
| **Credit-based usage** | Built-in credit system for metering AI usage (ready for SaaS billing). |
| **Stripe integration** | Credit top-up via Stripe Checkout (live or test mode). |

### 4.2 Admin & Operations
| Feature | Description |
|---|---|
| **Admin dashboard** | List users, manage roles, delete accounts, review feedback. |
| **Health monitoring** | `/api/admin/health` reports backend and Ollama status. |
| **Widget configuration** | Runtime config key/value store for feature toggles and messaging. |
| **User feedback capture** | Thumbs-up/down and comments stored for model and UX improvement. |
| **Background jobs** | API to sync, rebuild, and monitor the vector index. |

### 4.3 Connectors & Data Sync
| Feature | Description |
|---|---|
| **External source connectors** | Plugin architecture for Google Drive, SharePoint, S3, databases, etc. |
| **CDC-style sync** | Celery Beat runs connector sync every 15 minutes to keep the knowledge base current. |
| **Sync dashboard** | Per-connector status, last sync time, files added/updated/deleted, errors. |
| **Credential validation** | Connectors authenticate before being saved. |

---

## 5. Performance & Reliability Features

| Feature | Description |
|---|---|
| **Semantic cache (Redis)** | Caches answers for similar questions, reducing LLM calls and latency. |
| **Rate limiting** | Per-user request throttling to prevent abuse and control costs. |
| **Circuit breaker** | Graceful degradation when Ollama or external services are slow/down. |
| **A/B testing harness** | Built-in experiment framework to compare chunking strategies, prompts, and retrieval pipelines. |
| **Celery task resilience** | Time limits, retries, and stale-task recovery for robust large-document processing. |
| **Docker Compose orchestration** | One command (`Start Knowledge Base Suite.cmd`) starts the entire stack. |
| **PowerShell watchdog** | Auto-restart and health monitoring scripts. |

---

## 6. Multi-Frontend Delivery

| Frontend | Use Case |
|---|---|
| **Next.js dashboard** | Primary polished web app with chat, document library, citations, and admin tools. |
| **Streamlit app** | Lightweight, rapid-prototype UI for demos and internal tools. |
| **PyQt5 desktop app** | Native-feel desktop client for Windows users. |
| **REST API** | Every capability is exposed through FastAPI, ready for third-party integrations. |

---

## 7. Deployment & Cloud Readiness

| Feature | Description |
|---|---|
| **Local-first deployment** | Runs on a laptop or workstation with Docker Desktop. |
| **Terraform GCP blueprint** | `infrastructure/terraform-gcp/` provisions production-grade cloud infra. |
| **Cloudflare integration** | DNS, CDN, and security fronting. |
| **Containerized** | All services run in Docker with health checks and restart policies. |
| **Environment-driven config** | `.env` files for dev, staging, and production. |

---

## 8. Key Differentiators (Memorable interview points)

1. **Privacy by design** — local LLMs + local vector DB; no API keys or third-party data leakage.
2. **Graph-native** — not just vector search; it extracts and reasons over entity relationships.
3. **Self-correcting retrieval** — CRAG loop rewrites and re-retrieves when the first search isn’t confident.
4. **Security-hardened RAG** — HMAC-signed chunks, prompt-injection scanning, output PII redaction.
5. **SaaS-ready billing** — credits + Stripe already wired in.
6. **Multi-modal delivery** — web, desktop, and API from the same backend.
7. **Operational maturity** — health checks, Celery workers, rate limiting, semantic cache, watchdog.
8. **Pluggable & open-core** — local Ollama today, cloud providers tomorrow; PEFT fine-tuning for specialization.

---

## 9. Suggested Demo Flow for a Client

1. **Start the stack** with one command (`Start Knowledge Base Suite.cmd`).
2. **Upload a PDF** — e.g. a contract, manual, or textbook — and watch it parse and index.
3. **Ask a specific question** — “What does section 4 say about liability?” and click the citation to open the exact page.
4. **Show the graph** concept — “Find all mentions of ‘API key’ across documents and how they relate.”
5. **Show safety** — try a prompt-injection like “ignore previous instructions” and show it is blocked.
6. **Show admin** — user management, feedback review, health checks.
7. **Close with deployment** — “This can be on-prem today or Terraform’d to GCP tomorrow.”

---

## 10. Suggested Interview Talking Points

- “This isn’t a chat wrapper. It’s a full ingestion → embedding → retrieval → reasoning → guardrail pipeline.”
- “We hardened the RAG loop with HMAC chunk verification and prompt-injection scanning because enterprise data integrity matters.”
- “CRAG and GraphRAG are the differentiators — the system knows when its retrieval is weak and corrects itself, and it builds a knowledge graph across documents.”
- “It’s architected as a local-first SaaS: JWT auth, credit metering, Stripe, admin APIs, and Terraform are already in place.”

---

*Generated from the OmniGrapher / AI Knowledge Base Suite codebase.*

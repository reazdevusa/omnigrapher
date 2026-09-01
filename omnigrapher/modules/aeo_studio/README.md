# AEO Studio (OmniGrapher Module)

**Phase 1 — Backend Architecture** | **Phase 2 — REST API & SaaS Infrastructure** | **Phase 3 — Next.js Dashboard UI**

AEO Studio is an isolated, pluggable SEO & Answer Engine Optimization module
for OmniGrapher. It researches high-intent local keywords, grounds entities in
the existing vector/graph store, synthesizes AEO-optimized page content, and
audits the result for AEO readiness. Phase 2 adds the FastAPI layer, B2B
API-key authentication, credit metering, and WordPress publishing. Phase 3 adds
a modern Next.js dashboard at `/dashboard/aeo-studio`.

## Location

```
omnigrapher/modules/aeo_studio/
```

## Module layout

```
omnigrapher/modules/aeo_studio/
├── __init__.py                 # Public exports
├── README.md                   # This file
├── config.py                   # Environment-driven settings (AEO_* prefix)
├── database.py                 # SQLAlchemy Base, engine, session factory
├── models.py                   # ORM models
├── schemas.py                  # Pydantic request/response schemas
├── router.py                   # FastAPI router (pluggable into main app)
├── agents/                     # 4 backend agents
│   ├── base.py
│   ├── intent_entity_researcher.py       # Agent 1
│   ├── knowledge_graph_grounding.py      # Agent 2
│   ├── aeo_content_synthesizer.py          # Agent 3
│   └── fact_checker_auditor.py           # Agent 4
├── services/
│   ├── auth.py                 # API-key authentication
│   ├── credits.py              # Credit balance & deduction
│   ├── llm_client.py           # Minimal Ollama client
│   ├── graph_client.py         # ChromaDB + Kùzu adapters
│   ├── orchestrator.py         # Pipeline orchestrator + credit ledger
│   └── publisher.py            # WordPress / generic webhook publishing
└── tests/
    ├── test_module.py          # Phase 1 smoke tests
    └── test_api.py             # Phase 2 REST API tests
```

## Database schemas (Phase 1 + Phase 2)

| Table | Purpose |
|---|---|
| `aeo_projects` | Top-level AEO campaigns. |
| `aeo_jobs` | Job queue for pipeline runs (status, payloads, timing, trace). |
| `aeo_page_outputs` | Generated pages: direct answer block, content, score. |
| `aeo_json_ld` | schema.org JSON-LD payloads (LocalBusiness, Service, FAQPage, Organization, WebSite). |
| `aeo_credit_ledger` | Per-module credit consumption / top-up records. |
| `aeo_entities` | Extracted/grounded entities with source chunks. |
| `aeo_intent_keywords` | High-intent keywords and question forms. |
| `aeo_api_keys` | B2B API keys (hashed, scoped, expirable). |
| `aeo_publish_logs` | CMS/webhook publish attempts and results. |

## Agents

1. **Intent & Entity Researcher** — Finds high-intent local keywords, related
   entities, and question forms from a business description + seed keywords.
2. **Knowledge Graph Grounding** — Validates entities against ChromaDB/Kùzu,
   then emits `LocalBusiness`, `Service`, and `FAQPage` JSON-LD schemas.
3. **AEO & Content Synthesizer** — Produces structured page content with a
   40-60 word **Direct Answer Block** optimized for LLM scraping.
4. **Fact-Checker & Auditor** — Scores AEO readiness (0-100), entity coverage,
   schema completeness, and grounded claims; returns recommendations.

## REST API (Phase 2)

Base path: `/api/v1/aeo-studio`

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| POST | `/generate` | API key (`X-API-Key`) | Generate AEO pages for a new or existing project. |
| POST | `/generate/csv` | API key | Bulk generate from CSV upload. |
| GET | `/jobs/{job_id}/status` | API key | Real-time job status + agent trace. |
| GET | `/jobs/{job_id}/status/stream` | API key | Server-Sent Events stream of status updates. |
| GET | `/jobs/{job_id}/result` | API key | HTML, Markdown, JSON-LD, and audit scores. |
| POST | `/publish/wordpress` | API key | Publish a generated page to WordPress. |
| POST | `/api-keys` | API key | Create a scoped API key. |
| GET | `/api-keys` | API key | List API keys. |
| DELETE | `/api-keys/{key_id}` | API key | Revoke an API key. |
| POST | `/credits/top-up` | API key | Add credits for testing/integration. |

### Credit model

- **1 credit per page generated** (default; configurable).
- Credits are stored per `owner_id` in `aeo_credit_ledger`.
- Insufficient balance returns HTTP `402 Payment Required`.

### API key authentication

Use the `X-API-Key` header:

```bash
curl -X POST https://api.example.com/api/v1/aeo-studio/generate \
  -H "X-API-Key: aeo_..." \
  -H "Content-Type: application/json" \
  -d '{"project": {"name": "Alfa Roofing", "business_name": "Alfa Roofing LLC", "business_type": "RoofingContractor", "location": "Austin, TX"}, "page_types": ["landing", "faq"]}'
```

### WordPress publishing

```bash
curl -X POST https://api.example.com/api/v1/aeo-studio/publish/wordpress \
  -H "X-API-Key: aeo_..." \
  -H "Content-Type: application/json" \
  -d '{
    "page_output_id": 1,
    "wordpress_url": "https://myblog.example.com",
    "username": "admin",
    "application_password": "XXXX XXXX XXXX XXXX",
    "status": "draft"
  }'
```

## Usage

### Standalone in Python

```python
from omnigrapher.modules.aeo_studio import init_db, get_db
from omnigrapher.modules.aeo_studio.models import AeoProject
from omnigrapher.modules.aeo_studio.schemas import ProjectCreateRequest, RunPipelineRequest
from omnigrapher.modules.aeo_studio.services import AeoOrchestrator

init_db()

with get_db() as db:
    project = AeoProject(
        name="Alfa Roofing",
        business_name="Alfa Roofing LLC",
        business_type="RoofingContractor",
        location="Austin, TX",
        seed_keywords=["roof repair", "new roof", "emergency roofing"],
    )
    db.add(project)
    db.flush()

    orch = AeoOrchestrator(db=db)
    result = orch.run_pipeline(
        RunPipelineRequest(project_id=project.id),
        owner_id=None,
    )
    print(result)
```

### Plug into OmniGrapher FastAPI

The AEO Studio router is already wired into `knowledge_base_pilot/app/main.py`:

```python
# knowledge_base_pilot/app/main.py
from omnigrapher.modules.aeo_studio.router import get_router as get_aeo_router
app.include_router(get_aeo_router())
```

This exposes all endpoints under `/api/v1/aeo-studio` on the existing backend.

When integrating, replace `router.get_db()` with the parent app's
`app.database.get_db` dependency so AEO Studio shares the same SQLite/PostgreSQL
session and transactions.

## Environment variables

All settings use the `AEO_` prefix and fall back to parent OmniGrapher values:

| Variable | Default | Purpose |
|---|---|---|
| `AEO_DATABASE_URL` | `sqlite:///<module>/aeo_studio.db` | Relational DB |
| `AEO_CHROMA_HOST` | `localhost` | ChromaDB host |
| `AEO_CHROMA_PORT` | `8000` | ChromaDB port |
| `AEO_CHROMA_PERSISTENT_PATH` | — | Use persistent ChromaDB instead of HTTP |
| `AEO_KUZU_DB_PATH` | `<module>/kuzu_db` | Kùzu graph DB path |
| `AEO_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `AEO_OLLAMA_MODEL` | `llama3.2:latest` | Default LLM |
| `AEO_DIRECT_ANSWER_MIN_WORDS` | `40` | Minimum direct answer length |
| `AEO_DIRECT_ANSWER_MAX_WORDS` | `60` | Maximum direct answer length |
| `AEO_CREDIT_COST_PER_JOB` | `10.0` | Base job credit cost |
| `AEO_CREDIT_COST_PER_PAGE` | `2.0` | Per-page credit cost |

## Next.js Dashboard UI (Phase 3)

The dashboard lives in the existing OmniGrapher Next.js app:

```
web_app_nextjs/app/dashboard/aeo-studio/page.tsx
web_app_nextjs/components/aeo-studio-dashboard.tsx
web_app_nextjs/lib/aeo-api.ts
```

Features:

- **Input Form**: Business Name, Industry, Location, Target Service, Keywords,
  Competitor URLs, Website URL, and page-type chips (Landing, Service, FAQ,
  etc.) plus Bulk CSV upload.
- **Live Agent Progress**: Real-time progress bar + streaming agent logs
  (`[Agent 1: Extracting Entities]` → `[Agent 2: Mapping Knowledge Graph]` → ...).
- **Tabbed Preview Workspace**:
  - **Live Preview**: Rendered page with headings and direct answer block.
  - **Scorecard**: Visual AEO Readiness and entity-density badges.
  - **Code Export**: Syntax-highlighted Markdown, HTML, and JSON-LD with
    one-click copy and download.

A link is also added to the existing sidebar as **AEO Studio**.

### Run the dashboard

1. Start the backend:

```powershell
cd D:\Upwork\ai_knowledge_base_suite\knowledge_base_pilot
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

2. Seed the first API key (one-time):

```powershell
cd D:\Upwork\ai_knowledge_base_suite
.venv\Scripts\python.exe omnigrapher\modules\aeo_studio\scripts\seed_first_api_key.py
```

3. Start the Next.js frontend:

```powershell
cd D:\Upwork\ai_knowledge_base_suite\web_app_nextjs
npm run dev -- --port 3002
```

4. Open [http://localhost:3002/dashboard/aeo-studio](http://localhost:3002/dashboard/aeo-studio).

The dashboard defaults to relative `/api/v1/aeo-studio` requests, which Next.js
rewrites to the backend. Paste the API key from step 2 into the **AEO API Key**
field and top up credits to start generating.

## Verification

### Backend

```powershell
cd D:\Upwork\ai_knowledge_base_suite
.venv\Scripts\python.exe -m omnigrapher.modules.aeo_studio.tests.test_module
.venv\Scripts\python.exe -m omnigrapher.modules.aeo_studio.tests.test_api
```

### Frontend

```powershell
cd D:\Upwork\ai_knowledge_base_suite\web_app_nextjs
npx tsc --noEmit
npm run build
```

### End-to-end (requires backend + dev server running)

```powershell
cd D:\Upwork\ai_knowledge_base_suite\web_app_nextjs
$env:AEO_TEST_API_KEY="aeo_..."
npx playwright test e2e/aeo-studio.spec.ts
```

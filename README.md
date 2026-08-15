# OmniGrapher

**AI-powered local knowledge base and agentic RAG system**

OmniGrapher lets you index your own documents and chat with them privately using locally-running LLMs (via [Ollama](https://ollama.com)) and vector search (via [ChromaDB](https://www.trychroma.com)). No data ever leaves your machine.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Browser                                                 │
│  Next.js 14 (TypeScript · Tailwind CSS · App Router)     │
│  • Document upload UI                                    │
│  • Chat session management                               │
│  • Model selection                                       │
└──────────────────┬───────────────────────────────────────┘
                   │  HTTP (REST)
┌──────────────────▼───────────────────────────────────────┐
│  FastAPI backend  (Python 3.11)                          │
│  • POST /api/documents/upload  – ingest & chunk docs     │
│  • GET  /api/documents/        – list indexed docs       │
│  • POST /api/chat/             – RAG query               │
│  • GET  /api/models/           – list Ollama models      │
└─────────┬────────────────────────┬────────────────────────┘
          │                        │
┌─────────▼──────┐       ┌─────────▼──────┐
│  ChromaDB      │       │  Ollama         │
│  Vector store  │       │  LLM + Embeds   │
└────────────────┘       └─────────────────┘
```

### Key components

| Component | Technology |
|-----------|-----------|
| Frontend | Next.js 14, TypeScript, Tailwind CSS, App Router |
| Backend API | FastAPI, Pydantic v2, Uvicorn |
| Vector store | ChromaDB (HTTP client) |
| LLM / Embeddings | Ollama (local, via HTTP) |
| Document parsing | pypdf, python-docx |
| Orchestration | Docker Compose |

---

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) + Docker Compose v2
- (Optional for GPU) NVIDIA drivers + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)

### 1. Clone and configure

```bash
git clone https://github.com/reazdevusa/omnigrapher.git
cd omnigrapher
cp .env.example .env
# Edit .env if you want to change models or ports
```

### 2. Start all services

```bash
docker compose up --build
```

This will start:

| Service | Local URL |
|---------|-----------|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| ChromaDB | http://localhost:8001 |
| Ollama | http://localhost:11434 |

### 3. Pull required Ollama models

After services are running, pull the models you plan to use:

```bash
# Default LLM
docker exec -it omnigrapher_ollama ollama pull llama3

# Default embedding model
docker exec -it omnigrapher_ollama ollama pull nomic-embed-text
```

### 4. Use the app

1. Open http://localhost:3000
2. Go to the **Documents** tab and upload a PDF, DOCX, or TXT file.
3. Switch to the **Chat** tab, select a model, and start chatting.

---

## Environment Variables

All variables can be set in `.env` (root) or overridden via `docker-compose.yml`.

### Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama service URL |
| `DEFAULT_LLM_MODEL` | `llama3` | Ollama model used for chat generation |
| `DEFAULT_EMBED_MODEL` | `nomic-embed-text` | Ollama model used for embeddings |
| `CHROMA_HOST` | `chromadb` | ChromaDB host |
| `CHROMA_PORT` | `8000` | ChromaDB port (internal) |
| `CHROMA_COLLECTION` | `omnigrapher` | ChromaDB collection name |
| `CHUNK_SIZE` | `512` | Words per text chunk |
| `CHUNK_OVERLAP` | `64` | Overlapping words between chunks |
| `TOP_K` | `5` | Number of chunks retrieved per query |

### Frontend

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend API base URL |

---

## Development (without Docker)

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Make sure Ollama and ChromaDB are running locally and update `.env` accordingly.

---

## Project Structure

```
omnigrapher/
├── backend/
│   ├── app/
│   │   ├── core/config.py        # Pydantic-settings configuration
│   │   ├── models/schemas.py     # Request/response schemas
│   │   ├── routers/
│   │   │   ├── chat.py           # RAG chat endpoint
│   │   │   ├── documents.py      # Document upload/list endpoints
│   │   │   └── models.py         # Model listing endpoint
│   │   ├── services/
│   │   │   ├── embeddings.py     # Ollama embedding service
│   │   │   ├── llm.py            # Ollama LLM service
│   │   │   └── vector_store.py   # ChromaDB service
│   │   └── main.py               # FastAPI app + CORS
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── app/
│   │   ├── layout.tsx            # Root layout with navigation
│   │   ├── page.tsx              # Home redirect
│   │   ├── documents/page.tsx    # Document management UI
│   │   ├── chat/page.tsx         # Chat UI
│   │   └── globals.css           # Tailwind base styles
│   ├── components/               # Shared React components
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## License

MIT

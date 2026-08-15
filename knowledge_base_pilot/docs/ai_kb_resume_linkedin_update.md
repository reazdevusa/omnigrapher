# AI Knowledge Base Suite - Resume & LinkedIn Refresh

Use this as a source of truth for the latest updates to your resume and LinkedIn profile.

---

## Project / Experience: Enterprise AI Knowledge Base Suite

**One-line summary**
Built a production-ready, fully local Retrieval-Augmented Generation (RAG) platform in response to two high-demand AI engineering prompts: **multimodal document ingestion** and **GraphRAG / knowledge-graph reasoning**.

### Master Prompts Implemented

- **Multimodal Ingestion Pipeline Deployment**: Integrated layout-aware PDF parsing with `unstructured`, vision-model image/diagram description via Ollama `llava`, and OCR fallbacks (RapidOCR / Tesseract / PyMuPDF) to process scanned and visual documents.
- **GraphRAG & Knowledge Graph Integration**: Built an embedded Kuzu graph database that extracts entities and relationships from chunks, runs 1-hop/2-hop Cypher traversals, and generates global community summaries for cross-document, multi-hop reasoning.

### Resume Bullets

- Architected and shipped a FastAPI-backed RAG platform with Ollama-driven local LLMs, ChromaDB vector storage, and a Next.js front end, eliminating external API dependencies and recurring inference costs.
- Implemented a hybrid retrieval pipeline (dense vector + BM25 sparse + reranking) using LlamaIndex and FlashRank, with Corrective RAG (CRAG) evaluation and query rewriting to reduce hallucinations.
- Designed a multimodal document ingestion pipeline using `unstructured` for layout-aware PDF parsing, Ollama vision models for image/diagram description, and OCR fallbacks (RapidOCR / Tesseract) for scanned pages.
- Built a **GraphRAG** module on the embedded Kuzu graph database, extracting entities and relationships from chunks and enabling 1-hop/2-hop Cypher traversal plus global community summaries for cross-document reasoning.
- Hardened the ingestion layer with Presidio PII detection, input sanitization, role-based access control, Stripe billing integration, and async Celery workers backed by Redis and PostgreSQL.
- Automated service orchestration with PowerShell start/stop scripts and a watchdog, monitoring FastAPI, Celery, Ollama, Redis, PostgreSQL, and the Next.js frontend.

### Tech Stack (for Skills section)

- **Backend**: FastAPI, Uvicorn, SQLAlchemy, Alembic, PostgreSQL, Redis, Pydantic
- **AI/ML**: LlamaIndex, Ollama (Llama3, nomic-embed-text), Kuzu, `unstructured`, `rapidocr-onnxruntime`, PyMuPDF, `flashrank`
- **Vector & Graph**: ChromaDB, Kuzu (Cypher), SentenceTransformers, OpenAI-compatible embeddings
- **Infra/Automation**: Celery, Docker Compose, python-dotenv, PowerShell
- **Frontend**: Next.js / React
- **Other**: OAuth2/JWT auth, Stripe, Presidio, pytest

---

## LinkedIn "About" Section

I design and deploy production-grade AI systems that make enterprise knowledge searchable, explainable, and private. My recent work has been shaped by two high-demand AI engineering prompts: multimodal document ingestion and GraphRAG knowledge-graph reasoning.

My latest work is an end-to-end RAG platform built with FastAPI, LlamaIndex, and Ollama, supporting:
- **Multimodal document ingestion** (master prompt) with layout-aware PDF parsing, vision model image captioning, and OCR fallbacks.
- **Hybrid retrieval** that fuses dense embeddings, sparse BM25, reranking, and Corrective RAG evaluation.
- **GraphRAG** reasoning (master prompt) using an embedded Kuzu knowledge graph for entity/relationship extraction, multi-hop Cypher traversal, and global community summaries.
- **Real-world production concerns**: PII redaction with Presidio, role-based access control, Stripe billing, async Celery workers, and PowerShell service orchestration.

Everything runs locally by default, giving organizations data sovereignty while keeping the experience fast and cost-effective. I'm actively building in public on this project and open to roles in AI/ML engineering, RAG systems, and full-stack AI product development.

---

## LinkedIn Headline Options

1. AI/ML Engineer | Building Local RAG, Multimodal Document AI & GraphRAG Systems
2. Full-Stack AI Engineer | RAG • Knowledge Graphs • Multimodal Document Pipelines
3. RAG Engineer | Production AI Systems with LlamaIndex, Ollama, ChromaDB & Kuzu
4. Software Engineer | Specializing in Retrieval-Augmented Generation & AI Knowledge Bases

---

## LinkedIn "Featured" / "Projects" Post (copy-paste)

**Project: Enterprise AI Knowledge Base Suite**

I recently upgraded my local-first AI knowledge base in response to two in-demand AI engineering prompts:
- **Multimodal ingestion** that reads PDF layout, describes images/diagrams with a vision model, and falls back to OCR.
- **GraphRAG** on an embedded Kuzu graph, extracting entities and relationships from every chunk so the system can answer multi-hop and global community questions that vector search alone can't.

The stack is FastAPI + LlamaIndex + ChromaDB + Ollama + Celery + PostgreSQL + Next.js, and it's designed for data sovereignty: no required external API calls.

If you're working on RAG, document AI, or local LLM deployment, I'd love to connect and compare notes.

---

## Next Steps

Send me your existing resume (PDF, Word, or plain text) and the job titles or roles you want to target. I can:
- Tailor these bullets to specific job descriptions.
- Rewrite the LinkedIn About to match a desired voice (technical vs. product vs. leadership).
- Generate a one-page resume LaTeX/Markdown layout.

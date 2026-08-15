# Enterprise AI Knowledge Base Pilot - Resume Version

## Project Overview
Designed and implemented a production-ready, local-first AI knowledge base suite using a multi-stage Retrieval-Augmented Generation (RAG) architecture, with multimodal document understanding, knowledge-graph reasoning, and secure enterprise integrations.

## Technical Implementation
- **Backend Architecture**: Built RESTful API using FastAPI with async support, SQLAlchemy/Alembic data models, JWT/RBAC authentication, and Stripe billing integration.
- **AI/ML Core**: Implemented a hybrid RAG pipeline with LlamaIndex, dense + BM25 sparse retrieval, reranking, query rewriting, and a Corrective RAG (CRAG) evaluator to reduce hallucinations.
- **Local LLM Stack**: Integrated Ollama for on-premise inference (Llama3, nomic-embed-text), eliminating external API costs and keeping data private.
- **Multimodal Document Ingestion**: Deployed layout-aware PDF parsing with `unstructured`, Ollama vision model image/diagram description, and OCR fallbacks (RapidOCR / Tesseract / PyMuPDF).
- **GraphRAG & Knowledge Graphs**: Built an embedded Kuzu graph database workflow that extracts entities and relationships from chunks, runs 1-hop/2-hop Cypher traversals, and generates global community summaries for cross-document reasoning.
- **Vector Storage**: Configured ChromaDB for persistent vector storage with local deployment for data sovereignty.
- **Async Pipeline**: Implemented a Celery worker queue backed by Redis for asynchronous document ingestion, graph extraction, and embedding.
- **Data Privacy & Security**: Added Presidio PII detection, input sanitization, role-based access control, and environment secret management with python-dotenv.
- **Service Orchestration**: Created PowerShell start/stop/cleanup scripts and a watchdog to manage FastAPI, Celery, Ollama, Redis, PostgreSQL, and the Next.js frontend.

## Key Technologies
- **Backend**: FastAPI, Uvicorn, SQLAlchemy, Alembic, Pydantic, PostgreSQL, Redis, Celery
- **AI/ML**: LlamaIndex, Ollama (Llama3, nomic-embed-text), `unstructured`, `rapidocr-onnxruntime`, PyMuPDF, FlashRank
- **Vector & Graph**: ChromaDB, Kuzu (Cypher-compatible embedded graph DB)
- **DevOps & Automation**: Docker Compose, PowerShell, python-dotenv, pytest
- **Frontend**: Next.js / React

## Achievements
- Deployed a fully local AI system without required external API dependencies.
- Implemented multimodal, layout-aware PDF ingestion with vision model image captioning and OCR fallbacks.
- Added GraphRAG capabilities enabling entity/relationship extraction and multi-hop query resolution across documents.
- Built a CRAG evaluator and query rewriter to improve answer reliability.
- Achieved 42 passing unit/integration tests covering endpoints, sanitization, CRAG, multimodal parser, and graph extraction.

## Impact
- Enabled intelligent document retrieval and cross-document reasoning without recurring API costs.
- Provided enterprise-grade solution with data sovereignty and PII-safe processing.
- Demonstrated expertise in modern RAG architectures, local LLM deployment, multimodal parsing, and knowledge graphs.

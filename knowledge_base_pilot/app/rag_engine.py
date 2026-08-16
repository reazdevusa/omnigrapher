import concurrent.futures
import json
import logging
import mimetypes
import os
import re
import uuid
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import fitz
import numpy as np
import requests
from chromadb import HttpClient, PersistentClient
from rapidocr_onnxruntime import RapidOCR

try:
    import wordninja
except ImportError:  # pragma: no cover - graceful degradation in production
    wordninja = None  # type: ignore[assignment]

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover
    BM25Okapi = None  # type: ignore[assignment,misc]

try:
    from flashrank import Ranker, RerankRequest
except ImportError:  # pragma: no cover
    Ranker = None  # type: ignore[assignment,misc]
    RerankRequest = None  # type: ignore[assignment,misc]

# Core modern LlamaIndex imports
from llama_index.core import Document, PromptTemplate, VectorStoreIndex, SimpleDirectoryReader, StorageContext
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import MetadataMode
from llama_index.core.vector_stores import (
    MetadataFilter,
    MetadataFilters,
    FilterCondition,
    FilterOperator,
)
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.ollama import OllamaEmbedding

from app.config import get_settings
from app.database import create_db_session, ParentChunk, Document as DBDocument
from app.services.sanitizer import sanitize_and_log

def _with_timeout(fn: Callable[..., Any], *args, timeout: float = 1.5, **kwargs) -> Any:
    """Run a sync function under a strict timeout using a throwaway thread."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("Stage timed out after %.2fs: %s", timeout, fn.__name__)
            return None


# Establish absolute paths relative to this file
current_dir = Path(__file__).parent.resolve()
root_dir = current_dir.parent

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


def _sanitize_ollama_model(model: str) -> str:
    """Strip provider prefixes and ensure a tag is present."""
    if model.startswith("ollama-"):
        model = model[len("ollama-"):]
    if ":" not in model:
        model = f"{model}:latest"
    return model


LLM_MODEL = _sanitize_ollama_model(os.getenv("LLM_MODEL", "llama3.2:latest"))
LLM_FALLBACK_MODEL = _sanitize_ollama_model(os.getenv("LLM_FALLBACK_MODEL", "llama3:latest"))
EMBED_MODEL = _sanitize_ollama_model(os.getenv("EMBED_MODEL", "nomic-embed-text:latest"))

_index_cache: Optional[VectorStoreIndex] = None
_resolved_llm_model: Optional[str] = None
_ocr_engine: Optional[RapidOCR] = None
CHUNK_SIZE = int(os.getenv("RAG_CHILD_CHUNK_SIZE", "300"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHILD_CHUNK_OVERLAP", "50"))
PARENT_CHUNK_SIZE = int(os.getenv("RAG_PARENT_CHUNK_SIZE", "1200"))
PARENT_CHUNK_OVERLAP = int(os.getenv("RAG_PARENT_CHUNK_OVERLAP", "200"))
LOG_DIR = root_dir / "logs"
LOG_DIR.mkdir(exist_ok=True)
AUDIT_LOG_PATH = LOG_DIR / "document_ingestion.jsonl"
RAG_QA_PROMPT = PromptTemplate(
    "Use only the context below to answer the question. Preserve names and numerical values "
    "exactly as stated. If the question asks about chapters, sections, or topics, explicitly "
    "list the relevant chapters/sections from the context. Prefer direct statements that answer "
    "the question, and do not combine values from different scoring systems. If the context is "
    "truly insufficient, say so.\n\nContext:\n{context_str}\n\nQuestion: {query_str}\n\nAnswer:"
)
RAG_SYNTHESIS_PROMPT = PromptTemplate(
    "You are a precise document assistant. The text below is the authoritative content of the user's documents. "
    "Use it to answer the question directly and concisely. "
    "Never use phrases like 'Unfortunately, I don't see a document', 'The provided excerpts', "
    "'These random passages', 'I don't have access to the full text', or any similar meta-commentary. "
    "Do not apologize for missing information and do not speculate beyond the supplied text. "
    "If the question asks for a summary or an explanation of main concepts, output a clean, actionable summary based only on the available text. "
    "If the exact information is not present, state clearly and concisely what the document DOES cover instead of listing what is missing. "
    "Format your answer with bullet points where it helps clarity, keep it concise, and ground every point in the supplied text. "
    "Do not output raw passage excerpts unless explicitly asked. "
    "Do not include a separate source list; the sources will be appended automatically.\n\n"
    "{context_str}\n\n"
    "Question: {query_str}\n\n"
    "Answer:"
)

# Small words that wordninja may legitimately split out of longer runs.
_SHORT_WORDS = {
    "a", "i", "of", "or", "to", "in", "is", "it", "be", "as", "at", "by", "he",
    "we", "us", "me", "my", "no", "on", "so", "up", "an", "do", "go", "if", "am",
    "oh", "ok",
}


def _is_acceptable_wordninja_split(parts: list[str]) -> bool:
    return all(len(p) >= 3 or p in _SHORT_WORDS for p in parts)


def clean_text(text: str) -> str:
    """Lightweight OCR post-processor that re-injects spaces between merged words."""
    if not isinstance(text, str) or not text:
        return text

    # Common OCR digit confusions (e.g. "are1oo%valid" should be "are 100% valid")
    text = re.sub(r"(?<![0-9])1[oO]{2}(?![0-9A-Za-z])", "100", text)

    # Space around punctuation that is followed by a letter
    text = re.sub(r"([.,;:!?%])(?=[A-Za-z])", r"\1 ", text)

    # Insert spaces between letters and adjacent digits
    text = re.sub(r"([A-Za-z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([A-Za-z])", r"\1 \2", text)

    # Split an all-caps acronym (2+) when it is followed by a lowercase word
    text = re.sub(r"([A-Z]{2,})(?=[a-z])", r"\1 ", text)

    # Split a lowercase word that runs directly into an all-caps acronym at the end of a token
    text = re.sub(r"([a-z])([A-Z]{2,})(?=\s|$|[^A-Za-z])", r"\1 \2", text)

    # Split lowercase-to-uppercase word boundaries that are not the start of an acronym
    text = re.sub(r"([a-z])([A-Z])(?![A-Z])", r"\1 \2", text)

    def _clean_token(token: str) -> str:
        if wordninja is None or not token.isalpha() or token.isupper() or len(token) < 3:
            return token
        parts = wordninja.split(token.lower())
        if len(parts) > 1 and _is_acceptable_wordninja_split(parts):
            if token[0].isupper():
                parts[0] = parts[0].capitalize()
            return " ".join(parts)
        return token

    pieces = re.findall(r"[A-Za-z]+|[^A-Za-z]+", text)
    return "".join(_clean_token(p) for p in pieces)


def _format_context_for_llm(passages: list[dict], top_k: int = 5, max_chars: int = 1200, include_sources: bool = True) -> str:
    """Format retrieved passages as numbered excerpts with a source reference list.

    top_k limits how many chunks are included; max_chars truncates each chunk so
    local models are not overwhelmed by huge prompt payloads. include_sources can
    be disabled for Ollama to strip the bulky source metadata section.
    """
    selected = passages[:top_k]
    parts = [f"Excerpt [{i}]:\n{clean_text(str(p['text'])[:max_chars])}" for i, p in enumerate(selected, 1)]
    if include_sources:
        parts.append("Sources:")
        for i, p in enumerate(selected, 1):
            parts.append(f"[{i}] Source: {p['source']}, Page {p['page']}")
    return "\n\n".join(parts)


def _format_source_citations(passages: list[dict]) -> str:
    """Return a compact source citation list for appending to a synthesized answer."""
    seen = set()
    out = []
    for p in passages:
        key = (p["source"], p["page"])
        if key in seen:
            continue
        seen.add(key)
        out.append(f"[Source: {p['source']}, Page {p['page']}]")
    return "\n".join(out)


RAG_RELEVANCE_THRESHOLD = float(os.getenv("RAG_RELEVANCE_THRESHOLD", "0.35"))
RAG_DOCUMENT_RELEVANCE_THRESHOLD = float(os.getenv("RAG_DOCUMENT_RELEVANCE_THRESHOLD", "0.0"))
NO_LLM_BM25_THRESHOLD = float(os.getenv("NO_LLM_BM25_THRESHOLD", "0.0"))

RERANK_TOP_K = int(os.getenv("RAG_RERANK_TOP_K", "5"))
HYBRID_DENSE_K = int(os.getenv("RAG_HYBRID_DENSE_K", "20"))
HYBRID_SPARSE_K = int(os.getenv("RAG_HYBRID_SPARSE_K", "20"))
HYBRID_FUSION_K = int(os.getenv("RAG_HYBRID_FUSION_K", "15"))
RANKER_MODEL = os.getenv("RAG_RANKER_MODEL", "ms-marco-TinyBERT-L-2-v2")

logger = logging.getLogger(__name__)

_ranker: Optional[object] = None

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as", "and",
    "or", "it", "its", "this", "that", "these", "those", "i", "you", "he",
    "she", "we", "they", "my", "your", "his", "her", "our", "their", "what",
    "which", "who", "when", "where", "why", "how", "all", "any", "both", "each",
    "has", "have", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "must", "shall", "can", "need", "dare", "ought", "used", "than",
}
if not logger.handlers:
    handler = logging.FileHandler(LOG_DIR / "rag_engine.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
SETTINGS = get_settings()
logger.info(
    "[Context Clamped] Setting num_ctx=%d embedding_num_ctx=%d system_ram=%.1fGB vram=%s",
    SETTINGS.llm_num_ctx,
    SETTINGS.embedding_num_ctx,
    SETTINGS.system_ram_gb,
    f"{SETTINGS.vram_gb:.1f}GB" if SETTINGS.vram_gb is not None else "not detected",
)


def _audit(event: str, **payload) -> None:
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps({"event": event, **payload}, ensure_ascii=False, default=str) + "\n")


def _normalize_pdf_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _get_ocr_engine() -> RapidOCR:
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    return _ocr_engine


def _ocr_page(page) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    result, _ = _get_ocr_engine()(image)
    if not result:
        return ""
    return _normalize_pdf_text("\n".join(line[1] for line in result if line[1].strip()))


def _pdf_cache_path(file_path: Path) -> Path:
    return file_path.with_suffix(".extracted.json")


def _load_cached_pdf(file_path: Path) -> Optional[list[Document]]:
    cache_path = _pdf_cache_path(file_path)
    if not cache_path.exists():
        return None
    try:
        if cache_path.stat().st_mtime < file_path.stat().st_mtime:
            return None
        with cache_path.open("r", encoding="utf-8") as f:
            pages = json.load(f)
        if not isinstance(pages, list):
            return None
        documents = []
        for item in pages:
            if not isinstance(item, dict):
                continue
            page_num = item.get("page", 0)
            text = item.get("text", "")
            parser = item.get("parser", "cached")
            if not text:
                continue
            metadata = {
                "file_name": file_path.name,
                "file_path": str(file_path),
                "page": page_num,
                "parser": parser,
            }
            documents.append(Document(text=text, metadata=metadata))
        if documents:
            logger.info("[PDF cache] Loaded %d cached pages for %s", len(documents), file_path.name)
            return documents
    except Exception:
        logger.exception("Failed to load PDF text cache for %s", file_path.name)
    return None


def _save_pdf_cache(file_path: Path, documents: list[Document]) -> None:
    cache_path = _pdf_cache_path(file_path)
    try:
        pages = [
            {
                "page": doc.metadata.get("page", 0),
                "text": doc.text,
                "parser": doc.metadata.get("parser", "unknown"),
            }
            for doc in documents
            if doc.text
        ]
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(pages, f, ensure_ascii=False, indent=2)
        logger.info("[PDF cache] Saved %d pages for %s", len(pages), cache_path.name)
    except Exception:
        logger.exception("Failed to save PDF text cache for %s", file_path.name)


def _load_pdf(file_path: Path) -> list[Document]:
    cached = _load_cached_pdf(file_path)
    if cached is not None:
        return cached

    documents = []
    try:
        with fitz.open(file_path) as pdf:
            for page_number, page in enumerate(pdf, start=1):
                text = _normalize_pdf_text(page.get_text("text", sort=True))
                parser = "PyMuPDF"
                if len(text) < SETTINGS.ocr_min_text_chars:
                    logger.info(
                        "[OCR Triggered] Page %d in %s contained %d characters",
                        page_number,
                        file_path.name,
                        len(text),
                    )
                    try:
                        ocr_text = _ocr_page(page)
                    except Exception:
                        logger.exception("OCR failed for %s page %d", file_path.name, page_number)
                        ocr_text = ""
                    if len(ocr_text) > len(text):
                        text = ocr_text
                        parser = "PyMuPDF + RapidOCR"
                if not text:
                    logger.warning("No text extracted from %s page %d", file_path.name, page_number)
                    continue
                metadata = {
                    "file_name": file_path.name,
                    "file_path": str(file_path),
                    "page": page_number,
                    "parser": parser,
                }
                documents.append(Document(text=text, metadata=metadata))
                _audit("parsed_page", metadata=metadata, character_count=len(text), text=text)
        if documents:
            _save_pdf_cache(file_path, documents)
            logger.info("Extracted %d readable pages from %s", len(documents), file_path.name)
            return documents
    except Exception:
        logger.exception("PyMuPDF/OCR failed for %s; using LlamaIndex fallback", file_path.name)

    fallback = SimpleDirectoryReader(input_files=[str(file_path)]).load_data()
    readable_fallback = []
    for document in fallback:
        document.text = _normalize_pdf_text(document.text)
        if not document.text:
            continue
        document.metadata["parser"] = "LlamaIndex fallback"
        document.metadata.setdefault("file_name", file_path.name)
        document.metadata.setdefault("file_path", str(file_path))
        readable_fallback.append(document)
        _audit(
            "parsed_document",
            metadata=document.metadata,
            character_count=len(document.text),
            text=document.text,
        )
    if readable_fallback:
        _save_pdf_cache(file_path, readable_fallback)
    logger.info(
        "LlamaIndex fallback extracted %d readable documents from %s",
        len(readable_fallback),
        file_path.name,
    )
    return readable_fallback


def _load_documents(knowledge_base_path: Path) -> list[Document]:
    documents = []
    for file_path in sorted(path for path in knowledge_base_path.iterdir() if path.is_file()):
        if file_path.suffix.lower() == ".pdf":
            documents.extend(_load_pdf(file_path))
        else:
            loaded = SimpleDirectoryReader(input_files=[str(file_path)]).load_data()
            for document in loaded:
                document.metadata["parser"] = "LlamaIndex"
                _audit(
                    "parsed_document",
                    metadata=document.metadata,
                    character_count=len(document.text),
                    text=document.text,
                )
            documents.extend(loaded)
    return documents


def _chunk_documents(documents: list[Document]):
    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    nodes = splitter.get_nodes_from_documents(documents)
    for node in nodes:
        node.text = clean_text(node.text)
    for position, node in enumerate(nodes):
        _audit(
            "chunk",
            position=position,
            node_id=node.node_id,
            metadata=node.metadata,
            character_count=len(node.text),
            text=node.text,
        )
    logger.info("Created %d chunks from %d parsed documents", len(nodes), len(documents))
    return nodes


def _build_parent_child_nodes(documents: list[Document]) -> tuple[list[dict], list]:
    """Split documents into large parent chunks and small child chunks for retrieval."""
    parent_splitter = SentenceSplitter(
        chunk_size=PARENT_CHUNK_SIZE,
        chunk_overlap=PARENT_CHUNK_OVERLAP,
        paragraph_separator="\n\n",
    )
    child_splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        paragraph_separator="\n\n",
    )

    parent_nodes = parent_splitter.get_nodes_from_documents(documents)
    parent_chunks: list[dict] = []
    child_nodes: list = []
    for parent_node in parent_nodes:
        parent_id = uuid.uuid4().hex
        parent_node.metadata["parent_id"] = parent_id
        parent_node.metadata["chunk_type"] = "parent"
        parent_text = clean_text(parent_node.text.strip())
        parent_chunks.append(
            {
                "parent_id": parent_id,
                "text": parent_text,
                "source": parent_node.metadata.get("file_name", "document"),
                "page": int(
                    parent_node.metadata.get("page_label")
                    or parent_node.metadata.get("page", 0)
                ),
            }
        )

        child_doc = Document(text=parent_node.text, metadata=parent_node.metadata)
        children = child_splitter.get_nodes_from_documents([child_doc])
        for child in children:
            child.metadata = {**parent_node.metadata, **child.metadata}
            child.metadata["parent_id"] = parent_id
            child.metadata["chunk_type"] = "child"
            child.text = clean_text(child.text.strip())
            child_nodes.append(child)

    logger.info(
        "Created %d parent chunks and %d child chunks from %d documents",
        len(parent_chunks),
        len(child_nodes),
        len(documents),
    )
    for position, node in enumerate(child_nodes):
        _audit(
            "child_chunk",
            position=position,
            node_id=getattr(node, "node_id", None),
            metadata=node.metadata,
            character_count=len(node.text),
            text=node.text,
        )
    return parent_chunks, child_nodes


def _resolve_llm_model() -> str:
    global _resolved_llm_model
    if _resolved_llm_model is not None:
        return _resolved_llm_model

    for model_name in dict.fromkeys((LLM_MODEL, LLM_FALLBACK_MODEL)):
        try:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/show",
                json={"model": model_name},
                timeout=5,
            )
            if response.ok:
                capabilities = response.json().get("capabilities", [])
                if "completion" in capabilities:
                    _resolved_llm_model = model_name
                    if model_name != LLM_MODEL:
                        logger.warning(
                            "Configured model %s cannot generate; using local fallback %s",
                            LLM_MODEL,
                            model_name,
                        )
                    return model_name
        except Exception:
            logger.warning("Ollama is not reachable at %s for model resolution; using configured %s", OLLAMA_BASE_URL, LLM_MODEL)
            _resolved_llm_model = LLM_MODEL
            return LLM_MODEL

    _resolved_llm_model = LLM_MODEL
    return LLM_MODEL


def _get_llm() -> Ollama:
    return Ollama(
        model=_resolve_llm_model(),
        base_url=OLLAMA_BASE_URL,
        context_window=SETTINGS.llm_num_ctx,
        additional_kwargs={"num_ctx": SETTINGS.llm_num_ctx},
        request_timeout=300.0,
    )


def _get_embed_model() -> OllamaEmbedding:
    return OllamaEmbedding(
        model_name=EMBED_MODEL,
        base_url=OLLAMA_BASE_URL,
        embed_batch_size=15,
        ollama_additional_kwargs={"num_ctx": SETTINGS.embedding_num_ctx},
        client_kwargs={"timeout": 120.0},
    )


_chroma_client: Optional[PersistentClient] = None


def get_chroma_client():
    """Initialize and cache the configured ChromaDB client."""
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client

    chroma_host = os.getenv("CHROMA_HOST")
    if chroma_host:
        _chroma_client = HttpClient(
            host=chroma_host,
            port=int(os.getenv("CHROMA_PORT", "8002")),
            ssl=os.getenv("CHROMA_SSL", "false").lower() == "true",
        )
    else:
        chroma_path = root_dir / "chroma_db"
        chroma_path.mkdir(exist_ok=True)
        _chroma_client = PersistentClient(path=str(chroma_path))
    return _chroma_client


def _get_knowledge_base_collection():
    """Get or create the knowledge_base collection.

    ChromaDB 0.6.3's get_or_create_collection can throw KeyError('_type') on
    existing collections. Use get_collection first and create with metadata if
    the collection is missing.
    """
    client = get_chroma_client()
    try:
        return client.get_collection("knowledge_base")
    except Exception:
        return client.create_collection(
            "knowledge_base",
            metadata={"_type": "Node", "description": "Knowledge base document chunks"},
        )


def _get_knowledge_base_path() -> Path:
    return root_dir / "knowledge_base"


def _build_index(force: bool = False) -> Optional[VectorStoreIndex]:
    """Load the worker-managed vector index without processing source files."""
    chroma_collection = _get_knowledge_base_collection()
    if chroma_collection.count() == 0:
        return None
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    return VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        embed_model=_get_embed_model(),
    )


_index_cache_count: Optional[int] = None


def _get_index() -> Optional[VectorStoreIndex]:
    """Return a cached index, rebuilding it when the Chroma collection changes."""
    global _index_cache, _index_cache_count
    collection = _get_knowledge_base_collection()
    count = collection.count()
    if _index_cache is None or _index_cache_count != count:
        _index_cache = _build_index()
        _index_cache_count = count
    return _index_cache


def refresh_index() -> Optional[VectorStoreIndex]:
    """Refresh the query view of the worker-managed vector index."""
    global _index_cache
    _index_cache = _build_index(force=True)
    return _index_cache


def _tokenize_for_bm25(text: str) -> list[str]:
    tokens = re.findall(r"\b\w+\b", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _normalize_id(value: Any) -> str:
    """Normalize an owner/user id for comparison, accepting int/str/float/None."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _is_allowed_role(metadata: dict, user_role: Optional[str]) -> bool:
    """Return True if the chunk explicitly grants access to the user's role.

    Checks both the legacy ``allowed_roles`` list and the indexed ``role_*``
    boolean keys used for ChromaDB pre-filtering.
    """
    if not user_role:
        return False
    if metadata.get(f"role_{user_role}") is True:
        return True
    allowed_roles = metadata.get("allowed_roles") or []
    if isinstance(allowed_roles, str):
        allowed_roles = [r.strip() for r in allowed_roles.split(",") if r.strip()]
    return user_role in allowed_roles


def _can_access_chunk(
    metadata: dict,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
) -> bool:
    """Return True if the chunk is accessible to the requesting user.

    Access is granted when any of the following is true:
      * the caller is an admin
      * the chunk is owned by the user
      * the chunk is public
      * the chunk explicitly allows the user's role
    """
    if is_admin:
        return True
    visibility = metadata.get("visibility", "private")
    if visibility == "public":
        return True
    if user_id is not None and _normalize_id(metadata.get("owner_id")) == _normalize_id(user_id):
        return True
    if _is_allowed_role(metadata, user_role):
        return True
    return False


def _chroma_passages(
    source: Optional[str],
    scope: str,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
    limit: int = 10000,
    where_document: Optional[dict] = None,
) -> list[dict]:
    collection = _get_knowledge_base_collection()
    where_clauses: list[dict] = []

    # Source scope is a safe, deterministic filter we can push to ChromaDB.
    if scope == "single" and source:
        where_clauses.append({"file_name": source})

    # Push the bulk of the auth filter to ChromaDB for performance, but still
    # run the same logic in Python as a correctness back-stop.  We use per-role
    # boolean keys (e.g. ``role_user``) because ChromaDB does not support
    # ``$contains`` on metadata lists.
    if not is_admin:
        if user_id is not None:
            auth_or = [
                {"owner_id": user_id},
                {"visibility": "public"},
            ]
            if user_role:
                auth_or.append({f"role_{user_role}": True})
            where_clauses.append({"$or": auth_or})
        else:
            # Unauthenticated users may only see public chunks.
            where_clauses.append({"visibility": "public"})

    where = None
    if len(where_clauses) == 1:
        where = where_clauses[0]
    elif len(where_clauses) > 1:
        where = {"$and": where_clauses}

    try:
        results = collection.get(
            where=where,
            where_document=where_document,
            include=["documents", "metadatas"],
            limit=limit,
        )
    except Exception:
        logger.exception("Failed to fetch BM25 corpus from ChromaDB")
        return []

    passages = []
    for chunk_id, text, metadata in zip(
        results.get("ids", []),
        results.get("documents", []),
        results.get("metadatas", []),
    ):
        if metadata is None:
            metadata = {}
        if not _can_access_chunk(metadata, user_id, user_role, is_admin):
            continue
        passages.append(
            {
                "chunk_id": str(chunk_id),
                "source": metadata.get("file_name", "document"),
                "page": int(metadata.get("page_label") or metadata.get("page", 0)),
                "text": clean_text(str(text).strip()),
                "score": 0.0,
                "parent_id": metadata.get("parent_id"),
            }
        )
    return passages


def _dense_candidates(
    query_text: str,
    source: Optional[str],
    scope: str,
    top_k: int,
    score_threshold: float,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
) -> list[dict]:
    index = _get_index()
    if index is None:
        return []

    # Prefetch more candidates so Python-side filters (owner, visibility,
    # allowed_roles) don't starve the final top_k.
    retriever = index.as_retriever(similarity_top_k=max(top_k * 10, 50))
    all_nodes = retriever.retrieve(query_text)
    nodes = []
    for node in all_nodes:
        if score_threshold and float(getattr(node, "score", 0.0) or 0.0) < score_threshold:
            continue
        if scope == "single" and source and node.metadata.get("file_name") != source:
            continue
        if not _can_access_chunk(node.metadata, user_id, user_role, is_admin):
            continue
        nodes.append(node)
        if len(nodes) >= top_k:
            break
    if score_threshold:
        nodes = [n for n in nodes if getattr(n, "score", 1.0) >= score_threshold]

    return [
        {
            "chunk_id": str(getattr(node, "id_", "")),
            "source": node.metadata.get("file_name", "document"),
            "page": int(node.metadata.get("page_label") or node.metadata.get("page", 0)),
            "text": clean_text(
                node.get_content().strip()
                if hasattr(node, "get_content")
                else str(node.text).strip()
            ),
            "score": float(getattr(node, "score", 0.0) or 0.0),
            "parent_id": node.metadata.get("parent_id"),
            "dense_rank": rank,
        }
        for rank, node in enumerate(nodes, start=1)
    ]


def _bm25_candidates(
    query_text: str,
    source: Optional[str],
    scope: str,
    top_k: int,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
    limit: int = 10000,
    pre_filter: bool = False,
) -> list[dict]:
    if BM25Okapi is None:
        return []

    tokenized_query = _tokenize_for_bm25(query_text)
    if not tokenized_query:
        tokenized_query = [t for t in query_text.lower().split() if t and t not in _STOPWORDS]

    where_document = None
    if pre_filter and tokenized_query:
        if len(tokenized_query) == 1:
            where_document = {"$contains": tokenized_query[0]}
        else:
            where_document = {"$or": [{"$contains": t} for t in tokenized_query]}

    passages = _chroma_passages(
        source=source,
        scope=scope,
        user_id=user_id,
        user_role=user_role,
        is_admin=is_admin,
        limit=limit,
        where_document=where_document,
    )
    if not passages:
        return []

    tokenized_corpus = [_tokenize_for_bm25(p["text"]) for p in passages]
    if not any(tokenized_corpus):
        return []

    tokenized_query = _tokenize_for_bm25(query_text)
    if not tokenized_query:
        tokenized_query = query_text.lower().split()

    try:
        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(tokenized_query)
    except Exception:
        logger.exception("BM25 scoring failed")
        return []

    ranked_indices = np.argsort(scores)[::-1]
    results = []
    for rank, idx in enumerate(ranked_indices[:top_k], start=1):
        passage = passages[idx].copy()
        passage["score"] = float(scores[idx])
        passage["sparse_rank"] = rank
        results.append(passage)
    return results


def _rrf_fuse(
    dense: list[dict],
    sparse: list[dict],
    k: int = 60,
) -> list[dict]:
    dense_by_id = {p["chunk_id"]: (rank, p) for rank, p in enumerate(dense, start=1)}
    sparse_by_id = {p["chunk_id"]: (rank, p) for rank, p in enumerate(sparse, start=1)}
    worst_rank = max(len(dense), len(sparse)) + 1

    fused = []
    for chunk_id in set(dense_by_id) | set(sparse_by_id):
        dense_rank, dense_passage = dense_by_id.get(chunk_id, (worst_rank, None))
        sparse_rank, sparse_passage = sparse_by_id.get(chunk_id, (worst_rank, None))
        passage = (dense_passage or sparse_passage).copy()
        passage["rrf_score"] = 1.0 / (k + dense_rank) + 1.0 / (k + sparse_rank)
        passage["dense_rank"] = dense_rank if dense_passage else None
        passage["sparse_rank"] = sparse_rank if sparse_passage else None
        fused.append(passage)
    return sorted(fused, key=lambda x: x["rrf_score"], reverse=True)


def _fetch_parent_passages(child_passages: list[dict]) -> list[dict]:
    """Map top child chunks back to their unique parent chunks for LLM context."""
    parent_ids = list(dict.fromkeys(p.get("parent_id") for p in child_passages if p.get("parent_id")))
    if not parent_ids:
        return child_passages

    db = create_db_session()
    try:
        rows = db.query(ParentChunk).filter(ParentChunk.parent_id.in_(parent_ids)).all()
        parent_map = {row.parent_id: row for row in rows}
    finally:
        db.close()

    parent_passages = []
    seen_ids: set[str] = set()
    for child in child_passages:
        parent_id = child.get("parent_id")
        if not parent_id or parent_id in seen_ids:
            continue
        parent = parent_map.get(parent_id)
        seen_ids.add(parent_id)
        if parent:
            p_source = parent.source
            p_page = parent.page
            p_text = parent.content
            p_chunk_id = parent_id
        else:
            p_source = child.get("source") or child.get("file_name", "document")
            p_page = int(child.get("page", 0))
            p_text = child.get("text", "")
            p_chunk_id = child.get("chunk_id", parent_id)
        parent_passages.append(
            {
                "chunk_id": p_chunk_id,
                "source": p_source,
                "page": p_page,
                "text": p_text,
                "rerank_score": child.get("rerank_score"),
                "rrf_score": child.get("rrf_score"),
                "dense_rank": child.get("dense_rank"),
                "sparse_rank": child.get("sparse_rank"),
                "child_chunk_id": child.get("chunk_id"),
            }
        )
    return parent_passages


def retrieve_passages(
    query_text: str,
    owner_id: Optional[int] = None,
    source: Optional[str] = None,
    scope: str = "single",
    top_k: int = 5,
    score_threshold: Optional[float] = None,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
    model: str = "default",
) -> list[dict]:
    """Retrieve the top-k matching parent chunks using hybrid search over child chunks + reranking."""
    import time

    if source:
        source = urllib.parse.unquote(source)
        if scope != "knowledge_base":
            scope = "single"

    t0 = time.perf_counter()
    threshold = score_threshold if score_threshold is not None else RAG_RELEVANCE_THRESHOLD
    if user_id is None:
        user_id = owner_id

    # When the user has opened a specific document, retrieve ALL chunks for that
    # document by sql_document_id instead of depending on keyword/embedding match.
    # This makes "From This Document" robust for any question ("Summarize",
    # "What are the key findings?", etc.) and for 1-chunk documents.
    if scope == "single" and source:
        document_id = _resolve_document_id_by_source(user_id, source)
        if document_id is not None:
            file_path = Path(source)
            child_chunks, found = _with_timeout(
                _fetch_chunks_for_file,
                file_path,
                user_id,
                document_id,
                timeout=300.0,
            )
            if found:
                parent_passages = _fetch_parent_passages(child_chunks)
                logger.info(
                    "[LATENCY] single-doc retrieve total=%.2fms document_id=%s results=%d",
                    (time.perf_counter() - t0) * 1000,
                    document_id,
                    len(parent_passages),
                )
                return parent_passages[:top_k]

    # no_llm: fast keyword-only retrieval, skip Ollama embedding and graph.
    if model == "no_llm":
        collection = _get_knowledge_base_collection()
        if collection.count() == 0:
            logger.info("[LATENCY] no_llm_retrieve total=%.2fms results=0", (time.perf_counter() - t0) * 1000)
            return []
        t1 = time.perf_counter()
        bm25 = _bm25_candidates(
            query_text, source, scope, top_k,
            user_id=user_id, user_role=user_role, is_admin=is_admin,
            limit=100,
            pre_filter=False,
        )
        t2 = time.perf_counter()
        no_llm_threshold = max(score_threshold or 0.0, NO_LLM_BM25_THRESHOLD)
        bm25 = [p for p in bm25 if p.get("score", 0.0) >= no_llm_threshold]
        parents = _fetch_parent_passages(bm25[:top_k])
        t3 = time.perf_counter()
        logger.info(
            "[LATENCY] no_llm_retrieve total=%.2fms bm25=%.2fms parents=%.2fms results=%d",
            (t3 - t0) * 1000,
            (t2 - t1) * 1000,
            (t3 - t2) * 1000,
            len(parents),
        )
        return parents[:top_k]

    t1 = time.perf_counter()
    dense = _with_timeout(
        _dense_candidates,
        query_text, source, scope, HYBRID_DENSE_K, threshold,
        user_id, user_role, is_admin,
        timeout=300.0,
    )
    if dense is None:
        dense = []
    t2 = time.perf_counter()
    sparse = _bm25_candidates(
        query_text, source, scope, HYBRID_SPARSE_K,
        user_id=user_id, user_role=user_role, is_admin=is_admin,
    )
    t3 = time.perf_counter()

    fused = _rrf_fuse(dense, sparse)
    fused = fused[:HYBRID_FUSION_K]
    t4 = time.perf_counter()

    ranked_children = _with_timeout(_rerank_passages, query_text, fused, timeout=300.0)
    if ranked_children is None:
        ranked_children = _fallback_keyword_rerank(query_text, fused)
    t5 = time.perf_counter()
    parent_passages = _fetch_parent_passages(ranked_children)
    t6 = time.perf_counter()

    try:
        from app.services import graph_rag

        if graph_rag.is_available():
            if scope == "knowledge_base" and _is_global_query(query_text):
                summary = _with_timeout(graph_rag.community_summary, query_text, timeout=300.0)
                if summary:
                    parent_passages.insert(
                        0,
                        {
                            "chunk_id": "graph_summary",
                            "source": "graph",
                            "page": 0,
                            "text": f"Graph community summary: {summary}",
                            "score": 1.0,
                        },
                    )
            existing = {p["text"] for p in parent_passages}
            graph_context = _with_timeout(graph_rag.graph_context, query_text, 3, timeout=300.0)
            if graph_context:
                for gp in graph_context:
                    if gp["text"] not in existing:
                        parent_passages.append(gp)
                        existing.add(gp["text"])
    except Exception:
        logger.exception("GraphRAG retrieval augmentation failed")

    t7 = time.perf_counter()
    logger.info(
        "[LATENCY] retrieve_passages total=%.2fms dense=%.2fms sparse=%.2fms rrf=%.2fms rerank=%.2fms parents=%.2fms graph=%.2fms results=%d",
        (t7 - t0) * 1000,
        (t2 - t1) * 1000,
        (t3 - t2) * 1000,
        (t4 - t3) * 1000,
        (t5 - t4) * 1000,
        (t6 - t5) * 1000,
        (t7 - t6) * 1000,
        len(parent_passages),
    )

    return parent_passages[:top_k]


def _is_global_query(query_text: str) -> bool:
    lower = query_text.lower()
    keywords = {
        "themes",
        "main themes",
        "overall",
        "across all",
        "across my",
        "summary",
        "summarize",
        "global",
        "communities",
        "what are the",
        "key topics",
    }
    return any(kw in lower for kw in keywords)


def _format_passages(passages: list[dict]) -> str:
    return "\n\n".join(
        f"Passage {i} — {p['source']} (page {p['page']})\n{p['text']}"
        for i, p in enumerate(passages, 1)
    )


def pure_search(
    query_text: str,
    owner_id: Optional[int] = None,
    source: Optional[str] = None,
    scope: str = "single",
    top_k: int = 5,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
) -> str:
    """Synthesize an answer from the top-k matching chunks using the local Ollama model."""
    query_text = sanitize_and_log(query_text, context="query")
    passages = retrieve_passages(
        query_text, owner_id, source, scope, top_k,
        score_threshold=RAG_DOCUMENT_RELEVANCE_THRESHOLD,
        user_id=user_id, user_role=user_role, is_admin=is_admin,
    )
    if not passages:
        return "No relevant information found in your documents."
    try:
        return "".join(_stream_rag(query_text, passages))
    except Exception as exc:
        logger.warning("no_llm synthesis failed, falling back to raw passages: %s", exc)
        return _format_passages(passages)


def query_knowledge_base(query_text: str) -> str:
    """Queries the local vector store index using the configured LLM."""
    index = _get_index()
    if index is None:
        return "The knowledge base folder is currently empty. Please add reference documents."

    query_engine = index.as_query_engine(
        llm=_get_llm(),
        similarity_top_k=10,
        text_qa_template=RAG_QA_PROMPT,
    )
    response = query_engine.query(query_text)
    return str(response)


def _get_ranker() -> Optional[object]:
    global _ranker
    if _ranker is not None or Ranker is None:
        return _ranker
    try:
        _ranker = Ranker(model_name=RANKER_MODEL)
        logger.info("Loaded FlashRank cross-encoder reranker: %s", RANKER_MODEL)
    except Exception:
        logger.exception("Failed to load FlashRank reranker")
        _ranker = None
    return _ranker


def _fallback_keyword_rerank(query_text: str, passages: list[dict]) -> list[dict]:
    query_terms = set(_tokenize_for_bm25(query_text))
    for p in passages:
        text_terms = set(_tokenize_for_bm25(p["text"]))
        overlap = len(query_terms & text_terms) / max(len(query_terms), 1) if query_terms else 0.0
        p["rerank_score"] = float(p.get("rrf_score", 0.0)) + overlap
    return sorted(passages, key=lambda x: x["rerank_score"], reverse=True)


def _rerank_passages(query_text: str, passages: list[dict]) -> list[dict]:
    """Re-rank fused passages with a lightweight cross-encoder, falling back to keyword overlap."""
    if not passages:
        return []

    ranker = _get_ranker()
    if ranker is None or RerankRequest is None:
        return _fallback_keyword_rerank(query_text, passages)

    try:
        request = RerankRequest(
            query=query_text,
            passages=[{"text": p["text"]} for p in passages],
        )
        results = ranker.rerank(request)
        score_by_text = {r["text"]: float(r["score"]) for r in results}
        for p in passages:
            p["rerank_score"] = score_by_text.get(
                p["text"], float(p.get("rrf_score", 0.0))
            )
        return sorted(passages, key=lambda x: x["rerank_score"], reverse=True)
    except Exception:
        logger.exception("Cross-encoder reranking failed; using keyword fallback")
        return _fallback_keyword_rerank(query_text, passages)


def _stream_cloud(model: str, messages: list[dict]) -> Iterable[str]:
    """Call a registered cloud/local provider and yield its response as one chunk."""
    from app.providers import get_provider, Message

    provider = get_provider(model)
    msgs = [Message(role=m["role"], content=m["content"]) for m in messages]
    try:
        response = provider.generate(
            model,
            msgs,
            temperature=0.7,
            max_tokens=1024,
        )
        if response.text:
            yield response.text
    except Exception as exc:
        raise RuntimeError(f"{provider.name} provider failed: {exc}") from exc


def _stream_rag(query_text: str, passages: list[dict], history: Optional[list[dict]] = None) -> Iterable[str]:
    """Synthesize a clean Markdown answer from retrieved passages using the local Ollama model."""
    context = _format_context_for_llm(passages, top_k=3, max_chars=1000, include_sources=False)
    system = RAG_SYNTHESIS_PROMPT.format(context_str=context, query_str=query_text)
    messages = [{"role": "system", "content": system}]
    for message in history or []:
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant", "system"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": query_text})

    try:
        with requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": _resolve_llm_model(),
                "messages": messages,
                "stream": True,
                "options": {"num_ctx": SETTINGS.llm_num_ctx},
            },
            stream=True,
            timeout=(10, 300),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                payload = json.loads(line)
                if payload.get("error"):
                    raise RuntimeError(payload["error"])
                content = payload.get("message", {}).get("content", "")
                if content:
                    yield content
                if payload.get("done"):
                    break
    except Exception as exc:
        logger.exception("Ollama streaming RAG failed: host=%s error=%s", OLLAMA_BASE_URL, exc)
        raise RuntimeError(f"Ollama streaming RAG failed: {exc}") from exc


def _stream_model(
    query_text: str,
    mode: str,
    history: Optional[list[dict]],
    source: Optional[str],
    owner_id: Optional[int],
    scope: str,
    model: str,
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
) -> Iterable[str]:
    """Route a query to the selected model/orchestrator."""
    if model != "no_llm":
        query_text = sanitize_and_log(query_text, context="query")
        if history:
            history = [
                {
                    **msg,
                    "content": sanitize_and_log(msg.get("content", ""), context="history"),
                }
                for msg in history
            ]
    is_general_knowledge = mode in ("assistant", "ask_ai_freely")
    passages: list[dict] = []
    if mode == "document":
        passages = retrieve_passages(
            query_text, owner_id, source, scope, top_k=5,
            score_threshold=RAG_DOCUMENT_RELEVANCE_THRESHOLD,
            user_id=user_id, user_role=user_role, is_admin=is_admin,
            model=model or "default",
        )
        if not passages:
            yield {
                "type": "token",
                "token": "No relevant context found in indexed documents for your query.",
            }
            return
        for p in passages:
            source = p["source"]
            page = p["page"]
            file_url = f"/documents/{urllib.parse.quote(source, safe='')}#page={page}"
            yield {
                "type": "citation",
                "document_id": p.get("chunk_id", ""),
                "file_name": source,
                "page_number": page,
                "file_url": file_url,
                "page": page,
                "chunk_id": p["chunk_id"],
                "source": source,
                "rerank_score": p.get("rerank_score"),
                "rrf_score": p.get("rrf_score"),
                "dense_rank": p.get("dense_rank"),
                "sparse_rank": p.get("sparse_rank"),
            }

    if model == "no_llm":
        # no_llm: pure direct search. Return raw passages and sources; never call an LLM.
        if not passages:
            yield {
                "type": "token",
                "token": "No relevant context found in indexed documents for your query.",
            }
            return
        body = "\n\n".join(
            f"Passage {i}:\n{p['text']}"
            for i, p in enumerate(passages, 1)
        )
        yield {
            "type": "token",
            "token": body,
        }
        return

    if model == "langgraph-agent":
        prompt = (
            "You are a LangGraph agent with access to a document knowledge base. "
            "Use the provided context to answer and cite pages inline using [Page N]. "
            "Do NOT output raw chunk text, chunk IDs, or passage numbers.\n\n"
        )
        if mode == "document":
            prompt += f"Context:\n{_format_context_for_llm(passages, top_k=5, max_chars=1500)}\n\n"
        prompt += f"Question: {query_text}"
        for chunk in _stream_cloud(model, [{"role": "system", "content": prompt}, {"role": "user", "content": query_text}]):
            yield chunk
        return

    if model != "default":
        if mode == "document":
            prompt = RAG_SYNTHESIS_PROMPT.format(context_str=_format_context_for_llm(passages, top_k=5, max_chars=1500), query_str=query_text)
            messages = [{"role": "system", "content": prompt}, {"role": "user", "content": query_text}]
        else:
            messages = list(history or [])
            messages.append({"role": "user", "content": query_text})
        for chunk in _stream_cloud(model, messages):
            yield chunk
        return

    # Default local Ollama path
    if is_general_knowledge:
        yield from _stream_assistant(query_text, history)
    else:
        yield from _stream_rag(query_text, passages, history)


def _stream_assistant(query_text: str, history: Optional[list[dict]] = None) -> Iterable[str]:
    messages = []
    for message in history or []:
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant", "system"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": query_text})

    try:
        with requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": _resolve_llm_model(),
                "messages": messages,
                "stream": True,
                "options": {"num_ctx": SETTINGS.llm_num_ctx},
            },
            stream=True,
            timeout=(10, 300),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                payload = json.loads(line)
                if payload.get("error"):
                    raise RuntimeError(payload["error"])
                content = payload.get("message", {}).get("content", "")
                if content:
                    yield content
                if payload.get("done"):
                    return
    except Exception as exc:
        logger.exception("Ollama streaming assistant failed: host=%s error=%s", OLLAMA_BASE_URL, exc)
        raise RuntimeError(f"Ollama streaming assistant failed: {exc}") from exc


def ask_ai_freely(
    query_text: str,
    history: Optional[list[dict]] = None,
) -> dict:
    """Answer a general-knowledge question with the local Ollama LLM, skipping RAG entirely."""
    query_text = sanitize_and_log(query_text, context="query")
    messages = []
    for message in history or []:
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant", "system"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": sanitize_and_log(content, context="history")})
    messages.append({"role": "user", "content": query_text})

    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": _resolve_llm_model(),
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": SETTINGS.llm_num_ctx},
        },
        timeout=(10, 60),
    )
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(data["error"])

    message = data.get("message", {}) or {}
    return {
        "text": message.get("content", "").strip(),
        "model": _resolve_llm_model(),
        "input_tokens": data.get("prompt_eval_count", 0),
        "output_tokens": data.get("eval_count", 0),
    }


def stream_query_knowledge_base(
    query_text: str,
    mode: str = "document",
    history: Optional[list[dict]] = None,
    source: Optional[str] = None,
    owner_id: Optional[int] = None,
    model: Optional[str] = "default",
    scope: str = "single",
    user_id: Optional[int] = None,
    user_role: Optional[str] = None,
    is_admin: bool = False,
) -> Iterable[str]:
    """Stream a response from the selected model/orchestrator and retrieval scope."""
    yield from _stream_model(
        query_text, mode, history, source, owner_id, scope, model or "default",
        user_id=user_id, user_role=user_role, is_admin=is_admin,
    )


def _load_document(file_path: Path) -> list[Document]:
    if file_path.suffix.lower() == ".pdf":
        use_multimodal = os.getenv("USE_MULTIMODAL_PARSER", "true").lower() in {"1", "true", "yes"}
        if use_multimodal:
            try:
                from app.services.parsers import multimodal

                if multimodal.is_multimodal_available():
                    parsed = multimodal.parse_pdf(file_path)
                    if parsed:
                        return parsed
                    logger.info("Multimodal parser returned no content for %s; using fallback", file_path.name)
            except Exception:
                logger.exception("Multimodal parser failed for %s; using PyMuPDF fallback", file_path.name)
        return _load_pdf(file_path)

    documents = SimpleDirectoryReader(input_files=[str(file_path)]).load_data()
    for document in documents:
        document.metadata["parser"] = "LlamaIndex"
    return documents


def _document_filter(owner_id: int, filename: str) -> dict:
    return {
        "$and": [
            {"owner_id": {"$eq": owner_id}},
            {"file_name": {"$eq": filename}},
        ]
    }


def delete_document_vectors(owner_id: int, filename: str) -> None:
    collection = _get_knowledge_base_collection()
    collection.delete(where=_document_filter(owner_id, filename))


def _persist_parent_chunks(document_id: int, parent_chunks: list[dict]) -> None:
    db = create_db_session()
    try:
        db.query(ParentChunk).filter(ParentChunk.document_id == document_id).delete()
        for chunk in parent_chunks:
            db.add(
                ParentChunk(
                    parent_id=chunk["parent_id"],
                    document_id=document_id,
                    content=chunk["text"],
                    source=chunk["source"],
                    page=chunk["page"],
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist parent chunks for document_id=%s", document_id)
    finally:
        db.close()


def index_document(
    file_path: Path,
    document_id: int,
    owner_id: int,
    allowed_roles: Optional[list[str]] = None,
    visibility: str = "private",
    tenant_id: Optional[str] = None,
) -> dict:
    global _index_cache
    logger.info("[Ingestion] Extracting %s", file_path.name)
    documents = _load_document(file_path)
    if not documents:
        raise ValueError("No readable text was found in the document.")

    logger.info("[Ingestion] Sanitizing %s", file_path.name)
    for position, document in enumerate(documents):
        sanitized_text = sanitize_and_log(
            document.text, context=f"{file_path.name} page {position}"
        )
        if sanitized_text != document.text:
            documents[position] = Document(text=sanitized_text, metadata=document.metadata)

    ingestion_id = uuid.uuid4().hex
    allowed_roles = allowed_roles or []
    sql_document_id = str(document_id)
    role_metadata = {f"role_{role}": True for role in allowed_roles}
    for document in documents:
        metadata = {
            # ``document_id`` is overwritten by LlamaIndex with the ref_doc_id,
            # so we also store the stable SQL id under a custom key.
            "document_id": document_id,
            "sql_document_id": sql_document_id,
            "owner_id": owner_id,
            "file_name": file_path.name,
            "ingestion_id": ingestion_id,
            "visibility": visibility,
            **role_metadata,
        }
        if allowed_roles:
            metadata["allowed_roles"] = allowed_roles
        if tenant_id is not None:
            metadata["tenant_id"] = tenant_id
        document.metadata.update(metadata)

    logger.info("[Ingestion] Parent-child chunking %s", file_path.name)
    parent_chunks, child_nodes = _build_parent_child_nodes(documents)
    if not child_nodes:
        raise ValueError("The document did not produce any indexable chunks.")

    logger.info("[Ingestion] Building knowledge graph for %s", file_path.name)
    try:
        from app.services import graph_rag

        if graph_rag.is_available():
            graph_rag.ingest_document_graph(document_id, file_path.name, parent_chunks)
        else:
            logger.info("GraphRAG not available; skipping graph extraction for %s", file_path.name)
    except Exception:
        logger.exception("Graph ingestion failed for %s; continuing with vector indexing", file_path.name)

    logger.info(
        "[Ingestion] Embedding %d child chunks from %s with %s in batches of 15",
        len(child_nodes),
        file_path.name,
        EMBED_MODEL,
    )

    # Ensure per-node metadata is set; ChromaDB's top-level filters depend on these keys.
    for node in child_nodes:
        node.metadata["document_id"] = document_id
        node.metadata["sql_document_id"] = sql_document_id
        node.metadata["owner_id"] = owner_id
        node.metadata["file_name"] = file_path.name
        node.metadata["ingestion_id"] = ingestion_id
        node.metadata["visibility"] = visibility
        node.metadata.update(role_metadata)
        if allowed_roles:
            node.metadata["allowed_roles"] = allowed_roles
        if tenant_id is not None:
            node.metadata["tenant_id"] = tenant_id

    embed_model = _get_embed_model()
    collection = _get_knowledge_base_collection()
    vector_store = ChromaVectorStore(chroma_collection=collection)
    batch_size = 15
    total_batches = (len(child_nodes) + batch_size - 1) // batch_size
    for i in range(0, len(child_nodes), batch_size):
        batch = child_nodes[i : i + batch_size]
        batch_num = (i // batch_size) + 1
        texts = [node.get_content(metadata_mode=MetadataMode.EMBED) for node in batch]
        embeddings = embed_model.get_text_embedding_batch(texts, show_progress=False)
        if len(embeddings) != len(batch):
            raise RuntimeError(
                f"Embedding batch {batch_num}/{total_batches} returned "
                f"{len(embeddings)} embeddings for {len(batch)} texts"
            )
        for node, embedding in zip(batch, embeddings):
            node.embedding = embedding
        logger.info(
            "[Ingestion] Storing batch %d/%d (%d child chunks) for %s",
            batch_num,
            total_batches,
            len(batch),
            file_path.name,
        )
        vector_store.add(batch)

    _persist_parent_chunks(document_id, parent_chunks)

    existing = collection.get(
        where=_document_filter(owner_id, file_path.name),
        include=["metadatas"],
    )
    stale_ids = [
        item_id
        for item_id, metadata in zip(existing.get("ids", []), existing.get("metadatas", []))
        if metadata and metadata.get("ingestion_id") != ingestion_id
    ]
    if stale_ids:
        collection.delete(ids=stale_ids)
    _index_cache = None
    logger.info(
        "[Ingestion] Stored %d parent chunks and %d child chunks for %s",
        len(parent_chunks),
        len(child_nodes),
        file_path.name,
    )
    return {
        "status": "indexed",
        "filename": file_path.name,
        "chunks": len(child_nodes),
        "embedding_model": EMBED_MODEL,
        "embedding_num_ctx": SETTINGS.embedding_num_ctx,
    }


def index_documents() -> dict:
    """Return the current worker-managed vector index summary."""
    index = refresh_index()
    collection = _get_knowledge_base_collection()
    chunk_count = collection.count()
    return {
        "status": "indexed" if index is not None else "empty",
        "message": f"The worker-managed index contains {chunk_count} chunks.",
        "chunks": chunk_count,
    }


def _resolve_document_id_by_source(owner_id: int, filename: str) -> Optional[int]:
    """Resolve a stable document_id from the SQL Document table."""
    try:
        db = create_db_session()
        try:
            document = (
                db.query(DBDocument)
                .filter(DBDocument.owner_id == owner_id, DBDocument.filename == filename)
                .first()
            )
            return document.id if document else None
        finally:
            db.close()
    except Exception:
        logger.exception("Failed to resolve document_id for owner=%s file=%s", owner_id, filename)
        return None


def _fetch_chunks_for_file(
    file_path: Path,
    owner_id: int,
    document_id: Optional[int] = None,
) -> tuple[list[dict], bool]:
    """Return indexed chunks for a file from ChromaDB, sorted by page/order.

    Prefer filtering by ``sql_document_id`` (the stable SQL document id) when
    available so renames and duplicate filenames across users do not break
    retrieval.  Older chunks may not have that key, so we fall back to the
    original ``file_name`` + ``owner_id`` pair.
    """
    collection = _get_knowledge_base_collection()

    def _try_where(where: dict) -> tuple[list[dict], bool]:
        try:
            data = collection.get(
                where=where,
                include=["documents", "metadatas"],
                limit=1000,
            )
        except Exception:
            logger.exception("Failed to fetch chunks for %s", file_path.name)
            return [], False

        ids = data.get("ids", [])
        docs = data.get("documents", [])
        metas = data.get("metadatas", [])
        if not ids:
            return [], False

        is_pdf = file_path.suffix.lower() == ".pdf"
        items = []
        seen_texts = set()
        for i, (chunk_id, meta, text) in enumerate(zip(ids, metas, docs)):
            if meta is None:
                meta = {}
            try:
                page = int(meta.get("page", 0))
            except (TypeError, ValueError):
                page = 0
            text = text or ""
            if is_pdf:
                text = _normalize_pdf_text(text)
            if not text or text in seen_texts:
                continue
            seen_texts.add(text)
            items.append(
                {
                    "chunk_id": chunk_id,
                    "parent_id": meta.get("parent_id"),
                    "source": file_path.name,
                    "page": page,
                    "text": text,
                    "order": i,
                }
            )
        items.sort(key=lambda x: (x["page"], x["order"]))
        return items, True

    if document_id is not None:
        # The stable SQL id is stored in a custom key to avoid colliding with
        # LlamaIndex's ``document_id`` metadata, which is the LlamaIndex ref id.
        items, found = _try_where({"sql_document_id": str(document_id)})
        if found:
            return items, found

    return _try_where(
        {
            "$and": [
                {"file_name": {"$eq": file_path.name}},
                {"owner_id": {"$eq": owner_id}},
            ]
        }
    )


def _guess_mime_type(file_path: Path) -> str:
    """Return a proper MIME type for the given file path."""
    media_type, _ = mimetypes.guess_type(str(file_path))
    if not media_type:
        suffix = file_path.suffix.lower()
        if suffix == ".txt":
            media_type = "text/plain"
        elif suffix == ".pdf":
            media_type = "application/pdf"
        else:
            media_type = "application/octet-stream"
    return media_type


def get_document_content(file_path: Path, owner_id: int) -> dict:
    """Return readable document content, preferring indexed chunks if available."""
    mime_type = _guess_mime_type(file_path)
    chunks, found = _fetch_chunks_for_file(file_path, owner_id)
    if found and chunks:
        pages = []
        current_page = None
        buffer = []
        for chunk in chunks:
            if chunk["page"] != current_page:
                if buffer:
                    pages.append({"page": current_page, "text": "\n\n".join(buffer)})
                current_page = chunk["page"]
                buffer = [chunk["text"]]
            else:
                buffer.append(chunk["text"])
        if buffer:
            pages.append({"page": current_page, "text": "\n\n".join(buffer)})
        full_text = "\n\n".join(p["text"] for p in pages)
        return {"content": full_text, "pages": pages, "type": mime_type}

    # Fallback to reading/extracting from disk if not yet indexed
    if file_path.suffix.lower() == ".pdf":
        documents = _load_pdf(file_path)
    else:
        documents = SimpleDirectoryReader(input_files=[str(file_path)]).load_data()

    if not documents:
        return {"content": "", "pages": [], "type": mime_type}

    pages = [{"page": i + 1, "text": doc.text} for i, doc in enumerate(documents)]
    full_text = "\n\n".join(doc.text for doc in documents)
    return {"content": full_text, "pages": pages, "type": mime_type}


def get_document_chunks(file_path: Path, owner_id: int, max_chunk_size: int = 500) -> list:
    """Return chunks produced by the same parser and splitter used for indexing."""
    chunks, found = _fetch_chunks_for_file(file_path, owner_id)
    if found:
        return [
            {"chunk_id": chunk["chunk_id"], "page": chunk["page"], "text": chunk["text"]}
            for chunk in chunks
        ]

    if not file_path.exists():
        return []
    if file_path.suffix.lower() == ".pdf":
        documents = _load_pdf(file_path)
    else:
        documents = SimpleDirectoryReader(input_files=[str(file_path)]).load_data()

    nodes = _chunk_documents(documents)
    return [
        {
            "chunk_id": getattr(node, "node_id", node.id_),
            "page": node.metadata.get("page", 0),
            "text": node.text,
        }
        for node in nodes
    ]
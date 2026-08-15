"""Document ingestion router."""

import io
import uuid
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.models.schemas import DocumentListItem, DocumentUploadResponse
from app.services import embeddings as embed_svc
from app.services import vector_store

router = APIRouter()


def _chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    words = text.split()
    chunks: List[str] = []
    step = max(1, chunk_size - overlap)
    for i in range(0, len(words), step):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk:
            chunks.append(chunk)
    return chunks


def _extract_text(content: bytes, filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if filename.lower().endswith(".docx"):
        import docx

        doc = docx.Document(io.BytesIO(content))
        return "\n".join(para.text for para in doc.paragraphs)
    # Plain text / markdown fallback
    return content.decode("utf-8", errors="replace")


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or "unknown"
    try:
        text = _extract_text(content, filename)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to extract text: {exc}") from exc

    chunks = _chunk_text(text, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        raise HTTPException(status_code=422, detail="Document appears to be empty or unreadable.")

    doc_id = str(uuid.uuid4())
    try:
        embedding_vectors = await embed_svc.embed_texts(chunks)
        vector_store.add_chunks(chunks, embedding_vectors, doc_id, filename)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Indexing failed: {exc}") from exc

    return DocumentUploadResponse(
        id=doc_id,
        filename=filename,
        chunk_count=len(chunks),
        message="Document indexed successfully.",
    )


@router.get("/", response_model=List[DocumentListItem])
def list_documents():
    try:
        return vector_store.list_documents()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to list documents: {exc}") from exc

"""Chat router."""

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.models.schemas import ChatRequest, ChatResponse
from app.services import embeddings as embed_svc
from app.services import llm as llm_svc
from app.services import vector_store

router = APIRouter()


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        query_embedding = await embed_svc.embed_texts([request.message])
        context_docs, sources = vector_store.query_chunks(query_embedding[0], top_k=settings.top_k)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Retrieval failed: {exc}") from exc

    try:
        answer = await llm_svc.generate(
            prompt=request.message,
            context_chunks=context_docs,
            model=request.model,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"LLM generation failed: {exc}") from exc

    return ChatResponse(
        session_id=request.session_id,
        answer=answer,
        sources=list(dict.fromkeys(sources)),  # deduplicated, order-preserving
    )

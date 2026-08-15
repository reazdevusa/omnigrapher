"""Models router – list available Ollama models."""

from typing import List

from fastapi import APIRouter, HTTPException

from app.models.schemas import ModelInfo
from app.services import llm as llm_svc

router = APIRouter()


@router.get("/", response_model=List[ModelInfo])
async def list_models():
    try:
        raw_models = await llm_svc.list_models()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not reach Ollama: {exc}") from exc

    return [
        ModelInfo(
            name=m.get("name", ""),
            size=str(m.get("size", "")),
            modified_at=m.get("modified_at", ""),
        )
        for m in raw_models
    ]

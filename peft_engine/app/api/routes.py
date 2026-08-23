"""PEFT engine API routes."""

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

from peft_engine.app.config import Settings, get_settings
from peft_engine.app.core.dataset import build_dataset

logger = logging.getLogger(__name__)

router = APIRouter(tags=["peft"])
openai_router = APIRouter(tags=["openai"])

# In-memory job store. For a production deployment this should be backed by a
# database or task queue (e.g., Celery, Redis).
_job_store: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class TrainRequest(BaseModel):
    adapter_name: str = Field(..., min_length=1, description="Name of the adapter to create")
    dataset_name: str = Field(..., min_length=1, description="JSONL dataset filename without extension")
    base_model: Optional[str] = Field(None, description="Override base model for this run")
    format: str = Field("chatml", description="Dataset format: chatml or alpaca")
    epochs: Optional[int] = Field(None, ge=1, le=20, description="Number of training epochs")
    learning_rate: Optional[float] = Field(None, gt=0, le=1e-2, description="Learning rate")
    lora_r: Optional[int] = Field(None, ge=4, le=256, description="LoRA rank")
    lora_alpha: Optional[int] = Field(None, ge=4, le=512, description="LoRA alpha")


class TrainResponse(BaseModel):
    job_id: str
    status: str
    adapter_name: str
    base_model: str
    message: str


class AdapterListResponse(BaseModel):
    adapters: List[str]


class OpenAIChatMessage(BaseModel):
    role: str
    content: str


class OpenAIChatCompletionRequest(BaseModel):
    model: str = Field(..., description="Adapter name or base model identifier")
    messages: List[OpenAIChatMessage] = Field(..., min_length=1)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(1024, ge=1, le=8192)
    stream: bool = False


class OpenAIChatCompletionChoice(BaseModel):
    index: int = 0
    message: OpenAIChatMessage
    finish_reason: str = "stop"


class OpenAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[OpenAIChatCompletionChoice]
    usage: OpenAIUsage


# ---------------------------------------------------------------------------
# Inference engine abstraction (so tests can stub it without importing Unsloth)
# ---------------------------------------------------------------------------
class _InferenceEngine:
    """Default inference engine that returns a placeholder response.

    In production this can be swapped for a real PEFT/vLLM engine by setting
    `peft_engine.app.api.routes._inference_engine` before application startup.
    """

    def generate(self, request: OpenAIChatCompletionRequest) -> dict:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": 0,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"[Adapter '{request.model}' is not loaded in this environment.]",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


_inference_engine: Any = _InferenceEngine()


def get_inference_engine() -> Any:
    return _inference_engine


# ---------------------------------------------------------------------------
# Training helpers
# ---------------------------------------------------------------------------
def _run_training_job(
    job_id: str,
    adapter_name: str,
    dataset_path: Path,
    base_model: Optional[str],
    format: str,
    overrides: Dict[str, Any],
) -> None:
    """Import Unsloth lazily and run the training job in the background."""
    from peft_engine.app.core import trainer

    settings = get_settings()
    # Build a one-off settings object with overrides if needed
    if overrides:
        settings = Settings(
            **{
                **settings.__dict__,
                **overrides,
            }
        )

    _job_store[job_id] = {
        "job_id": job_id,
        "adapter_name": adapter_name,
        "status": "running",
        "result": None,
    }
    result = trainer.train_adapter(
        adapter_name=adapter_name,
        dataset_path=dataset_path,
        base_model=base_model,
        format=format,
        settings=settings,
    )
    _job_store[job_id]["status"] = result["status"]
    _job_store[job_id]["result"] = result


def _dataset_path(dataset_name: str, settings: Settings) -> Path:
    return Path(settings.dataset_dir) / f"{dataset_name}.jsonl"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/adapters", response_model=AdapterListResponse)
def list_adapters() -> AdapterListResponse:
    """List trained adapters on disk."""
    settings = get_settings()
    adapter_dir = Path(settings.adapter_dir)
    if not adapter_dir.exists():
        return AdapterListResponse(adapters=[])
    adapters = [d.name for d in adapter_dir.iterdir() if d.is_dir()]
    return AdapterListResponse(adapters=sorted(adapters))


@router.post("/train", response_model=TrainResponse, status_code=status.HTTP_202_ACCEPTED)
def start_training(
    request: TrainRequest,
    background_tasks: BackgroundTasks,
) -> TrainResponse:
    """Queue a fine-tuning job for a new adapter."""
    settings = get_settings()
    dataset_path = _dataset_path(request.dataset_name, settings)
    if not dataset_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset not found: {dataset_path}",
        )

    try:
        build_dataset(dataset_path, format=request.format)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid dataset format: {exc}",
        ) from exc

    overrides: Dict[str, Any] = {}
    if request.epochs is not None:
        overrides["num_train_epochs"] = request.epochs
    if request.learning_rate is not None:
        overrides["learning_rate"] = request.learning_rate
    if request.lora_r is not None:
        overrides["r"] = request.lora_r
    if request.lora_alpha is not None:
        overrides["lora_alpha"] = request.lora_alpha

    job_id = f"peft-{uuid.uuid4().hex[:12]}"
    base_model = request.base_model or settings.base_model

    background_tasks.add_task(
        _run_training_job,
        job_id,
        request.adapter_name,
        dataset_path,
        request.base_model,
        request.format,
        overrides,
    )

    return TrainResponse(
        job_id=job_id,
        status="queued",
        adapter_name=request.adapter_name,
        base_model=base_model,
        message="Training job queued. Use GET /api/jobs/{job_id} to track progress.",
    )


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> dict:
    """Return the status of a training job."""
    job = _job_store.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )
    return job


@openai_router.post("/v1/chat/completions", response_model=OpenAIChatCompletionResponse)
def chat_completions(request: OpenAIChatCompletionRequest) -> OpenAIChatCompletionResponse:
    """OpenAI-compatible chat completions endpoint served by the PEFT engine."""
    engine = get_inference_engine()
    raw = engine.generate(request)
    # Coerce back to the Pydantic response model for validation
    return OpenAIChatCompletionResponse(**raw)

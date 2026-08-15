"""Chat and model-selection endpoints."""
import json
import logging
import os
import time
from typing import List, Literal
from uuid import uuid4

import requests

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.cost.tracker import (
    PROVIDER_API_KEY_ENV,
    can_use_model,
    charge_and_log,
    estimate_price,
    get_credit_balance,
    get_user_api_key,
)
from app.database import User, get_db
from app.providers import Message, get_provider
from app.providers.registry import get_model_info, list_models, user_tier_is_paid
from app.rag_engine import (
    _get_embed_model,
    _get_knowledge_base_collection,
    retrieve_passages,
)
from app.services import ab_testing
from app.services.circuit_breaker import default_circuit_breaker
from app.services.guardrails import check_input, check_output
from app.services.query_disambiguator import process_query
from app.services.semantic_cache import SemanticCache
from app.services.ui_generator import generate_ui_output

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(system|user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    model: str = "gemini-1.5-flash"
    messages: List[ChatMessage] = Field(..., min_length=1)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(1024, ge=1, le=8192)
    mode: Literal["document", "ask_ai_freely"] = "ask_ai_freely"
    stream: bool = False


class ChatResponse(BaseModel):
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    price_usd: float
    remaining_credits: float
    retries_taken: int = 0
    documents_filtered_count: int = 0
    crag_status: str = "direct"
    triad_scores: dict = Field(default_factory=dict, description="RAG Triad: groundedness, answer_relevance, context_relevance")
    structured_output: dict = Field(default_factory=dict, description="Frontend-ready structured UI blocks")
    citations: list[dict] = Field(default_factory=list, description="Retrieved source citation cards")
    cache_hit: bool = False
    needs_clarification: bool = False
    clarification_choices: list[str] = Field(default_factory=list)


def _compute_triad_scores(question: str, answer: str, documents: list[dict]) -> dict:
    """Compute RAG Triad scores synchronously; returns empty dict on failure."""
    try:
        from app.tasks.observability import compute_triad

        return compute_triad(question, answer, documents)
    except Exception:
        logger.warning("RAG Triad computation skipped", exc_info=True)
        return {}


def _get_ollama_tags() -> set[str]:
    """Return the set of model names currently pulled in the local Ollama instance."""
    base = (os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    try:
        response = requests.get(f"{base}/api/tags", timeout=10)
        if response.ok:
            names = {m.get("name", "") for m in response.json().get("models", [])}
            # Strip tag/quantization suffixes (e.g. llama3.2:latest -> llama3.2)
            return {name.split(":", 1)[0] for name in names if name}
    except requests.exceptions.ReadTimeout as exc:
        logger.warning("Ollama tags request timed out at %s: %s; returning empty model list.", base, exc)
    except requests.exceptions.RequestException as exc:
        logger.warning("Ollama is unreachable at %s: %s; returning empty model list.", base, exc)
    except Exception:
        logger.exception("Failed to fetch Ollama tags from %s", base)
    return set()


@router.get("/models")
def get_available_models(
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return the full model catalog with an allowed flag per user."""
    from app.providers.registry import MODEL_REGISTRY

    ollama_tags = _get_ollama_tags()

    no_llm = {
        "id": "no_llm",
        "provider": "search",
        "tier": "free",
        "cost_input_1k": 0.0,
        "cost_output_1k": 0.0,
        "default": False,
        "capabilities": ["search", "free"],
        "allowed": True,
        "downloaded": True,
    }

    def _ollama_name(model_id: str) -> str:
        if model_id.startswith("ollama-"):
            return model_id[len("ollama-"):]
        return model_id

    models = []
    for info in MODEL_REGISTRY.values():
        downloaded = True
        if info.provider == "ollama":
            downloaded = _ollama_name(info.id) in ollama_tags
        models.append(
            {
                "id": info.id,
                "provider": info.provider,
                "tier": info.tier,
                "cost_input_1k": info.cost_input_1k,
                "cost_output_1k": info.cost_output_1k,
                "default": info.is_default,
                "capabilities": info.capabilities,
                "allowed": can_use_model(user, info.id, db),
                "downloaded": downloaded,
            }
        )

    return [no_llm] + models


def _ollama_base_url() -> str:
    return (
        os.getenv("OLLAMA_BASE_URL")
        or os.getenv("OLLAMA_HOST")
        or "http://127.0.0.1:11434"
    ).rstrip("/")


def _ollama_pull_name(model_id: str) -> str:
    if model_id.startswith("ollama-"):
        return model_id[len("ollama-"):]
    return model_id


def _pull_ollama_model(name: str) -> None:
    """Background task that asks the local Ollama server to pull a model."""
    base = _ollama_base_url()
    try:
        with requests.post(
            f"{base}/api/pull",
            json={"name": name, "stream": True},
            stream=True,
            timeout=(10, 3600),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    status = data.get("status", "")
                    logger.info("Ollama pull %s: %s", name, status)
                except Exception:
                    pass
    except Exception:
        logger.exception("Failed to pull Ollama model %s", name)


@router.post("/models/{model_id}/pull")
def pull_ollama_model(
    model_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    """Start downloading an Ollama model in the background."""
    from app.providers.registry import get_model_info

    try:
        info = get_model_info(model_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown model")
    if info.provider != "ollama":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only Ollama models can be pulled")
    name = _ollama_pull_name(model_id)
    background_tasks.add_task(_pull_ollama_model, name)
    return {"status": "pulling", "model": name}


@router.post("/chat")
def chat(
    req: ChatRequest,
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """Run a chat completion with the selected model, tracking cost and credits."""
    from app.services.crag_workflow import HIGH_CONFIDENCE, run_crag_workflow

    t0 = time.perf_counter()
    query_text = req.messages[-1].content if req.messages else ""
    history = [{"role": m.role, "content": m.content} for m in req.messages[:-1]]

    input_check = check_input(query_text)
    t1 = time.perf_counter()
    logger.info("[LATENCY] chat_input_guard: %.2fms", (t1 - t0) * 1000)
    if not input_check["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=input_check["reason"],
        )

    # Document-mode requests run through the Corrective RAG workflow.
    if req.mode == "document":
        query_text = (req.messages[-1].content if req.messages else "").strip()

        # no_llm: pure search; bypass LLM, circuit breaker, and output guard.
        if req.model == "no_llm":
            t_search = time.perf_counter()
            passages = retrieve_passages(
                query_text,
                owner_id=user.id,
                source=None,
                scope="knowledge_base",
                top_k=5,
                user_id=user.id,
                user_role=user.role,
                is_admin=user.role == "admin",
                model=req.model,
            )
            logger.info(
                "[LATENCY] no_llm_raw_retrieval: %.2fms",
                (time.perf_counter() - t_search) * 1000,
            )
            if not passages:
                return ChatResponse(
                    text="No relevant information found in your documents.",
                    model="no_llm",
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    price_usd=0.0,
                    remaining_credits=get_credit_balance(db, user.id).credits,
                    crag_status="raw_retrieval",
                )
            summary = "\n\n".join(
                f"**{p.get('source', 'document')} (page {p.get('page', 0)})**\n{p.get('text', '')}"
                for p in passages
            )
            ui_output = generate_ui_output(
                query_text,
                "Retrieved passages",
                passages,
                intent="raw_retrieval",
            )
            return ChatResponse(
                text=summary,
                model="no_llm",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                price_usd=0.0,
                remaining_credits=get_credit_balance(db, user.id).credits,
                crag_status="raw_retrieval",
                structured_output=ui_output,
                citations=ui_output.get("citations", []),
            )

        # 1. Disambiguate the user query.
        disambiguation = process_query(query_text)
        if not disambiguation.get("clear"):
            balance = get_credit_balance(db, user.id)
            return ChatResponse(
                text=disambiguation.get("query", query_text),
                model=req.model,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                price_usd=0.0,
                remaining_credits=balance.credits,
                needs_clarification=True,
                clarification_choices=disambiguation.get("clarification_choices", []),
            )

        t2 = time.perf_counter()
        _semantic_cache = SemanticCache()
        cached = _semantic_cache.get(query_text, tenant_id=user.tenant_id)
        t3 = time.perf_counter()
        logger.info("[LATENCY] chat_semantic_cache: %.2fms", (t3 - t2) * 1000)
        if cached:
            balance = get_credit_balance(db, user.id)
            cached_response = cached["response"]
            return ChatResponse(
                text=cached_response.get("text", ""),
                model=cached_response.get("model", req.model),
                input_tokens=cached_response.get("input_tokens", 0),
                output_tokens=cached_response.get("output_tokens", 0),
                cost_usd=0.0,
                price_usd=0.0,
                remaining_credits=balance.credits,
                retries_taken=cached_response.get("retries_taken", 0),
                documents_filtered_count=cached_response.get("documents_filtered_count", 0),
                crag_status=cached_response.get("crag_status", "cache_hit"),
                triad_scores=cached_response.get("triad_scores", {}),
                structured_output=cached_response.get("structured_output", {}),
                citations=cached_response.get("citations", []),
                cache_hit=True,
            )

        if req.model == "no_llm":
            result = run_crag_workflow(
                query_text,
                owner_id=user.id,
                user_id=user.id,
                user_role=user.role,
                is_admin=user.role == "admin",
                history=history,
            )
            triad = _compute_triad_scores(query_text, result["text"], result.get("documents", []))
            context_texts = [d.get("text", "") for d in result.get("documents", [])]
            relevance = result.get("relevance_score", 0.0)
            if relevance >= HIGH_CONFIDENCE:
                cb = default_circuit_breaker.safe_generate(
                    query_text,
                    result["text"],
                    context_texts,
                    triad_scores=triad,
                )
                final_text = cb["answer"]
            else:
                # Broad or low-fit query: use the generated fallback without
                # tripping the groundedness circuit breaker.
                final_text = result["text"]
            output_check = check_output(final_text, context=context_texts)
            if not output_check["allowed"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=output_check["reason"],
                )
            ui_output = generate_ui_output(
                query_text,
                final_text,
                result.get("documents", []),
                triad_scores=triad,
                intent=disambiguation.get("intent"),
            )
            _semantic_cache.set(
                query_text,
                {
                    "text": final_text,
                    "model": "no_llm",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "retries_taken": result["retries_taken"],
                    "documents_filtered_count": result["documents_filtered_count"],
                    "crag_status": result["crag_status"],
                    "triad_scores": triad,
                    "structured_output": ui_output,
                    "citations": ui_output.get("citations", []),
                },
                tenant_id=user.tenant_id,
            )
            ab_config = ab_testing.get_experiment_config(str(user.id), "prompt_template")
            if ab_config:
                ab_testing.track_experiment_metrics(
                    "prompt_template",
                    ab_config["variant"],
                    {
                        "triad_groundedness": triad.get("groundedness"),
                        "triad_relevance": triad.get("answer_relevance"),
                        "triad_context": triad.get("context_relevance"),
                    },
                )
            balance = get_credit_balance(db, user.id)
            return ChatResponse(
                text=final_text,
                model="no_llm",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                price_usd=0.0,
                remaining_credits=balance.credits,
                retries_taken=result["retries_taken"],
                documents_filtered_count=result["documents_filtered_count"],
                crag_status=result["crag_status"],
                triad_scores=triad,
                structured_output=ui_output,
                citations=ui_output.get("citations", []),
            )

        if not can_use_model(user, req.model, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Upgrade, add credits, or configure your own API key to use this cloud model.",
            )

        info = get_model_info(req.model)
        user_key = get_user_api_key(user, info.provider)
        using_byok = bool(user_key)

        # Free local models and user BYOK do not consume system credits.
        if info.tier != "free" and not using_byok:
            estimated = estimate_price(req.messages, req.model, req.max_tokens)
            balance = get_credit_balance(db, user.id)
            if balance.credits < estimated:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail="Insufficient credits. Add credits to continue.",
                )

        provider = get_provider(req.model)

        env_var = PROVIDER_API_KEY_ENV.get(info.provider)
        previous_key = os.environ.get(env_var) if env_var else None
        if env_var and user_key:
            os.environ[env_var] = user_key

        try:
            result = run_crag_workflow(
                query_text,
                owner_id=user.id,
                user_id=user.id,
                user_role=user.role,
                is_admin=user.role == "admin",
                history=history,
                generate_fn=lambda msgs: provider.generate(
                    req.model,
                    msgs,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                ),
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("CRAG workflow failed for model %s", req.model)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"The {info.provider} provider could not complete this request. Check that the API key is configured and the model is available. Error: {exc}",
            ) from exc
        finally:
            if env_var:
                if previous_key is None:
                    os.environ.pop(env_var, None)
                else:
                    os.environ[env_var] = previous_key

        resp = result.get("llm_response")
        input_tokens = resp.input_tokens if resp else 0
        output_tokens = resp.output_tokens if resp else 0
        triad = _compute_triad_scores(query_text, result["text"], result.get("documents", []))
        context_texts = [d.get("text", "") for d in result.get("documents", [])]
        cb = default_circuit_breaker.safe_generate(
            query_text,
            result["text"],
            context_texts,
            triad_scores=triad,
        )
        final_text = cb["answer"]
        output_check = check_output(final_text, context=context_texts)
        if not output_check["allowed"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=output_check["reason"],
            )
        ui_output = generate_ui_output(
            query_text,
            final_text,
            result.get("documents", []),
            triad_scores=triad,
            intent=disambiguation.get("intent"),
        )

        if using_byok or info.tier == "free":
            _semantic_cache.set(
                query_text,
                {
                    "text": result["text"],
                    "model": req.model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "retries_taken": result["retries_taken"],
                    "documents_filtered_count": result["documents_filtered_count"],
                    "crag_status": result["crag_status"],
                    "triad_scores": triad,
                    "structured_output": ui_output,
                    "citations": ui_output.get("citations", []),
                },
                tenant_id=user.tenant_id,
            )
            balance = get_credit_balance(db, user.id)
            return ChatResponse(
                text=final_text,
                model=req.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=0.0,
                price_usd=0.0,
                remaining_credits=balance.credits,
                retries_taken=result["retries_taken"],
                documents_filtered_count=result["documents_filtered_count"],
                crag_status=result["crag_status"],
                triad_scores=triad,
                structured_output=ui_output,
                citations=ui_output.get("citations", []),
            )

        usage = charge_and_log(
            db,
            user,
            req.model,
            uuid4().hex,
            input_tokens,
            output_tokens,
            result["text"],
        )

        _semantic_cache.set(
            query_text,
            {
                "text": result["text"],
                "model": req.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "retries_taken": result["retries_taken"],
                "documents_filtered_count": result["documents_filtered_count"],
                "crag_status": result["crag_status"],
                "triad_scores": triad,
                "structured_output": ui_output,
                "citations": ui_output.get("citations", []),
            },
            tenant_id=user.tenant_id,
        )
        ab_config = ab_testing.get_experiment_config(str(user.id), "prompt_template")
        if ab_config:
            ab_testing.track_experiment_metrics(
                "prompt_template",
                ab_config["variant"],
                {
                    "triad_groundedness": triad.get("groundedness"),
                    "triad_relevance": triad.get("answer_relevance"),
                    "triad_context": triad.get("context_relevance"),
                },
            )
        return ChatResponse(
            text=final_text,
            model=req.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=usage["cost_usd"],
            price_usd=usage["price_usd"],
            remaining_credits=usage["remaining_credits"],
            retries_taken=result["retries_taken"],
            documents_filtered_count=result["documents_filtered_count"],
            crag_status=result["crag_status"],
            triad_scores=triad,
            structured_output=ui_output,
            citations=ui_output.get("citations", []),
        )

    # General-knowledge / no-retrieval path.
    if req.model == "no_llm":
        t_search = time.perf_counter()
        passages = retrieve_passages(
            query_text,
            owner_id=user.id,
            source=None,
            scope="knowledge_base",
            top_k=5,
            user_id=user.id,
            user_role=user.role,
            is_admin=user.role == "admin",
        )
        logger.info(
            "[LATENCY] no_llm_raw_retrieval: %.2fms",
            (time.perf_counter() - t_search) * 1000,
        )
        if not passages:
            return ChatResponse(
                text="No relevant information found in your documents.",
                model="no_llm",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                price_usd=0.0,
                remaining_credits=get_credit_balance(db, user.id).credits,
                crag_status="raw_retrieval",
            )
        summary = "\n\n".join(
            f"**{p.get('source', 'document')} (page {p.get('page', 0)})**\n{p.get('text', '')}"
            for p in passages
        )
        return ChatResponse(
            text=summary,
            model="no_llm",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            price_usd=0.0,
            remaining_credits=get_credit_balance(db, user.id).credits,
            crag_status="raw_retrieval",
        )

    if not can_use_model(user, req.model, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Upgrade, add credits, or configure your own API key to use this cloud model.",
        )

    info = get_model_info(req.model)
    user_key = get_user_api_key(user, info.provider)
    using_byok = bool(user_key)

    # Free local models and user BYOK do not consume system credits.
    if info.tier != "free" and not using_byok:
        estimated = estimate_price(req.messages, req.model, req.max_tokens)
        balance = get_credit_balance(db, user.id)
        if balance.credits < estimated:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Insufficient credits. Add credits to continue.",
            )

    provider = get_provider(req.model)
    messages = [Message(role=m.role, content=m.content) for m in req.messages]

    # Hybridize ask_ai_freely: always ground in the user's documents first.
    # If no relevant context is found, the system prompt falls back to general knowledge.
    if req.mode == "ask_ai_freely" and user is not None:
        # Fast, direct Chroma query: embed once, fetch the top 3 relevant chunks.
        # This avoids the heavy worker index while keeping answers grounded in the user's docs.
        passages = []
        try:
            collection = _get_knowledge_base_collection()
            where = (
                None
                if user.role == "admin"
                else {"$or": [{"owner_id": user.id}, {"visibility": "public"}]}
            )
            embed_model = _get_embed_model()
            embedding = embed_model.get_text_embedding(query_text)
            results = collection.query(
                query_embeddings=[embedding],
                n_results=3,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            documents = results.get("documents") or []
            metadatas = results.get("metadatas") or []
            if documents and metadatas:
                for doc, meta in zip(documents[0], metadatas[0]):
                    passages.append(
                        {
                            "text": doc,
                            "source": meta.get("file_name", "document"),
                            "page": meta.get("page", 0),
                        }
                    )
        except Exception:
            logger.warning("RAG retrieval failed for ask_ai_freely; falling back to general knowledge", exc_info=True)
            passages = []
        if passages:
            context = "\n\n".join(
                f"{i+1}. {p.get('source', 'document')} (page {p.get('page', 0)}):\n{p.get('text', '')}"
                for i, p in enumerate(passages)
            )
            system = (
                "You are a helpful assistant. Use the document context below if it is relevant. "
                "If the context does not contain the answer, answer from your own general knowledge. "
                "Do not say that you lack information unless the context and your knowledge are both insufficient."
                f"\n\nContext:\n{context}"
            )
        else:
            system = (
                "You are a helpful assistant. Answer the question from your own general knowledge. "
                "Be concise and clear."
            )
        messages.insert(0, Message(role="system", content=system))

    # Normalize UI string 'ollama-llama3.2' -> 'llama3.2' before handing to Ollama.
    model_name = req.model.replace("ollama-", "").strip()

    if req.stream:
        if not hasattr(provider, "generate_stream"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Streaming is not supported for this provider",
            )

        def _event_generator():
            received = False
            error = False
            try:
                for chunk in provider.generate_stream(
                    model_name,
                    messages,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                ):
                    if not chunk:
                        continue
                    received = True
                    data = json.dumps({"type": "token", "token": chunk}, ensure_ascii=False)
                    yield f"data: {data}\n\n"
            except Exception as exc:
                error = True
                data = json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False)
                yield f"data: {data}\n\n"
            if not received and not error:
                data = json.dumps({
                    "type": "error",
                    "error": "The model completed without returning any tokens.",
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"
            yield 'data: {"type":"done"}\n\n'

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    env_var = PROVIDER_API_KEY_ENV.get(info.provider)
    previous_key = os.environ.get(env_var) if env_var else None
    if env_var and user_key:
        os.environ[env_var] = user_key

    try:
        resp = provider.generate(
            model_name,
            messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Provider %s failed for model %s", info.provider, req.model)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The {info.provider} provider could not complete this request. Check that the API key is configured and the model is available. Error: {exc}",
        ) from exc
    finally:
        if env_var:
            if previous_key is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = previous_key

    if using_byok or info.tier == "free":
        output_check = check_output(resp.text)
        if not output_check["allowed"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=output_check["reason"],
            )
        filtered = output_check.get("filtered_text", resp.text)
        balance = get_credit_balance(db, user.id)
        return ChatResponse(
            text=filtered,
            model=req.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=0.0,
            price_usd=0.0,
            remaining_credits=balance.credits,
        )

    output_check = check_output(resp.text)
    if not output_check["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=output_check["reason"],
        )
    filtered = output_check.get("filtered_text", resp.text)

    usage = charge_and_log(
        db,
        user,
        req.model,
        uuid4().hex,
        resp.input_tokens,
        resp.output_tokens,
        filtered,
    )

    return ChatResponse(
        text=filtered,
        model=req.model,
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        cost_usd=usage["cost_usd"],
        price_usd=usage["price_usd"],
        remaining_credits=usage["remaining_credits"],
    )

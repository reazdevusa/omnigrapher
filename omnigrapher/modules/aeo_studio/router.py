"""FastAPI router for AEO Studio (Phase 2 API).

Prefix: ``/api/v1/aeo-studio``

Endpoints:
- POST /generate               — generate AEO pages for a project.
- POST /generate/csv          — bulk generate from CSV upload.
- GET  /jobs/{job_id}/status   — real-time job status + trace logs.
- GET  /jobs/{job_id}/result   — HTML, Markdown, JSON-LD, audit scores.
- POST /publish/wordpress     — publish a page to WordPress.
- POST /api-keys              — create API key.
- GET  /api-keys              — list API keys.
- DELETE /api-keys/{key_id}   — revoke API key.
- POST /credits/top-up        — add credits (for testing/billing integration).

The router is standalone. To integrate with OmniGrapher's main FastAPI app:

    from omnigrapher.modules.aeo_studio.router import get_router
    app.include_router(get_router())
"""

import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from .database import create_db_session
from .models import AeoApiKey, AeoJob, AeoJsonLd, AeoPageOutput, AeoProject
from .schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    BulkGenerateRow,
    BulkGenerateResponse,
    GenerateRequest,
    GenerateResponse,
    JobResponse,
    JobResultResponse,
    JobStatusResponse,
    PageOutputListResponse,
    PageOutputResponse,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    RunPipelineRequest,
    WordPressPublishRequest,
    WordPressPublishResponse,
)
from .services.auth import (
    generate_api_key,
    get_api_key_owner,
    hash_api_key,
    require_scope,
)
from .services.credits import add_credits, deduct_credits, ensure_sufficient_credits
from .services.orchestrator import AeoOrchestrator
from .services.publisher import WordPressPublishError, publish_to_wordpress

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------
def get_db():
    """Yield a module-local database session.

    The session is committed automatically when the endpoint returns
    successfully, and rolled back on any unhandled exception. Replace this with
    OmniGrapher's ``get_db`` dependency to share the parent database session and
    transaction when integrating.
    """
    db = create_db_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# HTML rendering helper
# ---------------------------------------------------------------------------
def _markdown_to_html(markdown_text: Optional[str]) -> str:
    """Convert page markdown to a simple HTML page.

    Uses the ``markdown`` package if installed; otherwise wraps the raw text
    in a ``<pre>`` tag.
    """
    text = markdown_text or ""
    try:
        import markdown as md

        body = md.markdown(text, extensions=["extra", "toc"])
    except Exception:  # pragma: no cover - fallback
        import html

        body = f"<pre>{html.escape(text)}</pre>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AEO Studio Page</title>
    <style>
        body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; max-width: 720px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
        h1 {{ border-bottom: 2px solid #eee; }}
        h2 {{ margin-top: 2rem; }}
        pre {{ background: #f6f8fa; padding: 1rem; border-radius: 6px; overflow-x: auto; }}
    </style>
</head>
<body>
    {body}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------
def get_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/aeo-studio", tags=["aeo-studio"])

    # -----------------------------------------------------------------------
    # Projects (Phase 1 endpoints, unchanged)
    # -----------------------------------------------------------------------
    @router.post("/projects", response_model=ProjectResponse)
    def create_project(request: ProjectCreateRequest, db: Session = Depends(get_db)) -> AeoProject:
        project = AeoProject(
            name=request.name,
            business_name=request.business_name,
            business_type=request.business_type,
            website_url=request.website_url,
            location=request.location,
            target_audience=request.target_audience,
            seed_keywords=request.seed_keywords,
            status="draft",
        )
        db.add(project)
        db.flush()
        db.refresh(project)
        return project

    @router.get("/projects", response_model=ProjectListResponse)
    def list_projects(
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        db: Session = Depends(get_db),
    ) -> dict:
        query = db.query(AeoProject).order_by(AeoProject.created_at.desc())
        total = query.count()
        items = query.offset(skip).limit(limit).all()
        return {"total": total, "items": items}

    @router.get("/projects/{project_id}", response_model=ProjectResponse)
    def get_project(project_id: int, db: Session = Depends(get_db)) -> AeoProject:
        project = db.query(AeoProject).filter_by(id=project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    # -----------------------------------------------------------------------
    # Generation endpoints
    # -----------------------------------------------------------------------
    def _run_generate(
        db: Session,
        owner_id: Optional[int],
        request: GenerateRequest,
    ) -> GenerateResponse:
        project: Optional[AeoProject] = None
        if request.project_id:
            project = db.query(AeoProject).filter_by(id=request.project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
        elif request.project:
            project = AeoProject(
                name=request.project.name,
                business_name=request.project.business_name,
                business_type=request.project.business_type,
                website_url=request.project.website_url,
                location=request.project.location,
                target_audience=request.project.target_audience,
                seed_keywords=request.project.seed_keywords,
                status="draft",
            )
            db.add(project)
            db.flush()
            db.refresh(project)
        else:
            raise HTTPException(status_code=400, detail="Either project_id or project must be provided")

        if not request.page_types:
            raise HTTPException(status_code=400, detail="page_types cannot be empty")

        page_count = len(request.page_types)
        total_cost = ensure_sufficient_credits(
            db, owner_id, pages=page_count, cost_per_page=1.0
        )

        # Deduct credits before running.
        deduct_credits(
            db=db,
            owner_id=owner_id,
            amount=total_cost,
            operation_type="page_charge",
            project_id=project.id,
            metadata={"endpoint": "/generate", "page_types": request.page_types},
        )

        try:
            orchestrator = AeoOrchestrator(db=db)
            result = orchestrator.run_pipeline(
                RunPipelineRequest(
                    project_id=project.id,
                    page_types=request.page_types,
                    dry_run=request.dry_run,
                ),
                owner_id=owner_id,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Generation failed for project %s", project.id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Generation failed: {exc}",
            ) from exc

        return GenerateResponse(
            job_id=result.job_id,
            project_id=result.project_id,
            status=result.status,
            message=result.message,
            credits_deducted=total_cost,
            page_outputs_created=result.page_outputs_created,
        )

    @router.post("/generate", response_model=GenerateResponse)
    def generate_pages(
        request: GenerateRequest,
        db: Session = Depends(get_db),
        owner_id: int = Depends(require_scope("generate")),
    ) -> GenerateResponse:
        return _run_generate(db, owner_id, request)

    @router.post("/generate/csv", response_model=BulkGenerateResponse)
    def generate_pages_csv(
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        owner_id: int = Depends(require_scope("generate")),
    ) -> BulkGenerateResponse:
        if not file.filename or not file.filename.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="Only CSV files are supported")

        try:
            content = file.file.read().decode("utf-8-sig")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read CSV: {exc}") from exc
        finally:
            file.file.close()

        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV has no header row")

        required = {"name", "business_name"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"CSV missing required columns: {missing}",
            )

        job_ids: List[int] = []
        project_ids: List[int] = []
        total_pages = 0
        rows: List[Dict[str, Any]] = []

        for row in reader:
            page_types = [pt.strip() for pt in str(row.get("page_types", "landing")).split(",") if pt.strip()]
            if not page_types:
                page_types = ["landing"]
            rows.append({**row, "page_types": page_types})
            total_pages += len(page_types)

        ensure_sufficient_credits(db, owner_id, pages=total_pages, cost_per_page=1.0)
        deduct_credits(
            db=db,
            owner_id=owner_id,
            amount=float(total_pages),
            operation_type="page_charge",
            metadata={"endpoint": "/generate/csv", "rows": len(rows), "pages": total_pages},
        )

        orchestrator = AeoOrchestrator(db=db)
        for row in rows:
            project = AeoProject(
                name=row["name"],
                business_name=row["business_name"],
                business_type=row.get("business_type") or None,
                website_url=row.get("website_url") or None,
                location=row.get("location") or None,
                target_audience=row.get("target_audience") or None,
                seed_keywords=[k.strip() for k in str(row.get("seed_keywords", "")).split(",") if k.strip()],
                status="draft",
            )
            db.add(project)
            db.flush()
            db.refresh(project)
            project_ids.append(project.id)

            try:
                result = orchestrator.run_pipeline(
                    RunPipelineRequest(
                        project_id=project.id,
                        page_types=row["page_types"],
                        dry_run=False,
                    ),
                    owner_id=owner_id,
                )
                job_ids.append(result.job_id)
            except Exception as exc:
                logger.exception("CSV row generation failed for project %s", project.id)
                # Continue with remaining rows but record failure.
                job = AeoJob(
                    project_id=project.id,
                    job_type="full_pipeline",
                    status="failed",
                    error_message=str(exc),
                    credit_cost=0.0,
                )
                db.add(job)
                db.flush()
                db.refresh(job)
                job_ids.append(job.id)

        return BulkGenerateResponse(
            total_jobs=len(job_ids),
            job_ids=job_ids,
            project_ids=project_ids,
            credits_deducted=float(total_pages),
            message=f"Created {len(job_ids)} jobs from CSV ({total_pages} pages requested).",
        )

    # -----------------------------------------------------------------------
    # Job status & result endpoints
    # -----------------------------------------------------------------------
    @router.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
    def get_job_status(
        job_id: int,
        db: Session = Depends(get_db),
        owner_id: int = Depends(get_api_key_owner),
    ) -> JobStatusResponse:
        job = db.query(AeoJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        payload = job.output_payload or {}
        progress = int(payload.get("progress_percent", 0)) if isinstance(payload, dict) else 0
        trace = payload.get("trace", []) if isinstance(payload, dict) else []
        return JobStatusResponse(
            job_id=job.id,
            project_id=job.project_id,
            job_type=job.job_type,
            status=job.status,
            progress_percent=progress,
            trace=trace,
            error_message=job.error_message,
            started_at=job.started_at,
            completed_at=job.completed_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    @router.get("/jobs/{job_id}/status/stream")
    def stream_job_status(
        request: Request,
        job_id: int,
        db: Session = Depends(get_db),
        owner_id: int = Depends(get_api_key_owner),
    ) -> StreamingResponse:
        """Server-Sent Events stream of job status updates.

        Polls the DB every second and yields ``progress_percent`` and trace
        until the job reaches ``completed`` or ``failed``.
        """

        def event_generator():
            import asyncio
            import json

            last_payload_hash = ""
            while True:
                with db.no_autoflush:
                    job = db.query(AeoJob).filter_by(id=job_id).first()
                if not job:
                    yield f"event: error\ndata: {json.dumps({'detail': 'Job not found'})}\n\n"
                    break

                payload = job.output_payload or {}
                trace = payload.get("trace", []) if isinstance(payload, dict) else []
                current_hash = json.dumps({"status": job.status, "progress": payload.get("progress_percent"), "trace_len": len(trace)})
                if current_hash != last_payload_hash:
                    last_payload_hash = current_hash
                    data = {
                        "job_id": job.id,
                        "status": job.status,
                        "progress_percent": payload.get("progress_percent", 0) if isinstance(payload, dict) else 0,
                        "current_step": payload.get("current_step") if isinstance(payload, dict) else None,
                        "error_message": job.error_message,
                    }
                    yield f"event: status\ndata: {json.dumps(data)}\n\n"

                if job.status in {"completed", "failed"}:
                    yield f"event: done\ndata: {json.dumps({'status': job.status})}\n\n"
                    break

                try:
                    # Fast non-blocking sleep for async generator.
                    yield "\n"
                except Exception:
                    pass

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @router.get("/jobs/{job_id}/result", response_model=JobResultResponse)
    def get_job_result(
        job_id: int,
        db: Session = Depends(get_db),
        owner_id: int = Depends(get_api_key_owner),
    ) -> JobResultResponse:
        job = db.query(AeoJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        pages = (
            db.query(AeoPageOutput)
            .filter_by(job_id=job.id)
            .order_by(AeoPageOutput.created_at.asc())
            .all()
        )

        result_pages: List[Dict[str, Any]] = []
        audit_scores: List[Dict[str, Any]] = []
        for page in pages:
            html = _markdown_to_html(page.page_content)
            page_json_ld = [entry.json_payload for entry in page.json_ld_entries]
            audit = {
                "aeo_score": page.aeo_score,
                "page_id": page.id,
                "page_type": page.page_type,
            }
            audit_scores.append(audit)
            result_pages.append({
                "page_id": page.id,
                "page_type": page.page_type,
                "slug": page.slug,
                "title": page.title,
                "meta_description": page.meta_description,
                "html": html,
                "markdown": page.page_content or "",
                "direct_answer_block": page.direct_answer_block,
                "aeo_score": page.aeo_score,
                "entity_coverage_score": None,
                "json_ld": page_json_ld,
            })

        project_json_ld = (
            db.query(AeoJsonLd)
            .filter_by(project_id=job.project_id, page_id=None)
            .all()
        )

        return JobResultResponse(
            job_id=job.id,
            project_id=job.project_id,
            status=job.status,
            credits_deducted=job.credit_cost or 0.0,
            pages=result_pages,
            audit_summary={"pages": audit_scores},
            json_ld_schemas=[entry.json_payload for entry in project_json_ld],
        )

    @router.get("/jobs/{job_id}/result/html/{page_id}", response_class=HTMLResponse)
    def get_job_result_html(
        job_id: int,
        page_id: int,
        db: Session = Depends(get_db),
        owner_id: int = Depends(get_api_key_owner),
    ) -> str:
        page = (
            db.query(AeoPageOutput)
            .filter_by(id=page_id, job_id=job_id)
            .first()
        )
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        return _markdown_to_html(page.page_content)

    # -----------------------------------------------------------------------
    # Publish endpoints
    # -----------------------------------------------------------------------
    @router.post("/publish/wordpress", response_model=WordPressPublishResponse)
    def publish_wordpress(
        request: WordPressPublishRequest,
        db: Session = Depends(get_db),
        owner_id: int = Depends(require_scope("publish")),
    ) -> WordPressPublishResponse:
        try:
            result = publish_to_wordpress(
                db=db,
                page_output_id=request.page_output_id,
                wordpress_url=request.wordpress_url,
                username=request.username,
                application_password=request.application_password,
                status=request.status,
                post_type=request.post_type,
                extra_meta=request.extra_meta,
            )
            return WordPressPublishResponse(**result)
        except WordPressPublishError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=exc.message,
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -----------------------------------------------------------------------
    # API key management
    # -----------------------------------------------------------------------
    @router.post("/api-keys", response_model=ApiKeyCreatedResponse)
    def create_api_key(
        request: ApiKeyCreateRequest,
        db: Session = Depends(get_db),
        owner_id: int = Depends(get_api_key_owner),
    ) -> ApiKeyCreatedResponse:
        plain_key = generate_api_key()
        key_record = AeoApiKey(
            owner_id=request.owner_id or owner_id,
            name=request.name,
            key_hash=hash_api_key(plain_key),
            key_prefix=plain_key[:8] + "...",
            scopes=request.scopes,
            is_active=1,
            expires_at=request.expires_at,
        )
        db.add(key_record)
        db.flush()
        db.refresh(key_record)
        return ApiKeyCreatedResponse(
            id=key_record.id,
            owner_id=key_record.owner_id,
            name=key_record.name,
            key_prefix=key_record.key_prefix,
            scopes=key_record.scopes or [],
            is_active=bool(key_record.is_active),
            last_used_at=key_record.last_used_at,
            created_at=key_record.created_at,
            expires_at=key_record.expires_at,
            plain_key=plain_key,
        )

    @router.get("/api-keys", response_model=List[ApiKeyResponse])
    def list_api_keys(
        db: Session = Depends(get_db),
        owner_id: int = Depends(get_api_key_owner),
    ) -> List[AeoApiKey]:
        return db.query(AeoApiKey).filter_by(owner_id=owner_id).order_by(AeoApiKey.created_at.desc()).all()

    @router.delete("/api-keys/{key_id}")
    def revoke_api_key(
        key_id: int,
        db: Session = Depends(get_db),
        owner_id: int = Depends(get_api_key_owner),
    ) -> dict:
        key_record = db.query(AeoApiKey).filter_by(id=key_id, owner_id=owner_id).first()
        if not key_record:
            raise HTTPException(status_code=404, detail="API key not found")
        key_record.is_active = 0
        db.flush()
        return {"detail": "API key revoked"}

    # -----------------------------------------------------------------------
    # Credits (top-up helper for testing / billing integration)
    # -----------------------------------------------------------------------
    @router.post("/credits/top-up")
    def top_up_credits(
        amount: float = Form(..., gt=0),
        db: Session = Depends(get_db),
        owner_id: int = Depends(get_api_key_owner),
    ) -> dict:
        entry = add_credits(
            db=db,
            owner_id=owner_id,
            amount=amount,
            metadata={"source": "manual_topup"},
        )
        return {
            "owner_id": owner_id,
            "credits_added": entry.amount,
            "new_balance": entry.balance_after,
        }

    # -----------------------------------------------------------------------
    # Phase 1 legacy endpoints (kept under the new prefix)
    # -----------------------------------------------------------------------
    @router.post("/projects/{project_id}/run", response_model=JobResponse)
    def run_pipeline(
        project_id: int,
        request: RunPipelineRequest,
        db: Session = Depends(get_db),
    ) -> AeoJob:
        if project_id != request.project_id:
            raise HTTPException(status_code=400, detail="project_id mismatch")
        project = db.query(AeoProject).filter_by(id=project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        orchestrator = AeoOrchestrator(db=db)
        result = orchestrator.run_pipeline(request)
        return db.query(AeoJob).filter_by(id=result.job_id).first()

    @router.get("/projects/{project_id}/pages", response_model=PageOutputListResponse)
    def list_pages(
        project_id: int,
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        db: Session = Depends(get_db),
    ) -> dict:
        query = db.query(AeoPageOutput).filter_by(project_id=project_id).order_by(AeoPageOutput.created_at.desc())
        return {"total": query.count(), "items": query.offset(skip).limit(limit).all()}

    @router.get("/projects/{project_id}/pages/{page_id}", response_model=PageOutputResponse)
    def get_page(project_id: int, page_id: int, db: Session = Depends(get_db)) -> AeoPageOutput:
        page = db.query(AeoPageOutput).filter_by(project_id=project_id, id=page_id).first()
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        return page

    return router

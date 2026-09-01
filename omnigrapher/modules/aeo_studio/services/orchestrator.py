"""AEO Studio pipeline orchestrator.

Runs the four agents end-to-end, persists results to the database, and maintains
the module's credit ledger.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from sqlalchemy.orm import Session

from ..agents.base import AeoAgentContext, BaseAgent
from ..agents.intent_entity_researcher import IntentEntityResearcher
from ..agents.knowledge_graph_grounding import KnowledgeGraphGrounding
from ..agents.aeo_content_synthesizer import AeoContentSynthesizer
from ..agents.fact_checker_auditor import FactCheckerAuditor
from ..config import AeoStudioSettings, get_settings
from ..models import (
    AeoCreditLedger,
    AeoEntity,
    AeoIntentKeyword,
    AeoJob,
    AeoJsonLd,
    AeoPageOutput,
    AeoProject,
)
from ..schemas import RunPipelineRequest, RunPipelineResponse
from ..services.graph_client import StubVectorClient

logger = logging.getLogger(__name__)


class AeoOrchestrator:
    """Run the AEO Studio agent pipeline synchronously or via a Celery worker."""

    def __init__(
        self,
        db: Optional[Session] = None,
        settings: Optional[AeoStudioSettings] = None,
        vector_client=None,
        llm_client=None,
    ):
        self.settings = settings or get_settings()
        self.db = db
        self.vector_client = vector_client or self._default_vector_client()
        # Default to no LLM client; pass an OllamaLLMClient explicitly when a
        # running Ollama endpoint is available.
        self.llm_client = llm_client

    def _default_vector_client(self):
        # Phase 1 defaults to the offline stub. The parent OmniGrapher app can
        # inject ChromaVectorClient(self.settings) for full grounding.
        return StubVectorClient()

    @staticmethod
    def _agent_registry() -> Dict[str, Type[BaseAgent]]:
        return {
            "intent_research": IntentEntityResearcher,
            "grounding": KnowledgeGraphGrounding,
            "content_synthesis": AeoContentSynthesizer,
            "audit": FactCheckerAuditor,
        }

    def run_pipeline(self, request: RunPipelineRequest, owner_id: Optional[int] = None) -> RunPipelineResponse:
        """Create a job record and execute the full AEO pipeline."""
        if self.db is None:
            raise RuntimeError("AeoOrchestrator requires a database session to persist results")

        project = self.db.query(AeoProject).filter_by(id=request.project_id).first()
        if not project:
            raise ValueError(f"Project {request.project_id} not found")

        job = AeoJob(
            project_id=project.id,
            job_type="full_pipeline",
            status="pending",
            input_payload={"page_types": request.page_types, "dry_run": request.dry_run},
        )
        self.db.add(job)
        self.db.flush()

        context = AeoAgentContext(
            project_id=project.id,
            settings=self.settings,
            db_session=self.db,
            vector_client=self.vector_client,
            llm_client=self.llm_client,
        )

        try:
            job.status = "running"
            job.started_at = datetime.utcnow()
            self.db.flush()

            # Agent 1: research intent and entities.
            job.output_payload = {"progress_percent": 10, "current_step": "intent_research"}
            self.db.flush()
            research_inputs = {
                "business_name": project.business_name,
                "business_type": project.business_type or "LocalBusiness",
                "location": project.location or "",
                "seed_keywords": project.seed_keywords or [],
                "target_audience": project.target_audience,
                "website_url": project.website_url,
            }
            research_output = IntentEntityResearcher(self.settings).run(research_inputs, context)

            # Persist research artifacts.
            self._persist_entities(project.id, research_output["entities"])
            self._persist_keywords(project.id, research_output["keywords"])
            self.db.flush()

            # Agent 2: ground entities and build JSON-LD.
            job.output_payload = {"progress_percent": 35, "current_step": "knowledge_graph_grounding"}
            self.db.flush()
            grounding_inputs = {
                "project": self._project_to_dict(project),
                "entities": research_output["entities"],
                "top_questions": research_output["top_questions"],
                "keywords": research_output["keywords"],
            }
            grounding_output = KnowledgeGraphGrounding(self.settings).run(grounding_inputs, context)
            self.db.flush()

            # Agent 3: synthesize pages.
            pages_created = 0
            page_outputs = []
            page_count = len(request.page_types) or 1
            for idx, page_type in enumerate(request.page_types):
                job.output_payload = {
                    "progress_percent": 40 + int((idx / page_count) * 45),
                    "current_step": f"content_synthesis:{page_type}",
                }
                self.db.flush()
                synthesis_inputs = {
                    "project": self._project_to_dict(project),
                    "grounded_entities": grounding_output["grounded_entities"],
                    "keywords": research_output["keywords"],
                    "top_questions": research_output["top_questions"],
                    "page_type": page_type,
                }
                synthesis_output = AeoContentSynthesizer(self.settings).run(synthesis_inputs, context)
                page_outputs.append(synthesis_output)

                # Agent 4: audit each page.
                audit_inputs = {
                    "page_output": synthesis_output,
                    "grounded_entities": grounding_output["grounded_entities"],
                    "json_ld_schemas": grounding_output["json_ld_schemas"],
                    "sources": synthesis_output.get("sources", []),
                    "keywords": research_output["keywords"],
                }
                audit_output = FactCheckerAuditor(self.settings).run(audit_inputs, context)
                synthesis_output["audit"] = audit_output

                if not request.dry_run:
                    page = self._persist_page(project.id, job.id, synthesis_output, audit_output)
                    pages_created += 1
                    self._persist_jsonld_for_page(page.id, grounding_output["json_ld_schemas"])

            # Persist JSON-LD at project level.
            if not request.dry_run:
                self._persist_jsonld_for_project(project.id, grounding_output["json_ld_schemas"])

            job.output_payload = {"progress_percent": 90, "current_step": "credit_charge"}
            self.db.flush()

            # Charge credits.
            credit_cost = self._charge_credits(
                project=project,
                job=job,
                owner_id=owner_id,
                pages_created=pages_created,
                dry_run=request.dry_run,
            )

            job.status = "completed"
            job.completed_at = datetime.utcnow()
            job.output_payload = {
                "progress_percent": 100,
                "current_step": "completed",
                "research": research_output,
                "grounding": grounding_output,
                "pages": page_outputs,
                "trace": context.trace,
            }
            self.db.flush()

            return RunPipelineResponse(
                job_id=job.id,
                project_id=project.id,
                status=job.status,
                message="AEO pipeline completed successfully" if not request.dry_run else "Dry run completed",
                page_outputs_created=pages_created,
                json_ld_created=len(grounding_output["json_ld_schemas"]),
                credit_charged=credit_cost,
            )

        except Exception as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()
            job.output_payload = {
                "progress_percent": 0,
                "current_step": "failed",
                "trace": context.trace,
                "error": str(exc),
            }
            self.db.flush()
            logger.exception("AEO pipeline failed for project %s", project.id)
            raise

    def _project_to_dict(self, project: AeoProject) -> Dict[str, Any]:
        return {
            "id": project.id,
            "name": project.name,
            "business_name": project.business_name,
            "business_type": project.business_type,
            "website_url": project.website_url,
            "location": project.location,
            "target_audience": project.target_audience,
            "seed_keywords": project.seed_keywords or [],
            "status": project.status,
        }

    def _persist_entities(self, project_id: int, entities: List[Dict[str, Any]]) -> None:
        """Insert or update research entities for a project."""
        if not self.db:
            return
        for e in entities:
            existing = (
                self.db.query(AeoEntity)
                .filter_by(
                    project_id=project_id,
                    name=e.get("name", "").lower(),
                    type=e.get("type", "Concept").lower(),
                )
                .first()
            )
            if existing:
                existing.salience = float(e.get("salience", existing.salience))
                existing.description = e.get("description") or existing.description
                existing.source_chunks = e.get("source_chunks") or existing.source_chunks
                continue
            self.db.add(
                AeoEntity(
                    project_id=project_id,
                    name=e.get("name", ""),
                    type=e.get("type", "Concept"),
                    salience=float(e.get("salience", 0.0)),
                    description=e.get("description"),
                    source_chunks=e.get("source_chunks") or [],
                )
            )

    def _persist_keywords(self, project_id: int, keywords: List[Dict[str, Any]]) -> None:
        """Insert or update intent keywords for a project."""
        if not self.db:
            return
        for kw in keywords:
            keyword_text = (kw["keyword"] if isinstance(kw, dict) else str(kw)).lower()
            existing = (
                self.db.query(AeoIntentKeyword)
                .filter_by(project_id=project_id, keyword=keyword_text)
                .first()
            )
            if existing:
                existing.intent_type = kw.get("intent_type", existing.intent_type)
                existing.salience = float(kw.get("salience", existing.salience))
                existing.competition = kw.get("competition") or existing.competition
                continue
            self.db.add(
                AeoIntentKeyword(
                    project_id=project_id,
                    keyword=keyword_text,
                    intent_type=kw.get("intent_type", "informational"),
                    question_form=kw.get("question_form"),
                    search_volume_estimate=kw.get("search_volume_estimate"),
                    competition=kw.get("competition"),
                    entities=kw.get("entities") or [],
                    salience=float(kw.get("salience", 0.0)),
                )
            )

    def _persist_page(
        self,
        project_id: int,
        job_id: int,
        synthesis_output: Dict[str, Any],
        audit_output: Dict[str, Any],
    ) -> AeoPageOutput:
        page = AeoPageOutput(
            project_id=project_id,
            job_id=job_id,
            page_type=synthesis_output["page_type"],
            slug=synthesis_output["slug"],
            title=synthesis_output["title"],
            meta_description=synthesis_output.get("meta_description"),
            direct_answer_block=synthesis_output.get("direct_answer_block"),
            page_content=synthesis_output.get("page_content"),
            structured_outline=synthesis_output.get("structured_outline") or {},
            keywords=synthesis_output.get("keywords") or [],
            entities=synthesis_output.get("entities") or [],
            sources=synthesis_output.get("sources") or [],
            aeo_score=audit_output.get("aeo_score"),
            status="draft",
        )
        self.db.add(page)
        self.db.flush()
        return page

    def _persist_jsonld_for_page(self, page_id: int, schemas: List[Dict[str, Any]]) -> None:
        """Attach JSON-LD schemas to a specific page output."""
        if not self.db or not schemas:
            return
        page = self.db.query(AeoPageOutput).filter_by(id=page_id).first()
        if not page:
            return
        for s in schemas:
            existing = (
                self.db.query(AeoJsonLd)
                .filter_by(page_id=page.id, schema_type=s["schema_type"])
                .first()
            )
            if existing:
                existing.json_payload = s.get("json_payload", {})
                existing.name = s.get("name") or existing.name
                continue
            self.db.add(
                AeoJsonLd(
                    project_id=page.project_id,
                    page_id=page.id,
                    schema_type=s["schema_type"],
                    name=s.get("name"),
                    json_payload=s.get("json_payload", {}),
                )
            )

    def _persist_jsonld_for_project(self, project_id: int, schemas: List[Dict[str, Any]]) -> None:
        """Persist project-level JSON-LD schemas (page_id=None)."""
        if not self.db or not schemas:
            return
        for s in schemas:
            existing = (
                self.db.query(AeoJsonLd)
                .filter_by(project_id=project_id, page_id=None, schema_type=s["schema_type"])
                .first()
            )
            if existing:
                existing.json_payload = s.get("json_payload", {})
                existing.name = s.get("name") or existing.name
                continue
            self.db.add(
                AeoJsonLd(
                    project_id=project_id,
                    page_id=None,
                    schema_type=s["schema_type"],
                    name=s.get("name"),
                    json_payload=s.get("json_payload", {}),
                )
            )

    def _charge_credits(
        self,
        project: AeoProject,
        job: AeoJob,
        owner_id: Optional[int],
        pages_created: int,
        dry_run: bool,
    ) -> float:
        if dry_run:
            return 0.0

        job_cost = self.settings.credit_cost_per_job
        page_cost = pages_created * self.settings.credit_cost_per_page
        total = round(job_cost + page_cost, 2)

        # Compute current balance for owner/project pair.
        last_entry = (
            self.db.query(AeoCreditLedger)
            .filter_by(owner_id=owner_id, project_id=project.id)
            .order_by(AeoCreditLedger.id.desc())
            .first()
        )
        previous_balance = last_entry.balance_after if last_entry else 0.0

        ledger = AeoCreditLedger(
            owner_id=owner_id,
            project_id=project.id,
            operation_type="job_charge",
            amount=-total,
            balance_after=previous_balance - total,
            job_id=job.id,
            metadata_={"pages": pages_created, "job_cost": job_cost, "page_cost": page_cost},
        )
        self.db.add(ledger)
        job.credit_cost = total
        self.db.flush()
        return total

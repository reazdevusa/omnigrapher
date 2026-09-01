"""Smoke tests for the AEO Studio module (Phase 1).

These tests exercise the agent contracts and the database models without
requiring a running Ollama or ChromaDB instance.
"""

import os
import sys
import tempfile
from pathlib import Path

# Ensure the module can be imported from the workspace root.
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from omnigrapher.modules.aeo_studio import (
    AeoAgentContext,
    AeoContentSynthesizer,
    AeoOrchestrator,
    AeoProject,
    FactCheckerAuditor,
    IntentEntityResearcher,
    KnowledgeGraphGrounding,
    get_settings,
    init_db,
)
from omnigrapher.modules.aeo_studio.config import AeoStudioSettings
from omnigrapher.modules.aeo_studio.database import Base, create_db_session, get_engine
from sqlalchemy import inspect
from omnigrapher.modules.aeo_studio.schemas import RunPipelineRequest


def test_database_initialization():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_aeo.db")
        settings = AeoStudioSettings(db_url=f"sqlite:///{db_path}")

        # Replace cached singleton for the test.
        from omnigrapher.modules.aeo_studio import config
        from omnigrapher.modules.aeo_studio import database

        config._settings = settings
        database._engine = None
        database._session_factory = None

        init_db()
        try:
            inspector = inspect(get_engine())
            expected_tables = {
                "aeo_projects",
                "aeo_jobs",
                "aeo_page_outputs",
                "aeo_json_ld",
                "aeo_credit_ledger",
                "aeo_entities",
                "aeo_intent_keywords",
            }
            assert expected_tables.issubset(set(inspector.get_table_names())), inspector.get_table_names()
        finally:
            get_engine().dispose()
        print("[OK] database_initialization")


def test_agent_1_intent_entity_researcher():
    settings = get_settings()
    context = AeoAgentContext(project_id=1, settings=settings)
    agent = IntentEntityResearcher(settings)
    result = agent.run(
        inputs={
            "business_name": "Alfa Roofing LLC",
            "business_type": "RoofingContractor",
            "location": "Austin, TX",
            "seed_keywords": ["roof repair", "new roof", "emergency roofing"],
            "target_audience": "homeowners",
        },
        context=context,
    )
    assert "keywords" in result
    assert "entities" in result
    assert "top_questions" in result
    assert len(result["keywords"]) > 0
    assert any(e["name"] == "Alfa Roofing LLC" for e in result["entities"])
    assert any(k["intent_type"] in {"local", "transactional", "informational"} for k in result["keywords"])
    print("[OK] agent_1_intent_entity_researcher")


def test_agent_2_knowledge_graph_grounding():
    settings = get_settings()
    context = AeoAgentContext(project_id=1, settings=settings)
    agent = KnowledgeGraphGrounding(settings)
    result = agent.run(
        inputs={
            "project": {
                "business_name": "Alfa Roofing LLC",
                "business_type": "RoofingContractor",
                "location": "Austin, TX",
                "website_url": "https://alfaroofing.example",
            },
            "entities": [
                {"name": "Alfa Roofing LLC", "type": "Organization", "salience": 1.0, "description": "Roofing company"},
                {"name": "Austin, TX", "type": "Place", "salience": 0.9, "description": "Location"},
                {"name": "RoofingContractor", "type": "Service", "salience": 0.85, "description": "Service category"},
            ],
            "top_questions": ["What is the best roofing contractor in Austin, TX?"],
        },
        context=context,
    )
    assert "grounded_entities" in result
    assert "json_ld_schemas" in result
    schema_types = {s["schema_type"] for s in result["json_ld_schemas"]}
    assert "LocalBusiness" in schema_types
    assert "Service" in schema_types
    assert "FAQPage" in schema_types
    assert "Organization" in schema_types
    print("[OK] agent_2_knowledge_graph_grounding")


def test_agent_3_content_synthesizer():
    settings = get_settings()
    context = AeoAgentContext(project_id=1, settings=settings)
    agent = AeoContentSynthesizer(settings)
    result = agent.run(
        inputs={
            "project": {
                "business_name": "Alfa Roofing LLC",
                "business_type": "RoofingContractor",
                "location": "Austin, TX",
                "website_url": "https://alfaroofing.example",
            },
            "grounded_entities": [
                {"name": "Alfa Roofing LLC", "type": "Organization", "salience": 1.0, "description": "Roofing company"},
                {"name": "Austin, TX", "type": "Place", "salience": 0.9, "description": "Location"},
            ],
            "keywords": [
                {"keyword": "roof repair austin tx", "intent_type": "local"},
                {"keyword": "emergency roofing", "intent_type": "transactional"},
            ],
            "top_questions": ["What is the best roofing contractor in Austin, TX?"],
            "page_type": "landing",
        },
        context=context,
    )
    assert result["page_type"] == "landing"
    assert result["title"]
    assert result["slug"]
    assert result["direct_answer_block"]
    words = len(result["direct_answer_block"].split())
    assert settings.direct_answer_min_words <= words <= settings.direct_answer_max_words, words
    assert "page_content" in result
    assert "structured_outline" in result
    print("[OK] agent_3_content_synthesizer")


def test_agent_4_fact_checker_auditor():
    settings = get_settings()
    context = AeoAgentContext(project_id=1, settings=settings)
    agent = FactCheckerAuditor(settings)
    page_output = {
        "page_type": "landing",
        "title": "Alfa Roofing LLC — Roofing Contractor in Austin, TX",
        "slug": "alfa-roofing-llc-roofing-contractor-services",
        "meta_description": "Top roofing contractor in Austin, TX.",
        "direct_answer_block": (
            "Alfa Roofing LLC is a trusted roofing contractor serving Austin, TX. "
            "We specialize in roof repair, new roof installation, and emergency roofing. "
            "Contact us today for fast, reliable service."
        ),
        "page_content": "Alfa Roofing LLC offers roof repair and new roof installation in Austin, TX. Our team provides emergency roofing services.",
        "keywords": ["roof repair austin tx", "new roof", "emergency roofing"],
        "entities": ["Alfa Roofing LLC", "Austin, TX"],
        "sources": [],
    }
    grounded_entities = [
        {"name": "Alfa Roofing LLC", "type": "Organization", "salience": 1.0},
        {"name": "Austin, TX", "type": "Place", "salience": 0.9},
    ]
    schemas = [
        {"schema_type": "LocalBusiness", "json_payload": {"@context": "https://schema.org", "@type": "LocalBusiness"}},
        {"schema_type": "Service", "json_payload": {"@context": "https://schema.org", "@type": "Service"}},
        {"schema_type": "FAQPage", "json_payload": {"@context": "https://schema.org", "@type": "FAQPage"}},
    ]
    result = agent.run(
        inputs={
            "page_output": page_output,
            "grounded_entities": grounded_entities,
            "json_ld_schemas": schemas,
        },
        context=context,
    )
    assert 0 <= result["aeo_score"] <= 100
    assert 0 <= result["entity_coverage_score"] <= 100
    assert 0 <= result["direct_answer_score"] <= 100
    assert 0 <= result["schema_score"] <= 100
    assert "issues" in result
    assert "recommendations" in result
    print("[OK] agent_4_fact_checker_auditor")


def test_orchestrator_dry_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "orchestrator.db")
        settings = AeoStudioSettings(db_url=f"sqlite:///{db_path}")

        from omnigrapher.modules.aeo_studio import config
        from omnigrapher.modules.aeo_studio import database

        config._settings = settings
        database._engine = None
        database._session_factory = None

        init_db()
        db = create_db_session()
        try:
            project = AeoProject(
                name="Alfa Roofing",
                business_name="Alfa Roofing LLC",
                business_type="RoofingContractor",
                location="Austin, TX",
                seed_keywords=["roof repair", "new roof"],
            )
            db.add(project)
            db.flush()

            orch = AeoOrchestrator(db=db, settings=settings)
            request = RunPipelineRequest(
                project_id=project.id,
                page_types=["landing", "faq"],
                dry_run=True,
            )
            result = orch.run_pipeline(request, owner_id=None)
            assert result.status == "completed"
            assert result.page_outputs_created == 0  # dry run does not persist pages
            assert result.credit_charged == 0.0
            assert result.json_ld_created > 0

            # Ensure job record is persisted.
            jobs = db.query(AeoProject).filter_by(id=project.id).first().jobs
            assert len(jobs) == 1
            assert jobs[0].status == "completed"
        finally:
            db.close()
            get_engine().dispose()
        print("[OK] orchestrator_dry_run")


def run_smoke_test():
    """Run all smoke tests sequentially."""
    print("Running AEO Studio Phase 1 smoke tests...")
    test_database_initialization()
    test_agent_1_intent_entity_researcher()
    test_agent_2_knowledge_graph_grounding()
    test_agent_3_content_synthesizer()
    test_agent_4_fact_checker_auditor()
    test_orchestrator_dry_run()
    print("All smoke tests passed.")


if __name__ == "__main__":
    run_smoke_test()

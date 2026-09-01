"""AEO Studio — SEO & Answer Engine Optimization module for OmniGrapher.

Phase 1 ships the backend architecture:
- SQLAlchemy models for projects, jobs, page outputs, JSON-LD, credits, entities and keywords.
- Four pluggable Python backend agents that research intent, ground knowledge,
  synthesize AEO content, and audit the result.
- A lightweight orchestrator and FastAPI router that wire the agents together.

The module is intentionally isolated under ``omnigrapher/modules/aeo_studio/``
so it can be imported into the main FastAPI app with a single router include.
"""

__version__ = "0.1.0"
__module_name__ = "aeo_studio"

from .config import AeoStudioSettings, get_settings
from .database import Base, get_db, init_db, create_db_session
from .models import (
    AeoProject,
    AeoJob,
    AeoPageOutput,
    AeoJsonLd,
    AeoCreditLedger,
    AeoEntity,
    AeoIntentKeyword,
)
from .agents.base import BaseAgent, AeoAgentContext
from .agents.intent_entity_researcher import IntentEntityResearcher
from .agents.knowledge_graph_grounding import KnowledgeGraphGrounding
from .agents.aeo_content_synthesizer import AeoContentSynthesizer
from .agents.fact_checker_auditor import FactCheckerAuditor
from .services.orchestrator import AeoOrchestrator

__all__ = [
    "__version__",
    "__module_name__",
    "AeoStudioSettings",
    "get_settings",
    "Base",
    "get_db",
    "init_db",
    "create_db_session",
    "AeoProject",
    "AeoJob",
    "AeoPageOutput",
    "AeoJsonLd",
    "AeoCreditLedger",
    "AeoEntity",
    "AeoIntentKeyword",
    "BaseAgent",
    "AeoAgentContext",
    "IntentEntityResearcher",
    "KnowledgeGraphGrounding",
    "AeoContentSynthesizer",
    "FactCheckerAuditor",
    "AeoOrchestrator",
]

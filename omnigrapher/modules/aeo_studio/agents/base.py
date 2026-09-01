"""Base agent contract and shared context for AEO Studio agents."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import AeoStudioSettings, get_settings


@dataclass
class AeoAgentContext:
    """Shared runtime context passed to every agent."""

    project_id: int
    settings: AeoStudioSettings = field(default_factory=get_settings)
    db_session: Optional[Any] = None  # SQLAlchemy session when running with persistence
    vector_client: Optional[Any] = None  # ChromaDB / LlamaIndex client wrapper
    graph_client: Optional[Any] = None  # Kùzu connection wrapper
    llm_client: Optional[Any] = None  # LLM client wrapper
    trace: List[Dict[str, Any]] = field(default_factory=list)

    def log_step(self, agent: str, step: str, payload: Optional[Dict[str, Any]] = None) -> None:
        from datetime import datetime

        self.trace.append({
            "agent": agent,
            "step": step,
            "payload": payload or {},
            "timestamp": datetime.utcnow().isoformat(),
        })


class BaseAgent(ABC):
    """Every AEO agent implements this interface."""

    name: str = "base"
    description: str = ""
    required_input_keys: List[str] = []
    optional_input_keys: List[str] = []

    def __init__(self, settings: Optional[AeoStudioSettings] = None):
        self.settings = settings or get_settings()

    def validate_inputs(self, inputs: Dict[str, Any]) -> None:
        """Raise ValueError if required keys are missing."""
        missing = [key for key in self.required_input_keys if key not in inputs or inputs[key] is None]
        if missing:
            raise ValueError(f"Agent {self.name} missing required inputs: {missing}")

    @abstractmethod
    def run(self, inputs: Dict[str, Any], context: AeoAgentContext) -> Dict[str, Any]:
        """Execute the agent and return a serializable output dict."""
        ...

    def _estimate_tokens(self, text: str) -> int:
        """Fast byte/char heuristic used by the parent OmniGrapher LLMProvider."""
        if not text:
            return 0
        return max(1, len(text) // 4)

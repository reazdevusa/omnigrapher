"""Base types for LLM providers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Message:
    role: str
    content: str

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""
    raw: Any = field(default=None, repr=False)


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(
        self,
        model: str,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        ...

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        # Very fast byte/character heuristic; real token counts come from the API when available.
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _estimate_input(self, messages: List[Message]) -> int:
        return sum(self._estimate_tokens(m.content) for m in messages)

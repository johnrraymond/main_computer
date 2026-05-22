from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from main_computer.models import ChatMessage, ChatResponse


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        """Return an assistant response for the given chat messages."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers.base import LLMProvider


@dataclass
class OpenAIProvider(LLMProvider):
    model: str = "gpt-5.2"
    api_key: str | None = None
    base_url: str | None = None
    fallback: bool = False

    name: str = "openai"

    def _client(self) -> Any:
        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("OpenAI SDK is not installed. Run: pip install openai") from exc

        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("Missing OPENAI_API_KEY for OpenAI provider.")

        kwargs: dict[str, Any] = {"api_key": key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return OpenAI(**kwargs)

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        if any(message.attachments for message in messages):
            raise RuntimeError("OpenAI chat-console image/file attachments are not supported by this provider yet.")
        system_parts = [msg.content for msg in messages if msg.role == "system"]
        user_parts = [msg.content for msg in messages if msg.role != "system"]
        instructions = "\n\n".join(system_parts) or None
        user_input = "\n\n".join(user_parts)

        started = time.monotonic()
        if self.fallback:
            print(f"[fallback][openai] request model={self.model} messages={len(messages)}", file=sys.stderr, flush=True)
        response = self._client().responses.create(
            model=self.model,
            instructions=instructions,
            input=user_input,
        )
        content = getattr(response, "output_text", None)
        if content is None:
            content = str(response)
        if self.fallback:
            duration_ms = int((time.monotonic() - started) * 1000)
            print(f"[fallback][openai] first/complete response after {duration_ms} ms content_chars={len(str(content))}", file=sys.stderr, flush=True)
            print(str(content), flush=True)

        return ChatResponse(
            content=str(content),
            provider=self.name,
            model=self.model,
            metadata={"response_id": getattr(response, "id", None)},
        )

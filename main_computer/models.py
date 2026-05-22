from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatAttachment:
    id: str
    filename: str
    mime_type: str
    data_base64: str
    kind: str = "file"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str
    attachments: list[ChatAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class ChatResponse:
    content: str
    provider: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)

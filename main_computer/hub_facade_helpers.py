"""Legacy Hub facade helpers, independent of Hub HTTP and storage internals.

P5.3i relocates unchanged source, maintaining historical import identity through
``main_computer.hub``. Importing this module has no network or payment effects.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from typing import Any

from main_computer.models import ChatMessage, ChatResponse

def _stable_request_id(payload: dict[str, Any]) -> str:
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    stamp = str(time.time_ns()).encode("ascii")
    return "hub_" + hashlib.sha256(seed + stamp).hexdigest()[:20]


def _stable_session_id(request_id: str, payload: dict[str, Any]) -> str:
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    stamp = str(time.time_ns()).encode("ascii")
    return "sess_" + hashlib.sha256(request_id.encode("utf-8") + seed + stamp).hexdigest()[:24]


def chat_message_to_dict(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "attachments": [asdict(attachment) for attachment in message.attachments],
    }


def chat_response_from_dict(payload: dict[str, Any], *, default_provider: str, default_model: str) -> ChatResponse:
    return ChatResponse(
        content=str(payload.get("content", "")),
        provider=str(payload.get("provider") or default_provider),
        model=str(payload.get("model") or default_model),
        metadata=dict(payload.get("metadata", {})) if isinstance(payload.get("metadata", {}), dict) else {},
    )


def serving_hub_identity_for_server(server: Any) -> dict[str, Any]:
    """Return the concrete Hub identity that handled the current request.

    Stable multi-Hub deployments expose a shared entry URL such as
    ``testnet-hub.greatlibrary.io``.  The admin UI should still show the concrete
    Hub process that served the request, using the topology hub id
    (``testnet-hub2``) rather than an implementation address.
    """

    node = getattr(server, "stable_hub_node", None)
    if node is not None:
        hub_id = str(getattr(node, "hub_id", "") or "").strip()
        return {
            "hub_id": hub_id or "main-computer-hub",
            "display_name": hub_id or "main-computer-hub",
            "hub_url": str(getattr(node, "hub_url", "") or "").strip(),
            "public_url": str(getattr(node, "public_url", "") or "").strip(),
            "roles": list(getattr(node, "roles", ()) or ()),
            "source": "stable_topology",
        }

    config = getattr(server, "config", None)
    configured_id = str(getattr(config, "hub_id", "") or "").strip()
    fallback_id = configured_id or "main-computer-hub"
    return {
        "hub_id": fallback_id,
        "display_name": fallback_id,
        "hub_url": str(getattr(config, "hub_url", "") or "").strip(),
        "public_url": str(getattr(config, "hub_url", "") or "").strip(),
        "roles": [],
        "source": "local_hub",
    }

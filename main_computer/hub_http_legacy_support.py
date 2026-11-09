"""Shared legacy Dev-Test route authorization and serialization declarations.

No HTTP listener, registry, FDB, or payment side effect is constructed here.
This module is imported by the legacy facade and the extracted route handler.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from main_computer.models import ChatAttachment, ChatMessage
from main_computer.multisession_key_signing import normalize_chain_id

DEFAULT_HUB_PORT = 8770
ALLFATHER_HUB_REMOTE_MANIFEST_PATH = Path(__file__).resolve().parent / "config" / "allfather_hub_remote_manifest.json"

def load_allfather_hub_remote_manifest() -> dict[str, Any]:
    """Load the allfather build-time source manifest embedded with this Hub."""

    path = ALLFATHER_HUB_REMOTE_MANIFEST_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "available": False,
            "path": str(path),
            "reason": "allfather hub remote manifest is not present",
            "observed": {
                "current_directory_dirty_in_hub_remote_manifest": None,
                "current_directory_compared_to_remote_main": False,
            },
            "comparison": {
                "current_directory_dirty_vs_remote_main": None,
            },
        }
    except Exception as exc:
        return {
            "available": False,
            "path": str(path),
            "reason": str(exc),
            "observed": {
                "current_directory_dirty_in_hub_remote_manifest": None,
                "current_directory_compared_to_remote_main": False,
            },
            "comparison": {
                "current_directory_dirty_vs_remote_main": None,
            },
        }
    if not isinstance(payload, dict):
        return {
            "available": False,
            "path": str(path),
            "reason": "manifest was not a JSON object",
            "observed": {
                "current_directory_dirty_in_hub_remote_manifest": None,
                "current_directory_compared_to_remote_main": False,
            },
            "comparison": {
                "current_directory_dirty_vs_remote_main": None,
            },
        }
    payload = dict(payload)
    payload["available"] = True
    payload.setdefault("path", str(path))
    return payload

HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID = normalize_chain_id("0x28757b2")
HUB_MULTISESSION_KEY_MAX_AGE_MINUTES = 15

class HubPaymentRequired(ValueError):
    """Raised when a paid Hub request cannot reserve enough wallet credits."""

class HubCreditAuthorizationError(ValueError):
    """Raised when a paid Hub request is not authorized for the claimed wallet."""

def chat_message_from_dict(payload: dict[str, Any]) -> ChatMessage:
    attachments = [
        ChatAttachment(
            id=str(item.get("id", "")),
            filename=str(item.get("filename", "")),
            mime_type=str(item.get("mime_type", "application/octet-stream")),
            data_base64=str(item.get("data_base64", "")),
            kind=str(item.get("kind", "file")),
            metadata=dict(item.get("metadata", {})) if isinstance(item.get("metadata", {}), dict) else {},
        )
        for item in payload.get("attachments", []) or []
        if isinstance(item, dict)
    ]
    role = str(payload.get("role", "user"))
    if role not in {"system", "user", "assistant"}:
        role = "user"
    return ChatMessage(role=role, content=str(payload.get("content", "")), attachments=attachments)

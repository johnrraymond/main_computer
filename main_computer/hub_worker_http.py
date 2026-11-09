"""Legacy Worker-side HTTP ingress for the Dev/Test compatibility protocol.

P5.3g extracts the *unchanged* HTTP server and route-handler class bodies
from ``hub.py``. This is not the authenticated Stable Hub live-session Worker
transport. Importing this module starts no listener or Worker process.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse

from main_computer.config import MainComputerConfig
from main_computer.hub_http_shared import _JsonHandler
from main_computer.hub_http_legacy_support import chat_message_from_dict
from main_computer.hub_registry_json import _utc_now
from main_computer.hub_resource_limits import BoundedHubHTTPServer, load_hub_resource_policy
from main_computer.hub_security import (
    HUB_SECURITY_PROFILE, decrypt_hub_envelope, derive_hub_session_key,
    encrypt_hub_envelope, generate_hub_session_keypair,
)
from main_computer.models import ChatMessage, ChatResponse

DEFAULT_HUB_WORKER_PORT = 8771
HUB_WORKER_CHAT_PATH = "/api/hub/worker/chat"
HUB_WORKER_SESSION_START_PATH = "/api/hub/worker/sessions/start"
HUB_WORKER_SESSION_CHAT_PATH = "/api/hub/worker/sessions/chat"

class HubWorkerHttpServer(BoundedHubHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        config: MainComputerConfig,
        chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
        *,
        verbose: bool = True,
    ) -> None:
        self.resource_limits = load_hub_resource_policy()
        super().__init__(server_address, HubWorkerHandler)
        self.verbose = verbose
        self.config = config
        self.chat_fn = chat_fn
        self.secure_sessions: dict[str, dict[str, Any]] = {}
        self.session_lock = threading.Lock()

class HubWorkerHandler(_JsonHandler):
    server: HubWorkerHttpServer

    def do_GET(self) -> None:
        if self.path == "/api/hub/worker/status":
            self._send_json(
                {
                    "ok": True,
                    "node_id": self.server.config.hub_worker_node_id,
                    "model": self.server.config.model,
                    "provider": self.server.config.provider,
                    "credits_per_request": self.server.config.hub_credits_per_request,
                    "security": {
                        "high_security_default": self.server.config.hub_high_security,
                        "encryption_profile": HUB_SECURITY_PROFILE,
                    },
                }
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._preflight_post():
            return
        try:
            parsed = urlparse(self.path)
            if parsed.path == HUB_WORKER_SESSION_START_PATH:
                body = self._read_json()
                session_id = str(body.get("session_id", "")).strip()
                request_id = str(body.get("request_id", "")).strip()
                requester_public_key = str(body.get("requester_public_key", "")).strip()
                if not session_id or not request_id or not requester_public_key:
                    raise ValueError("session_id, request_id, and requester_public_key are required.")
                keypair = generate_hub_session_keypair()
                shared_key = derive_hub_session_key(
                    private_key=keypair.private_key,
                    peer_public_key=requester_public_key,
                    session_id=session_id,
                )
                with self.server.session_lock:
                    self.server.secure_sessions[session_id] = {
                        "request_id": request_id,
                        "key": shared_key,
                        "created_at": _utc_now(),
                        "energy": body.get("energy", {}),
                    }
                self._send_json(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "request_id": request_id,
                        "worker_public_key": keypair.public_key,
                        "encryption": {
                            "profile": HUB_SECURITY_PROFILE,
                            "temporary_public_keys": True,
                            "hub_blind": True,
                        },
                    }
                )
                return
            if parsed.path == HUB_WORKER_SESSION_CHAT_PATH:
                body = self._read_json()
                session_id = str(body.get("session_id", "")).strip()
                request_id = str(body.get("request_id", "")).strip()
                envelope = body.get("envelope")
                if not isinstance(envelope, dict):
                    raise ValueError("encrypted envelope is required.")
                with self.server.session_lock:
                    session = dict(self.server.secure_sessions.get(session_id, {}))
                if not session:
                    raise ValueError("Unknown or expired worker session.")
                if request_id and str(session.get("request_id", "")) != request_id:
                    raise ValueError("Worker session request id mismatch.")
                key = session["key"]
                request_aad = {"session_id": session_id, "request_id": request_id, "direction": "request"}
                secure_payload = decrypt_hub_envelope(envelope, key=key, aad=request_aad)
                messages_payload = secure_payload.get("messages")
                if not isinstance(messages_payload, list):
                    raise ValueError("messages must be a list.")
                messages = [chat_message_from_dict(item) for item in messages_payload if isinstance(item, dict)]
                response = self.server.chat_fn(messages)
                metadata = dict(response.metadata)
                metadata["hub_worker"] = {
                    "request_id": request_id,
                    "node_id": self.server.config.hub_worker_node_id,
                    "energy": session.get("energy", {}),
                    "security_mode": "high-security",
                    "hub_blind": True,
                    "encryption_profile": HUB_SECURITY_PROFILE,
                }
                response_payload = {
                    "content": response.content,
                    "provider": response.provider,
                    "model": response.model,
                    "metadata": metadata,
                }
                response_aad = {"session_id": session_id, "request_id": request_id, "direction": "response"}
                self._send_json(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "request_id": request_id,
                        "response_envelope": encrypt_hub_envelope(response_payload, key=key, aad=response_aad),
                    }
                )
                return
            if parsed.path == HUB_WORKER_CHAT_PATH:
                body = self._read_json()
                messages_payload = body.get("messages")
                if not isinstance(messages_payload, list):
                    raise ValueError("messages must be a list.")
                messages = [chat_message_from_dict(item) for item in messages_payload if isinstance(item, dict)]
                response = self.server.chat_fn(messages)
                metadata = dict(response.metadata)
                metadata["hub_worker"] = {
                    "request_id": body.get("request_id"),
                    "node_id": self.server.config.hub_worker_node_id,
                    "energy": body.get("energy", {}),
                    "security_mode": "legacy-plaintext",
                }
                self._send_json(
                    {
                        "content": response.content,
                        "provider": response.provider,
                        "model": response.model,
                        "metadata": metadata,
                    }
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

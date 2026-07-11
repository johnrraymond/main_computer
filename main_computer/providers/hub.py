from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from main_computer.hub_security import (
    HUB_SECURITY_PROFILE,
    decrypt_hub_envelope,
    derive_hub_session_key,
    encrypt_hub_envelope,
    generate_hub_session_keypair,
    hub_transport_is_encrypted_or_loopback,
)
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers.base import LLMProvider


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "attachments": [asdict(attachment) for attachment in message.attachments],
    }


@dataclass
class HubProvider(LLMProvider):
    """Provider adapter that sends chat requests to a Main Computer hub.

    High-security mode is on by default: the adapter first asks the hub to pair
    it with a worker, exchanges temporary public keys with that worker through
    the hub, and then sends only authenticated encrypted envelopes through hub
    routing endpoints. The legacy plaintext endpoint is still available only when
    high_security is explicitly set to False.
    """

    model: str = "hub-auto"
    hub_url: str = "http://127.0.0.1:8770"
    timeout_s: float = 600.0
    client_node_id: str = "main-computer-client"
    high_security: bool = True
    allow_insecure_dev_network: bool = False
    fallback: bool = False

    name: str = "hub"

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        if self.high_security:
            return self._secure_chat(messages)
        return self._legacy_plaintext_chat(messages)

    def remote_overflow_safe_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        remote_overflow_request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChatResponse:
        """Call the Hub server's observable, no-credit Remote Hub overflow surface.

        In local development the application process can inherit a Docker/dev
        hub DNS name that is not resolvable from the Windows host running the
        Hub server.  This safe deterministic surface is explicitly local-dev
        no-credit work, so a DNS-resolution failure retries the same port on
        127.0.0.1 and records that fallback in response metadata.
        """

        payload = {
            "model": self.model,
            "client_node_id": self.client_node_id,
            "messages": [_message_payload(message) for message in messages],
            "remote_overflow_request_id": str(remote_overflow_request_id or ""),
            "metadata": dict(metadata or {}),
        }
        original_hub_url = self.hub_url.rstrip("/")
        fallback_from = ""
        try:
            data = self._post_json("/api/hub/remote-overflow/safe-chat", payload)
            hub_url_used = original_hub_url
        except RuntimeError as exc:
            fallback_url = self._remote_overflow_loopback_fallback_url()
            if not fallback_url or not self._is_name_resolution_failure(exc):
                raise
            fallback_from = original_hub_url
            self.hub_url = fallback_url
            try:
                data = self._post_json("/api/hub/remote-overflow/safe-chat", payload)
                hub_url_used = fallback_url
            finally:
                self.hub_url = original_hub_url

        response_metadata = dict(data.get("metadata", {})) if isinstance(data.get("metadata", {}), dict) else {}
        response_metadata["hub_url"] = hub_url_used
        if fallback_from:
            response_metadata["hub_url_fallback_from"] = fallback_from
            response_metadata["hub_url_fallback_to"] = hub_url_used
        return ChatResponse(
            content=str(data.get("content", "")),
            provider=str(data.get("provider") or "remote-hub-ai"),
            model=str(data.get("model") or self.model),
            metadata=response_metadata,
        )

    def validate_multisession_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask the Hub whether a locally cached multi-session key is still usable.

        This readiness call must not reserve or spend credits. It mirrors the
        local-development loopback fallback used by the remote overflow surface
        so the Chat Console can show Hub key status before attempting paid work.
        """

        request_payload = dict(payload or {})
        original_hub_url = self.hub_url.rstrip("/")
        fallback_from = ""
        try:
            data = self._post_json("/api/hub/v1/credits/multisession-keys/validate", request_payload)
            hub_url_used = original_hub_url
        except RuntimeError as exc:
            fallback_url = self._remote_overflow_loopback_fallback_url()
            if not fallback_url or not self._is_name_resolution_failure(exc):
                raise
            fallback_from = original_hub_url
            self.hub_url = fallback_url
            try:
                data = self._post_json("/api/hub/v1/credits/multisession-keys/validate", request_payload)
                hub_url_used = fallback_url
            finally:
                self.hub_url = original_hub_url

        result = dict(data)
        result["hub_url"] = hub_url_used
        if fallback_from:
            result["hub_url_fallback_from"] = fallback_from
            result["hub_url_fallback_to"] = hub_url_used
        return result

    def _secure_chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        if not hub_transport_is_encrypted_or_loopback(
            self.hub_url,
            allow_insecure_dev_network=self.allow_insecure_dev_network,
        ):
            raise RuntimeError(
                "High-security hub requests require HTTPS, except for local loopback development URLs. "
                "Set MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK=1 only for local Docker/dev networks."
            )

        keypair = generate_hub_session_keypair()
        session_payload = {
            "model": self.model,
            "client_node_id": self.client_node_id,
            "requester_public_key": keypair.public_key,
        }
        session = self._post_json("/api/hub/sessions/start", session_payload)
        session_id = str(session.get("session_id", ""))
        request_id = str(session.get("request_id", ""))
        worker_public_key = str(session.get("worker_public_key", ""))
        if not session_id or not request_id or not worker_public_key:
            raise RuntimeError("Hub did not return a complete high-security session.")
        shared_key = derive_hub_session_key(
            private_key=keypair.private_key,
            peer_public_key=worker_public_key,
            session_id=session_id,
        )

        request_aad = {"session_id": session_id, "request_id": request_id, "direction": "request"}
        encrypted_request = encrypt_hub_envelope(
            {
                "model": self.model,
                "client_node_id": self.client_node_id,
                "messages": [_message_payload(message) for message in messages],
            },
            key=shared_key,
            aad=request_aad,
        )
        relay = self._post_json(
            "/api/hub/sessions/chat",
            {
                "session_id": session_id,
                "request_id": request_id,
                "envelope": encrypted_request,
            },
        )
        response_envelope = relay.get("response_envelope")
        if not isinstance(response_envelope, dict):
            raise RuntimeError("Hub did not return an encrypted worker response.")
        response_aad = {"session_id": session_id, "request_id": request_id, "direction": "response"}
        decrypted = decrypt_hub_envelope(response_envelope, key=shared_key, aad=response_aad)

        metadata = dict(decrypted.get("metadata", {})) if isinstance(decrypted.get("metadata", {}), dict) else {}
        relay_metadata = dict(relay.get("metadata", {})) if isinstance(relay.get("metadata", {}), dict) else {}
        hub_meta = dict(relay_metadata.get("hub", {})) if isinstance(relay_metadata.get("hub", {}), dict) else {}
        metadata["hub"] = {
            **hub_meta,
            **(dict(metadata.get("hub", {})) if isinstance(metadata.get("hub"), dict) else {}),
            "session_id": session_id,
            "request_id": request_id,
            "security_mode": "high-security",
            "hub_blind": True,
            "encryption_profile": HUB_SECURITY_PROFILE,
        }
        metadata.setdefault("hub_url", self.hub_url.rstrip("/"))
        return ChatResponse(
            content=str(decrypted.get("content", "")),
            provider="hub",
            model=str(decrypted.get("model") or self.model),
            metadata=metadata,
        )

    def _legacy_plaintext_chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        payload = {
            "model": self.model,
            "client_node_id": self.client_node_id,
            "messages": [_message_payload(message) for message in messages],
            "high_security": False,
        }
        data = self._post_json("/api/hub/chat", payload)
        metadata = dict(data.get("metadata", {})) if isinstance(data.get("metadata", {}), dict) else {}
        metadata.setdefault("hub_url", self.hub_url.rstrip("/"))
        metadata.setdefault("hub", {})
        if isinstance(metadata["hub"], dict):
            metadata["hub"].setdefault("security_mode", "legacy-plaintext")
            metadata["hub"].setdefault("hub_blind", False)
        return ChatResponse(
            content=str(data.get("content", "")),
            provider=str(data.get("provider") or self.name),
            model=str(data.get("model") or self.model),
            metadata=metadata,
        )

    def _remote_overflow_loopback_fallback_url(self) -> str:
        parsed = urlparse(self.hub_url)
        host = (parsed.hostname or "").strip().lower()
        if host in {"", "127.0.0.1", "localhost", "::1"}:
            return ""
        if parsed.scheme not in {"http", "https"}:
            return ""
        port = parsed.port or 8770
        return f"http://127.0.0.1:{port}"

    @staticmethod
    def _is_name_resolution_failure(exc: BaseException) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "getaddrinfo failed",
                "name or service not known",
                "nodename nor servname provided",
                "temporary failure in name resolution",
            )
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.hub_url.rstrip("/") + path
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(1.0, float(self.timeout_s or 600.0))) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub request failed for {url} with HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub request failed for {url}: {exc}") from exc

        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

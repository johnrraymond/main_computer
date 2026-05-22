from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.energy import EnergyCreditLedger
from main_computer.hub_security import (
    HUB_SECURITY_PROFILE,
    decrypt_hub_envelope,
    derive_hub_session_key,
    encrypt_hub_envelope,
    generate_hub_session_keypair,
    hub_transport_is_encrypted_or_loopback,
)
from main_computer.models import ChatAttachment, ChatMessage, ChatResponse


DEFAULT_HUB_PORT = 8770
DEFAULT_HUB_WORKER_PORT = 8771
HUB_WORKER_CHAT_PATH = "/api/hub/worker/chat"
HUB_WORKER_SESSION_START_PATH = "/api/hub/worker/sessions/start"
HUB_WORKER_SESSION_CHAT_PATH = "/api/hub/worker/sessions/chat"


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _clean_node_id(value: str, *, default: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    return text or default


def _stable_request_id(payload: dict[str, Any]) -> str:
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    stamp = str(time.time_ns()).encode("ascii")
    return "hub_" + hashlib.sha256(seed + stamp).hexdigest()[:20]


def _stable_session_id(request_id: str, payload: dict[str, Any]) -> str:
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    stamp = str(time.time_ns()).encode("ascii")
    return "sess_" + hashlib.sha256(request_id.encode("utf-8") + seed + stamp).hexdigest()[:24]


def _require_allowed_transport(url: str, *, role: str, allow_insecure_dev_network: bool = False) -> None:
    if not hub_transport_is_encrypted_or_loopback(
        url,
        allow_insecure_dev_network=allow_insecure_dev_network,
    ):
        raise ValueError(
            f"{role} endpoint must use HTTPS, except for local loopback development URLs. "
            "Set MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK=1 only for local Docker/dev networks."
        )


def chat_message_to_dict(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "attachments": [asdict(attachment) for attachment in message.attachments],
    }


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


def chat_response_from_dict(payload: dict[str, Any], *, default_provider: str, default_model: str) -> ChatResponse:
    return ChatResponse(
        content=str(payload.get("content", "")),
        provider=str(payload.get("provider") or default_provider),
        model=str(payload.get("model") or default_model),
        metadata=dict(payload.get("metadata", {})) if isinstance(payload.get("metadata", {}), dict) else {},
    )


@dataclass(frozen=True)
class HubWorker:
    node_id: str
    endpoint: str
    model: str = ""
    status: str = "available"
    credits_per_request: int = 1
    registered_at: str = ""
    last_seen_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HubUpstream:
    node_id: str
    endpoint: str
    status: str = "available"
    credits_per_request: int = 1
    registered_at: str = ""
    last_seen_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HubRegistry:
    """Tiny JSON-backed worker registry used by the hub server.

    The registry stores routing metadata only. In high-security mode the hub
    never receives prompt-bearing messages; it only brokers temporary public keys
    and relays authenticated encrypted envelopes between requester and worker.
    """

    def __init__(self, root: Path, *, allow_insecure_dev_network: bool = False) -> None:
        self.root = root
        self.path = root / "hub_workers.json"
        self.allow_insecure_dev_network = bool(allow_insecure_dev_network)
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        data = self._load()
        return {
            "ok": True,
            "hub": data.get("hub", {}),
            "workers": data.get("workers", []),
            "worker_count": len(data.get("workers", [])),
            "upstream_hubs": data.get("upstream_hubs", []),
            "upstream_count": len(data.get("upstream_hubs", [])),
        }

    def register_worker(
        self,
        *,
        node_id: str,
        endpoint: str,
        model: str = "",
        credits_per_request: int = 1,
    ) -> HubWorker:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_endpoint = str(endpoint or "").strip().rstrip("/")
        if not clean_endpoint:
            raise ValueError("Worker endpoint is required.")
        _require_allowed_transport(
            clean_endpoint,
            role="Worker",
            allow_insecure_dev_network=self.allow_insecure_dev_network,
        )
        now = _utc_now()
        credit_price = max(1, int(credits_per_request or 1))
        with self._lock:
            data = self._load()
            workers = [item for item in data["workers"] if item.get("node_id") != clean_node_id]
            existing = next((item for item in data["workers"] if item.get("node_id") == clean_node_id), {})
            registered_at = str(existing.get("registered_at") or now)
            worker = HubWorker(
                node_id=clean_node_id,
                endpoint=clean_endpoint,
                model=str(model or existing.get("model") or "").strip(),
                status="available",
                credits_per_request=credit_price,
                registered_at=registered_at,
                last_seen_at=now,
            )
            workers.append(worker.as_dict())
            data["workers"] = sorted(workers, key=lambda item: str(item.get("node_id", "")))
            self._save(data)
            return worker

    def register_upstream_hub(
        self,
        *,
        node_id: str,
        endpoint: str,
        credits_per_request: int = 1,
    ) -> HubUpstream:
        clean_node_id = _clean_node_id(node_id, default="upstream-hub")
        clean_endpoint = str(endpoint or "").strip().rstrip("/")
        if not clean_endpoint:
            raise ValueError("Upstream hub endpoint is required.")
        _require_allowed_transport(
            clean_endpoint,
            role="Upstream hub",
            allow_insecure_dev_network=self.allow_insecure_dev_network,
        )
        now = _utc_now()
        credit_price = max(1, int(credits_per_request or 1))
        with self._lock:
            data = self._load()
            upstreams = [item for item in data["upstream_hubs"] if item.get("node_id") != clean_node_id]
            existing = next((item for item in data["upstream_hubs"] if item.get("node_id") == clean_node_id), {})
            registered_at = str(existing.get("registered_at") or now)
            upstream = HubUpstream(
                node_id=clean_node_id,
                endpoint=clean_endpoint,
                status="available",
                credits_per_request=credit_price,
                registered_at=registered_at,
                last_seen_at=now,
            )
            upstreams.append(upstream.as_dict())
            data["upstream_hubs"] = sorted(upstreams, key=lambda item: str(item.get("node_id", "")))
            self._save(data)
            return upstream

    def mark_upstream_hub(self, node_id: str, *, status: str) -> None:
        clean_node_id = _clean_node_id(node_id, default="upstream-hub")
        with self._lock:
            data = self._load()
            changed = False
            for upstream in data["upstream_hubs"]:
                if upstream.get("node_id") == clean_node_id:
                    upstream["status"] = status
                    upstream["last_seen_at"] = _utc_now()
                    changed = True
            if changed:
                self._save(data)

    def select_upstream_hub(self) -> HubUpstream | None:
        data = self._load()
        available: list[HubUpstream] = []
        for item in data["upstream_hubs"]:
            if str(item.get("status", "available")) not in {"available", "configured"}:
                continue
            available.append(
                HubUpstream(
                    node_id=str(item.get("node_id", "")),
                    endpoint=str(item.get("endpoint", "")).rstrip("/"),
                    status=str(item.get("status", "available")),
                    credits_per_request=max(1, int(item.get("credits_per_request", 1) or 1)),
                    registered_at=str(item.get("registered_at", "")),
                    last_seen_at=str(item.get("last_seen_at", "")),
                )
            )
        return sorted(available, key=lambda upstream: upstream.last_seen_at or upstream.registered_at)[0] if available else None

    def mark_worker(self, node_id: str, *, status: str) -> None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        with self._lock:
            data = self._load()
            changed = False
            for worker in data["workers"]:
                if worker.get("node_id") == clean_node_id:
                    worker["status"] = status
                    worker["last_seen_at"] = _utc_now()
                    changed = True
            if changed:
                self._save(data)

    def select_worker(self, model: str = "") -> HubWorker | None:
        desired = str(model or "").strip()
        data = self._load()
        available: list[HubWorker] = []
        for item in data["workers"]:
            if str(item.get("status", "available")) not in {"available", "configured"}:
                continue
            worker_model = str(item.get("model", "") or "")
            if desired and worker_model and worker_model != desired:
                continue
            available.append(
                HubWorker(
                    node_id=str(item.get("node_id", "")),
                    endpoint=str(item.get("endpoint", "")).rstrip("/"),
                    model=worker_model,
                    status=str(item.get("status", "available")),
                    credits_per_request=max(1, int(item.get("credits_per_request", 1) or 1)),
                    registered_at=str(item.get("registered_at", "")),
                    last_seen_at=str(item.get("last_seen_at", "")),
                )
            )
        if not available and desired:
            return self.select_worker("")
        return sorted(available, key=lambda worker: worker.last_seen_at or worker.registered_at)[0] if available else None

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return self._normalize(data)
            except (OSError, json.JSONDecodeError):
                pass
        return self._normalize({})

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(self._normalize(data), ensure_ascii=False, indent=2), encoding="utf-8")

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        hub = data.get("hub") if isinstance(data.get("hub"), dict) else {}
        created_at = str(hub.get("created_at") or _utc_now())
        workers = data.get("workers") if isinstance(data.get("workers"), list) else []
        normalized_workers: list[dict[str, Any]] = []
        for item in workers:
            if not isinstance(item, dict):
                continue
            endpoint = str(item.get("endpoint", "")).strip().rstrip("/")
            node_id = _clean_node_id(str(item.get("node_id", "")), default="")
            if not node_id or not endpoint:
                continue
            normalized_workers.append(
                {
                    "node_id": node_id,
                    "endpoint": endpoint,
                    "model": str(item.get("model", "") or ""),
                    "status": str(item.get("status", "available") or "available"),
                    "credits_per_request": max(1, int(item.get("credits_per_request", 1) or 1)),
                    "registered_at": str(item.get("registered_at") or created_at),
                    "last_seen_at": str(item.get("last_seen_at") or item.get("registered_at") or created_at),
                }
            )
        upstream_hubs = data.get("upstream_hubs") if isinstance(data.get("upstream_hubs"), list) else []
        normalized_upstreams: list[dict[str, Any]] = []
        for item in upstream_hubs:
            if not isinstance(item, dict):
                continue
            endpoint = str(item.get("endpoint", "")).strip().rstrip("/")
            node_id = _clean_node_id(str(item.get("node_id", "")), default="")
            if not node_id or not endpoint:
                continue
            normalized_upstreams.append(
                {
                    "node_id": node_id,
                    "endpoint": endpoint,
                    "status": str(item.get("status", "available") or "available"),
                    "credits_per_request": max(1, int(item.get("credits_per_request", 1) or 1)),
                    "registered_at": str(item.get("registered_at") or created_at),
                    "last_seen_at": str(item.get("last_seen_at") or item.get("registered_at") or created_at),
                }
            )
        return {
            "hub": {
                "name": str(hub.get("name") or "main-computer-hub"),
                "created_at": created_at,
                "settlement": "batched-worker-claim",
                "security_profile": HUB_SECURITY_PROFILE,
            },
            "workers": sorted(normalized_workers, key=lambda item: item["node_id"]),
            "upstream_hubs": sorted(normalized_upstreams, key=lambda item: item["node_id"]),
        }


class HubDispatcher:
    def __init__(
        self,
        registry: HubRegistry,
        ledger: EnergyCreditLedger,
        *,
        timeout_s: float = 600.0,
        allow_insecure_dev_network: bool = False,
    ) -> None:
        self.registry = registry
        self.ledger = ledger
        self.timeout_s = max(1.0, float(timeout_s or 600.0))
        self.allow_insecure_dev_network = bool(allow_insecure_dev_network)
        self._secure_sessions: dict[str, dict[str, Any]] = {}
        self._session_lock = threading.Lock()

    def chat(
        self,
        *,
        messages: Sequence[ChatMessage],
        model: str = "",
        client_node_id: str = "main-computer-client",
        hop_count: int = 0,
    ) -> ChatResponse:
        request_payload = {
            "messages": [chat_message_to_dict(message) for message in messages],
            "model": model,
            "client_node_id": client_node_id,
        }
        request_id = _stable_request_id(request_payload)
        worker = self.registry.select_worker(model)
        if worker is None:
            return self._forward_to_upstream(
                request_id=request_id,
                messages=request_payload["messages"],
                model=model,
                client_node_id=client_node_id,
                hop_count=hop_count,
            )

        payload = {
            "request_id": request_id,
            "model": model or worker.model,
            "client_node_id": _clean_node_id(client_node_id, default="main-computer-client"),
            "messages": request_payload["messages"],
            "energy": {
                "credits": worker.credits_per_request,
                "settlement": "batched-worker-claim",
                "memo": f"hub request {request_id}",
            },
        }
        try:
            response_payload = self._post_json(worker.endpoint + HUB_WORKER_CHAT_PATH, payload)
            response = chat_response_from_dict(
                response_payload,
                default_provider="hub-worker",
                default_model=worker.model or model or "hub-worker-model",
            )
        except Exception:
            self.registry.mark_worker(worker.node_id, status="offline")
            raise

        energy_status = self.ledger.queue_worker_payout(
            worker.node_id,
            worker.credits_per_request,
            memo=f"hub request {request_id}",
            request_id=request_id,
        )
        self.registry.mark_worker(worker.node_id, status="available")
        metadata = dict(response.metadata)
        metadata["hub"] = {
            "request_id": request_id,
            "worker_node_id": worker.node_id,
            "worker_endpoint": worker.endpoint,
            "credits_queued": worker.credits_per_request,
            "settlement": "batched-worker-claim",
            "payout_queue": energy_status.get("payout_queue", {}),
            "security_mode": "legacy-plaintext",
            "hub_blind": False,
        }
        return ChatResponse(
            content=response.content,
            provider="hub",
            model=response.model,
            metadata=metadata,
        )

    def start_secure_session(
        self,
        *,
        requester_public_key: str,
        model: str = "",
        client_node_id: str = "main-computer-client",
        hop_count: int = 0,
    ) -> dict[str, Any]:
        request_payload = {
            "requester_public_key": requester_public_key,
            "model": model,
            "client_node_id": _clean_node_id(client_node_id, default="main-computer-client"),
            "hop_count": hop_count,
        }
        request_id = _stable_request_id(request_payload)
        worker = self.registry.select_worker(model)
        if worker is None:
            return self._start_upstream_secure_session(
                request_id=request_id,
                requester_public_key=requester_public_key,
                model=model,
                client_node_id=client_node_id,
                hop_count=hop_count,
            )

        session_id = _stable_session_id(request_id, request_payload)
        payload = {
            "request_id": request_id,
            "session_id": session_id,
            "model": model or worker.model,
            "client_node_id": request_payload["client_node_id"],
            "requester_public_key": requester_public_key,
            "energy": {
                "credits": worker.credits_per_request,
                "settlement": "batched-worker-claim",
                "memo": f"hub request {request_id}",
            },
        }
        try:
            worker_session = self._post_json(worker.endpoint + HUB_WORKER_SESSION_START_PATH, payload)
        except Exception:
            self.registry.mark_worker(worker.node_id, status="offline")
            raise

        worker_public_key = str(worker_session.get("worker_public_key") or "")
        if not worker_public_key:
            raise RuntimeError("Hub worker did not return a temporary public key.")
        with self._session_lock:
            self._secure_sessions[session_id] = {
                "kind": "worker",
                "request_id": request_id,
                "worker": worker.as_dict(),
                "credits": worker.credits_per_request,
                "created_at": _utc_now(),
            }
        self.registry.mark_worker(worker.node_id, status="available")
        return {
            "ok": True,
            "mode": "high-security",
            "session_id": session_id,
            "request_id": request_id,
            "worker_public_key": worker_public_key,
            "encryption": {
                "profile": HUB_SECURITY_PROFILE,
                "temporary_public_keys": True,
                "hub_blind": True,
            },
            "metadata": {
                "hub": {
                    "request_id": request_id,
                    "worker_node_id": worker.node_id,
                    "worker_endpoint": worker.endpoint,
                    "credits_queued": worker.credits_per_request,
                    "settlement": "batched-worker-claim",
                    "security_mode": "high-security",
                    "hub_blind": True,
                    "encryption_profile": HUB_SECURITY_PROFILE,
                }
            },
        }

    def secure_chat(self, *, session_id: str, request_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        session = self._session(session_id)
        stored_request_id = str(session.get("request_id", ""))
        if stored_request_id and request_id and stored_request_id != request_id:
            raise ValueError("Hub session request id mismatch.")
        if session.get("kind") == "upstream":
            return self._secure_chat_upstream(session=session, envelope=envelope)
        return self._secure_chat_worker(session=session, envelope=envelope)

    def _secure_chat_worker(self, *, session: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        worker_data = dict(session.get("worker", {}))
        worker = HubWorker(
            node_id=str(worker_data.get("node_id", "")),
            endpoint=str(worker_data.get("endpoint", "")).rstrip("/"),
            model=str(worker_data.get("model", "")),
            status=str(worker_data.get("status", "available")),
            credits_per_request=max(1, int(worker_data.get("credits_per_request", session.get("credits", 1)) or 1)),
            registered_at=str(worker_data.get("registered_at", "")),
            last_seen_at=str(worker_data.get("last_seen_at", "")),
        )
        request_id = str(session.get("request_id", ""))
        try:
            worker_response = self._post_json(
                worker.endpoint + HUB_WORKER_SESSION_CHAT_PATH,
                {
                    "session_id": session["session_id"],
                    "request_id": request_id,
                    "envelope": envelope,
                },
            )
        except Exception:
            self.registry.mark_worker(worker.node_id, status="offline")
            raise

        energy_status = self.ledger.queue_worker_payout(
            worker.node_id,
            worker.credits_per_request,
            memo=f"hub request {request_id}",
            request_id=request_id,
        )
        self.registry.mark_worker(worker.node_id, status="available")
        hub_meta = {
            "request_id": request_id,
            "worker_node_id": worker.node_id,
            "worker_endpoint": worker.endpoint,
            "credits_queued": worker.credits_per_request,
            "settlement": "batched-worker-claim",
            "payout_queue": energy_status.get("payout_queue", {}),
            "security_mode": "high-security",
            "hub_blind": True,
            "encryption_profile": HUB_SECURITY_PROFILE,
        }
        return {
            "ok": True,
            "mode": "high-security",
            "session_id": session["session_id"],
            "request_id": request_id,
            "response_envelope": worker_response.get("response_envelope"),
            "metadata": {"hub": hub_meta},
        }

    def _start_upstream_secure_session(
        self,
        *,
        request_id: str,
        requester_public_key: str,
        model: str,
        client_node_id: str,
        hop_count: int,
    ) -> dict[str, Any]:
        if hop_count >= 8:
            raise RuntimeError("Hub forwarding loop detected: hop limit exceeded.")
        upstream = self.registry.select_upstream_hub()
        if upstream is None:
            raise RuntimeError("No hub workers or upstream hubs are registered or available.")

        payload = {
            "model": model,
            "client_node_id": _clean_node_id(client_node_id, default="main-computer-client"),
            "requester_public_key": requester_public_key,
            "hop_count": hop_count + 1,
            "forwarded_by": request_id,
        }
        try:
            upstream_session = self._post_json(upstream.endpoint.rstrip("/") + "/api/hub/sessions/start", payload)
        except Exception:
            self.registry.mark_upstream_hub(upstream.node_id, status="offline")
            raise

        upstream_session_id = str(upstream_session.get("session_id", ""))
        upstream_request_id = str(upstream_session.get("request_id", ""))
        if not upstream_session_id or not upstream_request_id:
            raise RuntimeError("Upstream hub did not return a complete high-security session.")
        with self._session_lock:
            self._secure_sessions[upstream_session_id] = {
                "kind": "upstream",
                "request_id": upstream_request_id,
                "local_request_id": request_id,
                "upstream": upstream.as_dict(),
                "upstream_session_id": upstream_session_id,
                "upstream_request_id": upstream_request_id,
                "credits": upstream.credits_per_request,
                "created_at": _utc_now(),
            }
        self.registry.mark_upstream_hub(upstream.node_id, status="available")
        upstream_meta = {}
        if isinstance(upstream_session.get("metadata"), dict):
            upstream_meta = dict(upstream_session["metadata"].get("hub", {})) if isinstance(upstream_session["metadata"].get("hub"), dict) else {}
        return {
            "ok": True,
            "mode": "high-security",
            "session_id": upstream_session_id,
            "request_id": upstream_request_id,
            "worker_public_key": str(upstream_session.get("worker_public_key", "")),
            "encryption": {
                "profile": HUB_SECURITY_PROFILE,
                "temporary_public_keys": True,
                "hub_blind": True,
            },
            "metadata": {
                "hub": {
                    **upstream_meta,
                    "request_id": upstream_request_id,
                    "local_request_id": request_id,
                    "upstream_hub_node_id": upstream.node_id,
                    "upstream_hub_endpoint": upstream.endpoint,
                    "forwarded": True,
                    "credits_queued": upstream.credits_per_request,
                    "settlement": "batched-worker-claim",
                    "security_mode": "high-security",
                    "hub_blind": True,
                    "encryption_profile": HUB_SECURITY_PROFILE,
                }
            },
        }

    def _secure_chat_upstream(self, *, session: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        upstream_data = dict(session.get("upstream", {}))
        upstream = HubUpstream(
            node_id=str(upstream_data.get("node_id", "")),
            endpoint=str(upstream_data.get("endpoint", "")).rstrip("/"),
            status=str(upstream_data.get("status", "available")),
            credits_per_request=max(1, int(upstream_data.get("credits_per_request", session.get("credits", 1)) or 1)),
            registered_at=str(upstream_data.get("registered_at", "")),
            last_seen_at=str(upstream_data.get("last_seen_at", "")),
        )
        request_id = str(session.get("request_id", ""))
        local_request_id = str(session.get("local_request_id", request_id))
        try:
            upstream_response = self._post_json(
                upstream.endpoint.rstrip("/") + "/api/hub/sessions/chat",
                {
                    "session_id": str(session.get("upstream_session_id", "")),
                    "request_id": str(session.get("upstream_request_id", "")),
                    "envelope": envelope,
                },
            )
        except Exception:
            self.registry.mark_upstream_hub(upstream.node_id, status="offline")
            raise

        energy_status = self.ledger.queue_upstream_hub_payout(
            upstream.node_id,
            upstream.credits_per_request,
            memo=f"hub upstream request {local_request_id}",
            request_id=local_request_id,
        )
        self.registry.mark_upstream_hub(upstream.node_id, status="available")
        upstream_meta = {}
        if isinstance(upstream_response.get("metadata"), dict):
            upstream_meta = dict(upstream_response["metadata"].get("hub", {})) if isinstance(upstream_response["metadata"].get("hub"), dict) else {}
        hub_meta = {
            **upstream_meta,
            "request_id": request_id,
            "upstream_hub_node_id": upstream.node_id,
            "upstream_hub_endpoint": upstream.endpoint,
            "forwarded": True,
            "credits_queued": upstream.credits_per_request,
            "settlement": "batched-worker-claim",
            "payout_queue": energy_status.get("payout_queue", {}),
            "security_mode": "high-security",
            "hub_blind": True,
            "encryption_profile": HUB_SECURITY_PROFILE,
        }
        return {
            "ok": True,
            "mode": "high-security",
            "session_id": session["session_id"],
            "request_id": request_id,
            "response_envelope": upstream_response.get("response_envelope"),
            "metadata": {"hub": hub_meta},
        }

    def _session(self, session_id: str) -> dict[str, Any]:
        clean = str(session_id or "").strip()
        with self._session_lock:
            session = dict(self._secure_sessions.get(clean, {}))
        if not session:
            raise ValueError("Unknown or expired hub session.")
        session["session_id"] = clean
        return session

    def _forward_to_upstream(
        self,
        *,
        request_id: str,
        messages: list[dict[str, Any]],
        model: str,
        client_node_id: str,
        hop_count: int,
    ) -> ChatResponse:
        if hop_count >= 8:
            raise RuntimeError("Hub forwarding loop detected: hop limit exceeded.")
        upstream = self.registry.select_upstream_hub()
        if upstream is None:
            raise RuntimeError("No hub workers or upstream hubs are registered or available.")

        payload = {
            "model": model,
            "client_node_id": _clean_node_id(client_node_id, default="main-computer-client"),
            "messages": messages,
            "hop_count": hop_count + 1,
            "forwarded_by": request_id,
            "high_security": False,
        }
        try:
            response_payload = self._post_json(upstream.endpoint.rstrip("/") + "/api/hub/chat", payload)
            response = chat_response_from_dict(
                response_payload,
                default_provider="upstream-hub",
                default_model=model or "hub-forwarded-model",
            )
        except Exception:
            self.registry.mark_upstream_hub(upstream.node_id, status="offline")
            raise

        energy_status = self.ledger.queue_upstream_hub_payout(
            upstream.node_id,
            upstream.credits_per_request,
            memo=f"hub upstream request {request_id}",
            request_id=request_id,
        )
        self.registry.mark_upstream_hub(upstream.node_id, status="available")
        metadata = dict(response.metadata)
        upstream_meta = dict(metadata.get("hub", {})) if isinstance(metadata.get("hub"), dict) else {}
        metadata["hub"] = {
            **upstream_meta,
            "request_id": request_id,
            "upstream_hub_node_id": upstream.node_id,
            "upstream_hub_endpoint": upstream.endpoint,
            "forwarded": True,
            "credits_queued": upstream.credits_per_request,
            "settlement": "batched-worker-claim",
            "payout_queue": energy_status.get("payout_queue", {}),
            "security_mode": "legacy-plaintext",
            "hub_blind": False,
        }
        return ChatResponse(
            content=response.content,
            provider="hub",
            model=response.model,
            metadata=metadata,
        )

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        _require_allowed_transport(
            url,
            role="Hub peer",
            allow_insecure_dev_network=self.allow_insecure_dev_network,
        )
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub worker HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub worker is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub peer returned a non-object response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data


class _JsonHandler(BaseHTTPRequestHandler):
    server_version = "MainComputerHub/0.2"

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "verbose", False):
            super().log_message(format, *args)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class HubHttpServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: MainComputerConfig, *, verbose: bool = True) -> None:
        super().__init__(server_address, HubServerHandler)
        hub_root = config.hub_root
        if not hub_root.is_absolute():
            hub_root = Path.cwd().resolve() / hub_root
        self.verbose = verbose
        self.config = config
        self.hub_root = hub_root
        self.registry = HubRegistry(
            hub_root,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        )
        self.energy_ledger = EnergyCreditLedger(hub_root / "energy_credits")
        self.dispatcher = HubDispatcher(
            self.registry,
            self.energy_ledger,
            timeout_s=config.hub_timeout_s,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        )


class HubServerHandler(_JsonHandler):
    server: HubHttpServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/hub/status":
            status = self.server.registry.status()
            status["security"] = {
                "high_security_default": self.server.config.hub_high_security,
                "hub_blind_envelopes": self.server.config.hub_high_security,
                "encryption_profile": HUB_SECURITY_PROFILE,
                "transport": "https-required-except-loopback",
                "allow_insecure_dev_network": self.server.config.hub_allow_insecure_dev_network,
            }
            status["energy"] = self.server.energy_ledger.status()
            self._send_json(status)
            return
        if parsed.path == "/api/hub/payouts":
            node_id = parse_qs(parsed.query).get("node_id", [""])[0]
            try:
                self._send_json(self.server.energy_ledger.payout_summary(node_id))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/hub/workers/register":
                body = self._read_json()
                worker = self.server.registry.register_worker(
                    node_id=str(body.get("node_id", "")),
                    endpoint=str(body.get("endpoint", "")),
                    model=str(body.get("model", "")),
                    credits_per_request=int(body.get("credits_per_request", self.server.config.hub_credits_per_request)),
                )
                self.server.energy_ledger.register_node(worker.node_id, "gpu-worker", worker.endpoint)
                self._send_json({"ok": True, "worker": worker.as_dict(), "hub": self.server.registry.status()})
                return
            if path == "/api/hub/upstreams/register":
                body = self._read_json()
                upstream = self.server.registry.register_upstream_hub(
                    node_id=str(body.get("node_id", "")),
                    endpoint=str(body.get("endpoint", "")),
                    credits_per_request=int(body.get("credits_per_request", self.server.config.hub_credits_per_request)),
                )
                self.server.energy_ledger.register_node(upstream.node_id, "upstream-hub", upstream.endpoint)
                self._send_json({"ok": True, "upstream_hub": upstream.as_dict(), "hub": self.server.registry.status()})
                return
            if path == "/api/hub/sessions/start":
                body = self._read_json()
                requester_public_key = str(body.get("requester_public_key", ""))
                if not requester_public_key:
                    raise ValueError("requester_public_key is required.")
                self._send_json(
                    self.server.dispatcher.start_secure_session(
                        requester_public_key=requester_public_key,
                        model=str(body.get("model", self.server.config.model)),
                        client_node_id=str(body.get("client_node_id", self.server.config.hub_client_node_id)),
                        hop_count=int(body.get("hop_count", 0) or 0),
                    )
                )
                return
            if path == "/api/hub/sessions/chat":
                body = self._read_json()
                envelope = body.get("envelope")
                if not isinstance(envelope, dict):
                    raise ValueError("encrypted envelope is required.")
                self._send_json(
                    self.server.dispatcher.secure_chat(
                        session_id=str(body.get("session_id", "")),
                        request_id=str(body.get("request_id", "")),
                        envelope=envelope,
                    )
                )
                return
            if path == "/api/hub/payouts/claim":
                body = self._read_json()
                self._send_json(
                    self.server.energy_ledger.claim_payouts(
                        node_id=str(body.get("node_id", "")),
                        memo=str(body.get("memo", "")),
                    )
                )
                return
            if path == "/api/hub/chat":
                body = self._read_json()
                if self.server.config.hub_high_security and body.get("high_security") is not False:
                    raise ValueError(
                        "High-security hub mode is enabled; use /api/hub/sessions/start and encrypted envelopes, "
                        "or send high_security=false for an explicit legacy plaintext request."
                    )
                messages_payload = body.get("messages")
                if not messages_payload and body.get("prompt"):
                    messages_payload = [{"role": "user", "content": str(body.get("prompt", ""))}]
                if not isinstance(messages_payload, list):
                    raise ValueError("messages must be a list, or prompt must be supplied.")
                messages = [chat_message_from_dict(item) for item in messages_payload if isinstance(item, dict)]
                response = self.server.dispatcher.chat(
                    messages=messages,
                    model=str(body.get("model", self.server.config.model)),
                    client_node_id=str(body.get("client_node_id", self.server.config.hub_client_node_id)),
                    hop_count=int(body.get("hop_count", 0) or 0),
                )
                self._send_json(
                    {
                        "content": response.content,
                        "provider": response.provider,
                        "model": response.model,
                        "metadata": response.metadata,
                    }
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


class HubWorkerHttpServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        config: MainComputerConfig,
        chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
        *,
        verbose: bool = True,
    ) -> None:
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


def register_worker_with_hub(
    *,
    hub_url: str,
    node_id: str,
    endpoint: str,
    model: str = "",
    credits_per_request: int = 1,
    timeout_s: float = 10.0,
    allow_insecure_dev_network: bool = False,
) -> dict[str, Any]:
    _require_allowed_transport(hub_url, role="Hub", allow_insecure_dev_network=allow_insecure_dev_network)
    _require_allowed_transport(endpoint, role="Worker", allow_insecure_dev_network=allow_insecure_dev_network)
    payload = {
        "node_id": node_id,
        "endpoint": endpoint,
        "model": model,
        "credits_per_request": max(1, int(credits_per_request or 1)),
    }
    request = Request(
        hub_url.rstrip("/") + "/api/hub/workers/register",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=max(1.0, float(timeout_s or 10.0))) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Hub returned a non-object registration response.")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data


def serve_hub(config: MainComputerConfig, host: str = "127.0.0.1", port: int = DEFAULT_HUB_PORT, *, verbose: bool = True) -> None:
    server = HubHttpServer((host, port), config, verbose=verbose)
    scheme_note = "https required for remote peers; local http is allowed for loopback development"
    print(f"Main Computer hub server: http://{host}:{server.server_port}")
    print(f"Hub security: high-security={config.hub_high_security} profile={HUB_SECURITY_PROFILE}; {scheme_note}")
    print(
        "Hub endpoints: GET /api/hub/status, GET /api/hub/payouts?node_id=..., "
        "POST /api/hub/workers/register, POST /api/hub/upstreams/register, "
        "POST /api/hub/sessions/start, POST /api/hub/sessions/chat, POST /api/hub/payouts/claim"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHub server stopped.")
    finally:
        server.server_close()


def serve_hub_worker(
    config: MainComputerConfig,
    chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_HUB_WORKER_PORT,
    hub_url: str | None = None,
    public_endpoint: str | None = None,
    verbose: bool = True,
) -> None:
    server = HubWorkerHttpServer((host, port), config, chat_fn, verbose=verbose)
    endpoint = (public_endpoint or f"http://{host}:{server.server_port}").rstrip("/")
    if hub_url:
        report = register_worker_with_hub(
            hub_url=hub_url,
            node_id=config.hub_worker_node_id,
            endpoint=endpoint,
            model=config.model,
            credits_per_request=config.hub_credits_per_request,
            timeout_s=10.0,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        )
        if verbose:
            print(f"Registered hub worker {config.hub_worker_node_id}: {report.get('worker', {})}")
    print(f"Main Computer hub worker: {endpoint}")
    print(f"Worker security: high-security={config.hub_high_security} profile={HUB_SECURITY_PROFILE}")
    print(f"Worker endpoint: POST {HUB_WORKER_SESSION_START_PATH}, POST {HUB_WORKER_SESSION_CHAT_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHub worker stopped.")
    finally:
        server.server_close()

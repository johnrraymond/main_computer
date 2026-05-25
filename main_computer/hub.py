from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
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
from main_computer.hub_admin_site import HUB_ADMIN_ROUTES, build_admin_bootstrap_payload, render_hub_admin_html
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_plex_models import HubAIRequest, HubWorkerSummary
from main_computer.hub_plex_service import AIRequestPlexService
from main_computer.models import ChatAttachment, ChatMessage, ChatResponse


DEFAULT_HUB_PORT = 8770
DEFAULT_HUB_WORKER_PORT = 8771
HUB_WORKER_CHAT_PATH = "/api/hub/worker/chat"
HUB_WORKER_SESSION_START_PATH = "/api/hub/worker/sessions/start"
HUB_WORKER_SESSION_CHAT_PATH = "/api/hub/worker/sessions/chat"
HUB_WORKER_STALE_AFTER_SECONDS = 90.0
HUB_WORKER_LEASE_SECONDS = 600.0


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
    models: list[str] = field(default_factory=list)
    status: str = "available"
    credits_per_request: int = 1
    registered_at: str = ""
    last_seen_at: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    queue_depth: int = 0
    active_requests: int = 0
    max_concurrency: int = 1
    lease_expires_at: str = ""
    stale: bool = False

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
    """JSON-backed worker registry used by the hub server.

    Phase 2 keeps this class as the stable compatibility surface and extends it
    with worker heartbeats, leases, capacity metadata, and stale-worker
    filtering. The registry still stores routing metadata only; in high-security
    mode prompt-bearing data remains inside encrypted envelopes.
    """

    def __init__(self, root: Path, *, allow_insecure_dev_network: bool = False) -> None:
        self.root = root
        self.path = root / "hub_workers.json"
        self.allow_insecure_dev_network = bool(allow_insecure_dev_network)
        self.worker_stale_after_s = HUB_WORKER_STALE_AFTER_SECONDS
        self.worker_lease_s = HUB_WORKER_LEASE_SECONDS
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        self.expire_stale_workers()
        data = self._load()
        workers = data.get("workers", [])
        available_workers = [
            worker
            for worker in workers
            if str(worker.get("status", "available")) in {"available", "configured"}
            and not bool(worker.get("stale", False))
            and int(worker.get("active_requests", 0) or 0) < int(worker.get("max_concurrency", 1) or 1)
        ]
        stale_workers = [
            worker
            for worker in workers
            if bool(worker.get("stale", False)) or str(worker.get("status", "")).lower() == "stale"
        ]
        return {
            "ok": True,
            "hub": data.get("hub", {}),
            "workers": workers,
            "worker_count": len(workers),
            "available_worker_count": len(available_workers),
            "stale_worker_count": len(stale_workers),
            "upstream_hubs": data.get("upstream_hubs", []),
            "upstream_count": len(data.get("upstream_hubs", [])),
            "leases": {
                "worker_stale_after_seconds": self.worker_stale_after_s,
                "worker_lease_seconds": self.worker_lease_s,
            },
        }

    def register_worker(
        self,
        *,
        node_id: str,
        endpoint: str,
        model: str = "",
        models: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        credits_per_request: int = 1,
        queue_depth: int = 0,
        active_requests: int = 0,
        max_concurrency: int = 1,
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
        clean_models = self._normalize_models(model=model, models=models)
        primary_model = clean_models[0] if clean_models else str(model or "").strip()
        clean_capabilities = dict(capabilities or {})
        clean_max_concurrency = max(1, int(max_concurrency or 1))
        clean_active = min(max(0, int(active_requests or 0)), clean_max_concurrency)
        clean_queue_depth = max(0, int(queue_depth or 0))
        with self._lock:
            data = self._load()
            workers = [item for item in data["workers"] if item.get("node_id") != clean_node_id]
            existing = next((item for item in data["workers"] if item.get("node_id") == clean_node_id), {})
            registered_at = str(existing.get("registered_at") or now)
            worker = HubWorker(
                node_id=clean_node_id,
                endpoint=clean_endpoint,
                model=primary_model,
                models=clean_models,
                status="available" if clean_active < clean_max_concurrency else "busy",
                credits_per_request=credit_price,
                registered_at=registered_at,
                last_seen_at=now,
                capabilities=clean_capabilities,
                queue_depth=clean_queue_depth,
                active_requests=clean_active,
                max_concurrency=clean_max_concurrency,
                lease_expires_at=str(existing.get("lease_expires_at", "") or ""),
                stale=False,
            )
            workers.append(worker.as_dict())
            data["workers"] = sorted(workers, key=lambda item: str(item.get("node_id", "")))
            self._save(data)
            return worker

    def heartbeat_worker(
        self,
        node_id: str,
        *,
        status: str = "available",
        model: str = "",
        models: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        queue_depth: int | None = None,
        active_requests: int | None = None,
        max_concurrency: int | None = None,
    ) -> HubWorker:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        now = _utc_now()
        with self._lock:
            data = self._load()
            for item in data["workers"]:
                if item.get("node_id") != clean_node_id:
                    continue
                current_max = max(1, int(item.get("max_concurrency", 1) or 1))
                next_max = max(1, int(max_concurrency or current_max))
                next_active = max(0, int(item.get("active_requests", 0) or 0)) if active_requests is None else max(0, int(active_requests or 0))
                next_active = min(next_active, next_max)
                clean_status = str(status or item.get("status") or "available").strip().lower()
                if clean_status not in {"available", "configured", "busy", "offline", "draining"}:
                    clean_status = "available"
                if clean_status == "available" and next_active >= next_max:
                    clean_status = "busy"
                item["status"] = clean_status
                item["last_seen_at"] = now
                item["stale"] = False
                item["queue_depth"] = max(0, int(item.get("queue_depth", 0) or 0)) if queue_depth is None else max(0, int(queue_depth or 0))
                item["active_requests"] = next_active
                item["max_concurrency"] = next_max
                if capabilities is not None:
                    item["capabilities"] = dict(capabilities)
                if models is not None or model:
                    clean_models = self._normalize_models(model=model or str(item.get("model", "")), models=models)
                    item["models"] = clean_models
                    item["model"] = clean_models[0] if clean_models else str(model or "")
                worker = self._worker_from_payload(item)
                self._save(data)
                return worker
        raise KeyError(f"Unknown hub worker: {clean_node_id}")

    def get_worker(self, node_id: str) -> HubWorker | None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        self.expire_stale_workers()
        data = self._load()
        for item in data["workers"]:
            if item.get("node_id") == clean_node_id:
                return self._worker_from_payload(item)
        return None

    def expire_stale_workers(self, *, stale_after_s: float | None = None) -> int:
        threshold = self.worker_stale_after_s if stale_after_s is None else max(0.0, float(stale_after_s))
        now = datetime.now(tz=timezone.utc)
        changed = 0
        with self._lock:
            data = self._load()
            for worker in data["workers"]:
                status = str(worker.get("status", "available")).lower()
                if status in {"offline", "draining"}:
                    continue
                last_seen = self._parse_iso(str(worker.get("last_seen_at", "") or worker.get("registered_at", "")))
                lease_expires = self._parse_iso(str(worker.get("lease_expires_at", "") or ""))
                heartbeat_stale = last_seen is not None and (now - last_seen).total_seconds() > threshold
                lease_stale = lease_expires is not None and lease_expires < now and int(worker.get("active_requests", 0) or 0) > 0
                if heartbeat_stale or lease_stale:
                    worker["status"] = "stale"
                    worker["stale"] = True
                    worker["active_requests"] = 0
                    worker["lease_expires_at"] = ""
                    changed += 1
            if changed:
                self._save(data)
        return changed

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
        clean_status = str(status or "available").strip().lower()
        if clean_status not in {"available", "configured", "busy", "offline", "stale", "draining"}:
            clean_status = "available"
        with self._lock:
            data = self._load()
            changed = False
            for worker in data["workers"]:
                if worker.get("node_id") == clean_node_id:
                    worker["status"] = clean_status
                    worker["last_seen_at"] = _utc_now()
                    worker["stale"] = clean_status == "stale"
                    if clean_status in {"offline", "stale", "draining"}:
                        worker["active_requests"] = 0
                        worker["lease_expires_at"] = ""
                    changed = True
            if changed:
                self._save(data)

    def lease_worker(
        self,
        model: str = "",
        *,
        request_id: str = "",
        preferred_node_id: str = "",
        lease_seconds: float | None = None,
    ) -> HubWorker | None:
        desired = str(model or "").strip()
        preferred = _clean_node_id(preferred_node_id, default="") if preferred_node_id else ""
        lease_s = self.worker_lease_s if lease_seconds is None else max(1.0, float(lease_seconds))
        with self._lock:
            self._expire_stale_workers_unlocked(self._load(), stale_after_s=self.worker_stale_after_s)
            data = self._load()
            candidates = [
                item
                for item in data["workers"]
                if self._is_worker_lease_candidate(item, desired=desired, preferred_node_id=preferred, allow_model_fallback=False)
            ]
            if not candidates and desired and not preferred:
                candidates = [
                    item
                    for item in data["workers"]
                    if self._is_worker_lease_candidate(item, desired="", preferred_node_id="", allow_model_fallback=True)
                ]
            if not candidates:
                return None
            worker = sorted(
                candidates,
                key=lambda item: (
                    max(0, int(item.get("queue_depth", 0) or 0)) + max(0, int(item.get("active_requests", 0) or 0)),
                    str(item.get("last_seen_at", "") or item.get("registered_at", "")),
                    str(item.get("node_id", "")),
                ),
            )[0]
            max_concurrency = max(1, int(worker.get("max_concurrency", 1) or 1))
            active = min(max_concurrency, max(0, int(worker.get("active_requests", 0) or 0)) + 1)
            worker["active_requests"] = active
            worker["status"] = "busy" if active >= max_concurrency else "available"
            worker["lease_expires_at"] = (datetime.now(tz=timezone.utc) + timedelta(seconds=lease_s)).isoformat()
            worker["last_seen_at"] = _utc_now()
            worker["stale"] = False
            if request_id:
                worker["last_request_id"] = str(request_id)
            self._save(data)
            return self._worker_from_payload(worker)

    def release_worker(self, node_id: str, *, request_id: str = "", success: bool = True) -> None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        with self._lock:
            data = self._load()
            changed = False
            for worker in data["workers"]:
                if worker.get("node_id") != clean_node_id:
                    continue
                max_concurrency = max(1, int(worker.get("max_concurrency", 1) or 1))
                active = max(0, int(worker.get("active_requests", 0) or 0) - 1)
                worker["active_requests"] = active
                worker["status"] = "available" if success else "offline"
                if active >= max_concurrency:
                    worker["status"] = "busy"
                if not success:
                    worker["active_requests"] = 0
                    worker["lease_expires_at"] = ""
                elif active == 0:
                    worker["lease_expires_at"] = ""
                worker["last_seen_at"] = _utc_now()
                worker["stale"] = False
                if request_id:
                    worker["last_request_id"] = str(request_id)
                changed = True
            if changed:
                self._save(data)

    def select_worker(self, model: str = "") -> HubWorker | None:
        desired = str(model or "").strip()
        self.expire_stale_workers()
        data = self._load()
        available: list[HubWorker] = []
        for item in data["workers"]:
            if not self._is_worker_lease_candidate(item, desired=desired, preferred_node_id="", allow_model_fallback=False):
                continue
            available.append(self._worker_from_payload(item))
        if not available and desired:
            return self.select_worker("")
        return sorted(
            available,
            key=lambda worker: (worker.queue_depth + worker.active_requests, worker.last_seen_at or worker.registered_at),
        )[0] if available else None

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
            models = self._normalize_models(model=str(item.get("model", "") or ""), models=item.get("models") if isinstance(item.get("models"), list) else None)
            primary_model = models[0] if models else str(item.get("model", "") or "")
            max_concurrency = max(1, int(item.get("max_concurrency", 1) or 1))
            active_requests = min(max_concurrency, max(0, int(item.get("active_requests", 0) or 0)))
            status = str(item.get("status", "available") or "available").lower()
            if status not in {"available", "configured", "busy", "offline", "stale", "draining"}:
                status = "available"
            normalized_workers.append(
                {
                    "node_id": node_id,
                    "endpoint": endpoint,
                    "model": primary_model,
                    "models": models,
                    "status": status,
                    "credits_per_request": max(1, int(item.get("credits_per_request", 1) or 1)),
                    "registered_at": str(item.get("registered_at") or created_at),
                    "last_seen_at": str(item.get("last_seen_at") or item.get("registered_at") or created_at),
                    "capabilities": dict(item.get("capabilities", {})) if isinstance(item.get("capabilities"), dict) else {},
                    "queue_depth": max(0, int(item.get("queue_depth", 0) or 0)),
                    "active_requests": active_requests,
                    "max_concurrency": max_concurrency,
                    "lease_expires_at": str(item.get("lease_expires_at", "") or ""),
                    "stale": bool(item.get("stale", False)) or status == "stale",
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

    def _expire_stale_workers_unlocked(self, data: dict[str, Any], *, stale_after_s: float) -> int:
        threshold = max(0.0, float(stale_after_s))
        now = datetime.now(tz=timezone.utc)
        changed = 0
        for worker in data["workers"]:
            status = str(worker.get("status", "available")).lower()
            if status in {"offline", "draining"}:
                continue
            last_seen = self._parse_iso(str(worker.get("last_seen_at", "") or worker.get("registered_at", "")))
            lease_expires = self._parse_iso(str(worker.get("lease_expires_at", "") or ""))
            heartbeat_stale = last_seen is not None and (now - last_seen).total_seconds() > threshold
            lease_stale = lease_expires is not None and lease_expires < now and int(worker.get("active_requests", 0) or 0) > 0
            if heartbeat_stale or lease_stale:
                worker["status"] = "stale"
                worker["stale"] = True
                worker["active_requests"] = 0
                worker["lease_expires_at"] = ""
                changed += 1
        if changed:
            self._save(data)
        return changed

    def _is_worker_lease_candidate(
        self,
        item: dict[str, Any],
        *,
        desired: str,
        preferred_node_id: str,
        allow_model_fallback: bool,
    ) -> bool:
        status = str(item.get("status", "available")).lower()
        if status not in {"available", "configured"}:
            return False
        if bool(item.get("stale", False)):
            return False
        active = max(0, int(item.get("active_requests", 0) or 0))
        max_concurrency = max(1, int(item.get("max_concurrency", 1) or 1))
        if active >= max_concurrency:
            return False
        node_id = str(item.get("node_id", ""))
        if preferred_node_id and node_id != preferred_node_id:
            return False
        if not desired or allow_model_fallback:
            return True
        worker_models = [str(model).strip() for model in item.get("models", []) if str(model).strip()]
        worker_model = str(item.get("model", "") or "").strip()
        if worker_model and worker_model not in worker_models:
            worker_models.append(worker_model)
        return desired in worker_models

    def _worker_from_payload(self, item: dict[str, Any]) -> HubWorker:
        models = [str(model).strip() for model in item.get("models", []) if str(model).strip()]
        model = str(item.get("model", "") or "")
        if model and model not in models:
            models.insert(0, model)
        return HubWorker(
            node_id=str(item.get("node_id", "")),
            endpoint=str(item.get("endpoint", "")).rstrip("/"),
            model=model,
            models=models,
            status=str(item.get("status", "available")),
            credits_per_request=max(1, int(item.get("credits_per_request", 1) or 1)),
            registered_at=str(item.get("registered_at", "")),
            last_seen_at=str(item.get("last_seen_at", "")),
            capabilities=dict(item.get("capabilities", {})) if isinstance(item.get("capabilities"), dict) else {},
            queue_depth=max(0, int(item.get("queue_depth", 0) or 0)),
            active_requests=max(0, int(item.get("active_requests", 0) or 0)),
            max_concurrency=max(1, int(item.get("max_concurrency", 1) or 1)),
            lease_expires_at=str(item.get("lease_expires_at", "") or ""),
            stale=bool(item.get("stale", False)) or str(item.get("status", "")).lower() == "stale",
        )

    @staticmethod
    def _normalize_models(*, model: str = "", models: list[str] | None = None) -> list[str]:
        result: list[str] = []
        for raw in [model, *(models or [])]:
            clean = str(raw or "").strip()
            if clean and clean not in result:
                result.append(clean)
        return result

    @staticmethod
    def _parse_iso(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


class HubDispatcher:
    """Compatibility facade around the hub AI request plexing service."""

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
        self.plex_service = AIRequestPlexService(
            registry,
            ledger,
            root=registry.root,
            timeout_s=self.timeout_s,
            allow_insecure_dev_network=self.allow_insecure_dev_network,
        )

    def chat(
        self,
        *,
        messages: Sequence[ChatMessage],
        model: str = "",
        client_node_id: str = "main-computer-client",
        hop_count: int = 0,
    ) -> ChatResponse:
        request = HubAIRequest.from_messages(
            list(messages),
            model=model,
            client_node_id=client_node_id,
            hop_count=hop_count,
        )
        return self.plex_service.dispatch_sync(request)

    def submit(self, request: HubAIRequest) -> dict[str, Any]:
        return self.plex_service.submit(request).as_dict()

    def get_request_status(self, request_id: str) -> dict[str, Any]:
        return self.plex_service.get_status(request_id).as_dict()

    def cancel_request(self, request_id: str) -> dict[str, Any]:
        return self.plex_service.cancel(request_id).as_dict()

    def list_requests(self, *, limit: int = 100, states: set[str] | None = None) -> list[dict[str, Any]]:
        return [status.as_dict() for status in self.plex_service.list_statuses(limit=limit, states=states)]

    def get_request_events(self, request_id: str) -> list[dict[str, Any]]:
        return self.plex_service.get_events(request_id)

    def metrics(self) -> dict[str, Any]:
        return self.plex_service.metrics()

    def start_secure_session(
        self,
        *,
        requester_public_key: str,
        model: str = "",
        client_node_id: str = "main-computer-client",
        hop_count: int = 0,
    ) -> dict[str, Any]:
        return self.plex_service.start_secure_session(
            requester_public_key=requester_public_key,
            model=model,
            client_node_id=client_node_id,
            hop_count=hop_count,
        )

    def secure_chat(self, *, session_id: str, request_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.plex_service.secure_chat(session_id=session_id, request_id=request_id, envelope=envelope)


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

    def _send_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(payload)


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
        self.credit_ledger = HubCreditLedger(hub_root / "compute_credits")
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
        path = parsed.path
        query = parse_qs(parsed.query)
        if path in HUB_ADMIN_ROUTES:
            html = render_hub_admin_html()
            self._send_bytes(html.encode("utf-8"), content_type="text/html; charset=utf-8")
            return
        if path == "/api/hub/v1/admin/bootstrap":
            self._send_json(
                build_admin_bootstrap_payload(
                    config=self.server.config,
                    registry=self.server.registry,
                    dispatcher=self.server.dispatcher,
                    energy_ledger=self.server.energy_ledger,
                    credit_ledger=self.server.credit_ledger,
                )
            )
            return
        if path == "/api/hub/v1/health":
            self._send_json(
                {
                    "ok": True,
                    "service": "main-computer-hub",
                    "api_version": "v1",
                    "security_profile": HUB_SECURITY_PROFILE,
                }
            )
            return
        if path in {"/api/hub/status", "/api/hub/v1/status"}:
            status = self.server.registry.status()
            status["api_version"] = "v1" if path.startswith("/api/hub/v1/") else "legacy"
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
        if path == "/api/hub/v1/metrics":
            self._send_json(self.server.dispatcher.metrics())
            return
        if path == "/api/hub/v1/workers":
            include_endpoint = str(query.get("debug", [""])[0]).lower() in {"1", "true", "yes"}
            status = self.server.registry.status()
            workers = [
                HubWorkerSummary.from_worker_payload(worker, include_endpoint=include_endpoint).as_dict()
                for worker in status.get("workers", [])
                if isinstance(worker, dict)
            ]
            self._send_json(
                {
                    "ok": True,
                    "workers": workers,
                    "worker_count": len(workers),
                    "available_worker_count": status.get("available_worker_count", 0),
                    "stale_worker_count": status.get("stale_worker_count", 0),
                }
            )
            return
        if path.startswith("/api/hub/v1/workers/"):
            worker_id = path.removeprefix("/api/hub/v1/workers/").strip("/")
            if not worker_id or "/" in worker_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            include_endpoint = str(query.get("debug", [""])[0]).lower() in {"1", "true", "yes"}
            worker = self.server.registry.get_worker(worker_id)
            if worker is None:
                self._send_json({"error": f"Unknown hub worker: {worker_id}"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"ok": True, "worker": HubWorkerSummary.from_worker_payload(worker.as_dict(), include_endpoint=include_endpoint).as_dict()})
            return
        if path == "/api/hub/v1/models":
            status = self.server.registry.status()
            models = {
                str(model).strip()
                for worker in status.get("workers", [])
                if isinstance(worker, dict)
                for model in (worker.get("models") if isinstance(worker.get("models"), list) else [worker.get("model", "")])
                if str(model).strip()
            }
            if self.server.config.model:
                models.add(str(self.server.config.model))
            self._send_json({"ok": True, "models": sorted(models)})
            return
        if path == "/api/hub/v1/requests":
            states_param = str(query.get("state", [""])[0]).strip()
            states = {item.strip() for item in states_param.split(",") if item.strip()} if states_param else None
            limit = int(query.get("limit", ["100"])[0] or 100)
            requests = self.server.dispatcher.list_requests(limit=limit, states=states)
            self._send_json({"ok": True, "requests": requests, "request_count": len(requests)})
            return
        if path.startswith("/api/hub/v1/requests/") and path.endswith("/events"):
            request_id = path.removeprefix("/api/hub/v1/requests/").removesuffix("/events").strip("/")
            if not request_id or "/" in request_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                self._send_json({"ok": True, "request_id": request_id, "events": self.server.dispatcher.get_request_events(request_id)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/api/hub/v1/requests/"):
            request_id = path.removeprefix("/api/hub/v1/requests/").strip("/")
            if not request_id or "/" in request_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                self._send_json({"ok": True, "request": self.server.dispatcher.get_request_status(request_id)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        if path == "/api/hub/v1/credits":
            self._send_json(self.server.credit_ledger.status())
            return
        if path == "/api/hub/v1/credits/accounts":
            limit = int(query.get("limit", ["100"])[0] or 100)
            accounts = [account.as_dict() for account in self.server.credit_ledger.list_accounts(limit=limit)]
            self._send_json({"ok": True, "accounts": accounts, "account_count": len(accounts)})
            return
        if path == "/api/hub/v1/credits/balance":
            account_id = query.get("account_id", [self.server.config.hub_client_node_id])[0]
            account = self.server.credit_ledger.get_account(account_id)
            self._send_json({"ok": True, "account": account.as_dict(), "unit": self.server.credit_ledger.status()["unit"]})
            return
        if path == "/api/hub/v1/credits/transactions":
            account_id = query.get("account_id", [""])[0]
            limit = int(query.get("limit", ["100"])[0] or 100)
            transactions = [
                tx.as_dict()
                for tx in self.server.credit_ledger.list_transactions(account_id=account_id, limit=limit)
            ]
            self._send_json({"ok": True, "transactions": transactions, "transaction_count": len(transactions)})
            return
        if path == "/api/hub/v1/credits/purchases":
            account_id = query.get("account_id", [""])[0]
            limit = int(query.get("limit", ["100"])[0] or 100)
            purchases = [
                purchase.as_dict()
                for purchase in self.server.credit_ledger.list_purchases(account_id=account_id, limit=limit)
            ]
            self._send_json({"ok": True, "purchases": purchases, "purchase_count": len(purchases)})
            return
        if path in {"/api/hub/payouts", "/api/hub/v1/payouts"}:
            node_id = query.get("node_id", [""])[0]
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
            if path in {"/api/hub/workers/register", "/api/hub/v1/workers/register"}:
                body = self._read_json()
                worker = self.server.registry.register_worker(
                    node_id=str(body.get("node_id", "")),
                    endpoint=str(body.get("endpoint", "")),
                    model=str(body.get("model", "")),
                    models=[str(item) for item in body.get("models", [])] if isinstance(body.get("models"), list) else None,
                    capabilities=dict(body.get("capabilities", {})) if isinstance(body.get("capabilities"), dict) else None,
                    credits_per_request=int(body.get("credits_per_request", self.server.config.hub_credits_per_request)),
                    queue_depth=int(body.get("queue_depth", 0) or 0),
                    active_requests=int(body.get("active_requests", 0) or 0),
                    max_concurrency=int(body.get("max_concurrency", 1) or 1),
                )
                self.server.energy_ledger.register_node(worker.node_id, "gpu-worker", worker.endpoint)
                self._send_json({"ok": True, "worker": worker.as_dict(), "hub": self.server.registry.status()})
                return
            if path.startswith("/api/hub/v1/workers/") and path.endswith("/heartbeat"):
                body = self._read_json()
                worker_id = path.removeprefix("/api/hub/v1/workers/").removesuffix("/heartbeat").strip("/")
                if not worker_id or "/" in worker_id:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                worker = self.server.registry.heartbeat_worker(
                    worker_id,
                    status=str(body.get("status", "available")),
                    model=str(body.get("model", "")),
                    models=[str(item) for item in body.get("models", [])] if isinstance(body.get("models"), list) else None,
                    capabilities=dict(body.get("capabilities", {})) if isinstance(body.get("capabilities"), dict) else None,
                    queue_depth=int(body.get("queue_depth")) if body.get("queue_depth") is not None else None,
                    active_requests=int(body.get("active_requests")) if body.get("active_requests") is not None else None,
                    max_concurrency=int(body.get("max_concurrency")) if body.get("max_concurrency") is not None else None,
                )
                self._send_json({"ok": True, "worker": worker.as_dict(), "hub": self.server.registry.status()})
                return
            if path in {"/api/hub/upstreams/register", "/api/hub/v1/upstreams/register"}:
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
            if path in {"/api/hub/payouts/claim", "/api/hub/v1/payouts/claim"}:
                body = self._read_json()
                self._send_json(
                    self.server.energy_ledger.claim_payouts(
                        node_id=str(body.get("node_id", "")),
                        memo=str(body.get("memo", "")),
                    )
                )
                return
            if path == "/api/hub/v1/credits/admin/issue":
                body = self._read_json()
                result = self.server.credit_ledger.issue(
                    account_id=str(body.get("account_id", self.server.config.hub_client_node_id)),
                    credits=int(body.get("credits", 0) or 0),
                    memo=str(body.get("memo", "")),
                    owner_address=str(body.get("owner_address", "")),
                    metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                )
                self._send_json(result)
                return
            if path == "/api/hub/v1/requests":
                body = self._read_json()
                hub_request = HubAIRequest.from_payload(
                    body,
                    default_model=self.server.config.model,
                    default_client_node_id=self.server.config.hub_client_node_id,
                )
                status_payload = self.server.dispatcher.submit(hub_request)
                self._send_json({"ok": True, "request": status_payload})
                return
            if path.startswith("/api/hub/v1/requests/") and path.endswith("/cancel"):
                request_id = path.removeprefix("/api/hub/v1/requests/").removesuffix("/cancel").strip("/")
                if not request_id or "/" in request_id:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"ok": True, "request": self.server.dispatcher.cancel_request(request_id)})
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
    print(f"Hub admin/control site: http://{host}:{server.server_port}/admin")
    print(
        "Hub endpoints: GET /admin, GET /api/hub/v1/admin/bootstrap, GET /api/hub/status, "
        "GET /api/hub/payouts?node_id=..., POST /api/hub/workers/register, "
        "POST /api/hub/upstreams/register, POST /api/hub/sessions/start, "
        "POST /api/hub/sessions/chat, POST /api/hub/payouts/claim"
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

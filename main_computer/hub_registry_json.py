"""Legacy JSON-backed Hub Worker/Upstream registry implementation.

The FDB-backed Dev-Test registry subclasses this storage facade to preserve
normalization and inherited Worker semantics.  The implementation is isolated
from the legacy Hub HTTP server; no Hub startup, transport, wallet, or account
settlement is performed here.  Existing file storage semantics are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from main_computer.hub_security import HUB_SECURITY_PROFILE
from main_computer.hub_credit_models import normalize_worker_payout_precision_places
from main_computer.hub_registry_shared import (
    HUB_WORKER_INSTANCE_SLOT_LIMIT,
    HUB_WORKER_LEASE_SECONDS,
    HUB_WORKER_STALE_AFTER_SECONDS,
    HubUpstream,
    HubWorker,
    _clean_node_id,
    _hub_credit_public_value_from_wei,
    _hub_credit_wei_from_explicit_or_decimal,
    _hub_credit_wei_from_value,
    _hub_normalized_pricing_payload,
    _hub_pricing_credit_wei,
    _hub_worker_is_pull_mode,
    _phase9_worker_offer_from_payload,
    _require_allowed_transport,
)

def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


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
        credits_per_request: Any = 1,
        settlement_precision_places: int | None = None,
        queue_depth: int = 0,
        active_requests: int = 0,
        max_concurrency: int = 1,
        worker_instance_id: str = "",
    ) -> HubWorker:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else clean_node_id
        clean_endpoint = str(endpoint or "").strip().rstrip("/")
        clean_capabilities = dict(capabilities or {})
        # Outbound worker-pull uses an authenticated Hub socket, not an HTTP callback.
        if not clean_endpoint and not _hub_worker_is_pull_mode(clean_capabilities):
            raise ValueError("Worker endpoint is required for non-pull execution.")
        if clean_endpoint:
            _require_allowed_transport(
                clean_endpoint,
                role="Worker",
                allow_insecure_dev_network=self.allow_insecure_dev_network,
            )
        now = _utc_now()
        credit_price_wei, credit_price, _credit_price_display, pricing_payload = _hub_normalized_pricing_payload(
            clean_capabilities,
            {"credits_per_request": credits_per_request},
            "1",
        )
        clean_capabilities["pricing"] = pricing_payload
        clean_models = self._normalize_models(model=model, models=models)
        primary_model = clean_models[0] if clean_models else str(model or "").strip()
        clean_capabilities.setdefault("worker_instance_id", clean_worker_instance_id)
        precision_source = (
            settlement_precision_places
            if settlement_precision_places is not None
            else clean_capabilities.get("settlement_precision_places", clean_capabilities.get("payout_precision_places", None))
        )
        clean_settlement_precision = normalize_worker_payout_precision_places(precision_source)
        clean_capabilities.setdefault("settlement_precision_places", clean_settlement_precision)
        # One worker registration represents one live worker-instance slot.
        # A second simultaneous AI request must come from a second worker connection
        # with its own node/instance identity, not a multi-slot max_concurrency bump.
        clean_max_concurrency = HUB_WORKER_INSTANCE_SLOT_LIMIT
        clean_active = min(max(0, int(active_requests or 0)), clean_max_concurrency)
        clean_queue_depth = max(0, int(queue_depth or 0))
        with self._lock:
            data = self._load()
            workers = [
                item
                for item in data["workers"]
                if str(item.get("worker_instance_id") or item.get("node_id") or "") != clean_worker_instance_id
            ]
            existing = next(
                (
                    item
                    for item in data["workers"]
                    if str(item.get("worker_instance_id") or item.get("node_id") or "") == clean_worker_instance_id
                ),
                {},
            )
            registered_at = str(existing.get("registered_at") or now)
            worker = HubWorker(
                node_id=clean_node_id,
                endpoint=clean_endpoint,
                worker_instance_id=clean_worker_instance_id,
                model=primary_model,
                models=clean_models,
                status="available" if clean_active < clean_max_concurrency else "busy",
                credits_per_request=credit_price,
                settlement_precision_places=clean_settlement_precision,
                registered_at=registered_at,
                last_seen_at=now,
                capabilities=clean_capabilities,
                offer=_phase9_worker_offer_from_payload(
                    {
                        "node_id": clean_node_id,
                        "worker_instance_id": clean_worker_instance_id,
                        "model": primary_model,
                        "models": clean_models,
                        "credits_per_request": credit_price,
                        "credits_per_request_wei": str(credit_price_wei),
                        "capabilities": clean_capabilities,
                    }
                ),
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
        worker_instance_id: str = "",
    ) -> HubWorker:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else ""
        now = _utc_now()
        with self._lock:
            data = self._load()
            for item in data["workers"]:
                item_instance_id = str(item.get("worker_instance_id") or item.get("node_id") or "")
                if clean_worker_instance_id:
                    if item_instance_id != clean_worker_instance_id:
                        continue
                elif item.get("node_id") != clean_node_id:
                    continue
                next_max = HUB_WORKER_INSTANCE_SLOT_LIMIT
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

    def get_worker(self, node_id: str, *, worker_instance_id: str = "") -> HubWorker | None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else ""
        self.expire_stale_workers()
        data = self._load()
        if clean_worker_instance_id:
            for item in data["workers"]:
                if str(item.get("worker_instance_id") or item.get("node_id") or "") == clean_worker_instance_id:
                    return self._worker_from_payload(item)
            return None
        matches = [item for item in data["workers"] if item.get("node_id") == clean_node_id]
        if len(matches) == 1:
            return self._worker_from_payload(matches[0])
        for item in matches:
            if str(item.get("worker_instance_id") or item.get("node_id") or "") == clean_node_id:
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
                heartbeat_age = (now - last_seen).total_seconds() if last_seen is not None else None
                heartbeat_stale = heartbeat_age is not None and (
                    heartbeat_age > threshold or (threshold == 0 and heartbeat_age >= 0)
                )
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
        credits_per_request: Any = 1,
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
        credit_price_wei = _hub_credit_wei_from_value(credits_per_request, "1", minimum_wei=1)
        credit_price = _hub_credit_public_value_from_wei(credit_price_wei)
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
                    credits_per_request=_hub_credit_public_value_from_wei(
                        _hub_credit_wei_from_explicit_or_decimal(item.get("credits_per_request_wei"), item.get("credits_per_request", 1), "1", minimum_wei=1)
                    ),
                    registered_at=str(item.get("registered_at", "")),
                    last_seen_at=str(item.get("last_seen_at", "")),
                )
            )
        return sorted(available, key=lambda upstream: upstream.last_seen_at or upstream.registered_at)[0] if available else None

    def mark_worker(self, node_id: str, *, status: str, worker_instance_id: str = "") -> None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else ""
        clean_status = str(status or "available").strip().lower()
        if clean_status not in {"available", "configured", "busy", "offline", "stale", "draining"}:
            clean_status = "available"
        with self._lock:
            data = self._load()
            changed = False
            for worker in data["workers"]:
                item_instance_id = str(worker.get("worker_instance_id") or worker.get("node_id") or "")
                if clean_worker_instance_id:
                    if item_instance_id != clean_worker_instance_id:
                        continue
                elif worker.get("node_id") != clean_node_id:
                    continue
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
        preferred_worker_instance_id: str = "",
        lease_seconds: float | None = None,
    ) -> HubWorker | None:
        desired = str(model or "").strip()
        preferred = _clean_node_id(preferred_node_id, default="") if preferred_node_id else ""
        preferred_instance = _clean_node_id(preferred_worker_instance_id, default="") if preferred_worker_instance_id else ""
        lease_s = self.worker_lease_s if lease_seconds is None else max(1.0, float(lease_seconds))
        with self._lock:
            self._expire_stale_workers_unlocked(self._load(), stale_after_s=self.worker_stale_after_s)
            data = self._load()
            candidates = [
                item
                for item in data["workers"]
                if self._is_worker_lease_candidate(
                    item,
                    desired=desired,
                    preferred_node_id=preferred,
                    preferred_worker_instance_id=preferred_instance,
                    allow_model_fallback=False,
                )
            ]
            if not candidates and desired and not preferred and not preferred_instance:
                candidates = [
                    item
                    for item in data["workers"]
                    if self._is_worker_lease_candidate(
                        item,
                        desired="",
                        preferred_node_id="",
                        preferred_worker_instance_id="",
                        allow_model_fallback=True,
                    )
                ]
            if not candidates:
                return None
            worker = sorted(
                candidates,
                key=lambda item: (
                    max(0, int(item.get("queue_depth", 0) or 0)) + max(0, int(item.get("active_requests", 0) or 0)),
                    str(item.get("last_seen_at", "") or item.get("registered_at", "")),
                    str(item.get("node_id", "")),
                    str(item.get("worker_instance_id") or item.get("node_id") or ""),
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

    def release_worker(self, node_id: str, *, request_id: str = "", success: bool = True, worker_instance_id: str = "") -> None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else ""
        with self._lock:
            data = self._load()
            changed = False
            for worker in data["workers"]:
                item_instance_id = str(worker.get("worker_instance_id") or worker.get("node_id") or "")
                if clean_worker_instance_id:
                    if item_instance_id != clean_worker_instance_id:
                        continue
                elif worker.get("node_id") != clean_node_id:
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

    def record_ring_admission_rejection(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        payload.setdefault("event_type", "ring_admission_rejected")
        payload.setdefault("created_at", _utc_now())
        seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        payload.setdefault("event_id", "ring-admission-" + hashlib.sha256(seed).hexdigest()[:24])
        audit_path = self.root / "ring_admission_audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return payload

    def list_ring_admission_audit(self, *, limit: int = 500) -> list[dict[str, Any]]:
        audit_path = self.root / "ring_admission_audit.jsonl"
        if not audit_path.exists():
            return []
        clean_limit = max(1, int(limit or 500))
        events: list[dict[str, Any]] = []
        try:
            lines = audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines[-clean_limit:]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def ring_admission_audit_count(self) -> int:
        audit_path = self.root / "ring_admission_audit.jsonl"
        if not audit_path.exists():
            return 0
        try:
            return sum(1 for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            return 0

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
            worker_instance_id = _clean_node_id(str(item.get("worker_instance_id") or node_id), default=node_id)
            capabilities = dict(item.get("capabilities", {})) if isinstance(item.get("capabilities"), dict) else {}
            if not node_id or (not endpoint and not _hub_worker_is_pull_mode(capabilities)):
                continue
            models = self._normalize_models(model=str(item.get("model", "") or ""), models=item.get("models") if isinstance(item.get("models"), list) else None)
            primary_model = models[0] if models else str(item.get("model", "") or "")
            max_concurrency = HUB_WORKER_INSTANCE_SLOT_LIMIT
            active_requests = min(max_concurrency, max(0, int(item.get("active_requests", 0) or 0)))
            status = str(item.get("status", "available") or "available").lower()
            if status not in {"available", "configured", "busy", "offline", "stale", "draining"}:
                status = "available"
            capabilities = dict(item.get("capabilities", {})) if isinstance(item.get("capabilities"), dict) else {}
            price_wei, price, price_display, pricing_payload = _hub_normalized_pricing_payload(capabilities, item, "1")
            capabilities["pricing"] = pricing_payload
            normalized_worker = {
                "node_id": node_id,
                "worker_instance_id": worker_instance_id,
                "endpoint": endpoint,
                "model": primary_model,
                "models": models,
                "status": status,
                "credits_per_request": price,
                "credits_per_request_wei": str(price_wei),
                "credits_per_request_display": price_display,
                "settlement_precision_places": normalize_worker_payout_precision_places(
                    item.get("settlement_precision_places")
                    if item.get("settlement_precision_places") is not None
                    else (
                        item.get("capabilities", {}).get("settlement_precision_places")
                        if isinstance(item.get("capabilities"), dict)
                        else None
                    )
                ),
                "registered_at": str(item.get("registered_at") or created_at),
                "last_seen_at": str(item.get("last_seen_at") or item.get("registered_at") or created_at),
                "capabilities": capabilities,
                "queue_depth": max(0, int(item.get("queue_depth", 0) or 0)),
                "active_requests": active_requests,
                "max_concurrency": max_concurrency,
                "lease_expires_at": str(item.get("lease_expires_at", "") or ""),
                "stale": bool(item.get("stale", False)) or status == "stale",
            }
            offer = _phase9_worker_offer_from_payload(normalized_worker)
            if offer:
                normalized_worker["offer"] = offer
            normalized_workers.append(normalized_worker)
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
                    "credits_per_request": _hub_credit_public_value_from_wei(
                        _hub_credit_wei_from_explicit_or_decimal(item.get("credits_per_request_wei"), item.get("credits_per_request", 1), "1", minimum_wei=1)
                    ),
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
        preferred_worker_instance_id: str = "",
        allow_model_fallback: bool = False,
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
        worker_instance_id = str(item.get("worker_instance_id") or node_id)
        if preferred_worker_instance_id and worker_instance_id != preferred_worker_instance_id:
            return False
        if preferred_node_id and node_id != preferred_node_id and worker_instance_id != preferred_node_id:
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
            worker_instance_id=str(item.get("worker_instance_id") or item.get("node_id", "")),
            model=model,
            models=models,
            status=str(item.get("status", "available")),
            credits_per_request=_hub_credit_public_value_from_wei(_hub_pricing_credit_wei(dict(item.get("capabilities", {}).get("pricing", {})) if isinstance(item.get("capabilities", {}), dict) and isinstance(item.get("capabilities", {}).get("pricing", {}), dict) else {}, item, "1")),
            settlement_precision_places=normalize_worker_payout_precision_places(
                item.get("settlement_precision_places")
                if item.get("settlement_precision_places") is not None
                else (
                    item.get("capabilities", {}).get("settlement_precision_places")
                    if isinstance(item.get("capabilities"), dict)
                    else None
                )
            ),
            registered_at=str(item.get("registered_at", "")),
            last_seen_at=str(item.get("last_seen_at", "")),
            capabilities=dict(item.get("capabilities", {})) if isinstance(item.get("capabilities"), dict) else {},
            offer=dict(item.get("offer", {})) if isinstance(item.get("offer"), dict) else _phase9_worker_offer_from_payload(item),
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


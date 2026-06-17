from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from main_computer.energy import EnergyCreditLedger, EnergyNode, PendingEnergyPayout
from main_computer.exp_fdb_credit_ledger import (
    ExperimentalFoundationDbConfig,
    activate_cached_foundationdb_native_client,
)
from main_computer.hub import (
    HUB_SECURITY_PROFILE,
    HUB_WORKER_LEASE_SECONDS,
    HUB_WORKER_STALE_AFTER_SECONDS,
    HUB_WORKER_INSTANCE_SLOT_LIMIT,
    HubRegistry,
    HubUpstream,
    HubWorker,
    _clean_node_id,
    _phase9_worker_offer_from_payload,
    _require_allowed_transport,
)
from main_computer.hub_plex_models import HubRequestRecord, clean_node_id
from main_computer.hub_credit_models import (
    normalize_worker_payout_precision_places,
    truncate_worker_payout_for_precision,
)


EXPERIMENTAL_FDB_HUB_STATE_VERSION = "experimental-foundationdb-hub-state-v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fdb_value_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw
    try:
        return bytes(raw)
    except TypeError:
        return None


def _json_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_loads(raw: Any) -> dict[str, Any] | None:
    data = _fdb_value_bytes(raw)
    if data is None:
        return None
    payload = json.loads(data.decode("utf-8"))
    return payload if isinstance(payload, dict) else None


class ExperimentalFoundationDbHubState:
    """Shared FoundationDB access helper for the experimental hub state stores."""

    def __init__(self, config: ExperimentalFoundationDbConfig) -> None:
        self.config = config
        self.cluster_file = config.cluster_file
        self.namespace = str(config.namespace or "main-computer-exp-fdb").strip() or "main-computer-exp-fdb"
        if config.activate_native_client:
            self.native_client_library = activate_cached_foundationdb_native_client(config.repo_root)
        else:
            self.native_client_library = None

        try:
            import fdb  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "The experimental FDB hub requires the foundationdb Python package. "
                "Install it with: python -m pip install foundationdb"
            ) from exc

        try:
            fdb.api_version(int(config.api_version))
        except Exception as exc:
            message = str(exc).lower()
            if "api version" not in message or "already" not in message:
                raise RuntimeError(
                    f"Could not activate FoundationDB API version {config.api_version}. "
                    "Run scripts/smoke_foundationdb_credit_ledger_primitives.py once so it can "
                    "bootstrap the native FDB client, or install a matching FoundationDB client library."
                ) from exc

        import fdb.tuple  # type: ignore  # noqa: F401

        self.fdb = fdb
        try:
            self.db = fdb.open(cluster_file=str(config.cluster_file))
        except Exception as exc:
            raise RuntimeError(
                f"Could not open FoundationDB cluster file {config.cluster_file}. "
                "Start the local FDB container with the smoke script and verify the cluster file exists."
            ) from exc

    def pack(self, *parts: Any) -> bytes:
        return self.fdb.tuple.pack((self.namespace, "hub-state", *parts))

    def range_for(self, *parts: Any) -> slice:
        return self.fdb.Subspace((self.namespace, "hub-state", *parts)).range()

    def health_check(self) -> dict[str, Any]:
        marker_key = self.pack("meta", "health_check")

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            tr[marker_key] = _json_dumps({"ok": True, "checked_at": _utc_now()})
            payload = _json_loads(tr[marker_key].wait())
            return {"ok": bool(payload and payload.get("ok") is True)}

        result = _tx(self.db)
        result.update(
            {
                "backend": "foundationdb",
                "namespace": self.namespace,
                "store_version": EXPERIMENTAL_FDB_HUB_STATE_VERSION,
                "cluster_file": str(self.cluster_file),
                "native_client_library": str(self.native_client_library) if self.native_client_library else "",
            }
        )
        return result


class _StateComponent:
    def __init__(self, state: ExperimentalFoundationDbHubState) -> None:
        self.state = state
        self.fdb = state.fdb
        self.db = state.db

    def pack(self, *parts: Any) -> bytes:
        return self.state.pack(*parts)

    def range_for(self, *parts: Any) -> slice:
        return self.state.range_for(*parts)

    def _read_dict(self, tr: Any, *parts: Any) -> dict[str, Any] | None:
        return _json_loads(tr[self.pack(*parts)].wait())

    def _write_dict(self, tr: Any, payload: dict[str, Any], *parts: Any) -> None:
        tr[self.pack(*parts)] = _json_dumps(payload)

    def _list_dicts(self, tr: Any, *parts: Any, limit: int | None = None, reverse: bool = False) -> list[dict[str, Any]]:
        key_range = self.range_for(*parts)
        kwargs: dict[str, Any] = {}
        if limit is not None:
            kwargs["limit"] = max(1, int(limit))
        if reverse:
            kwargs["reverse"] = True
        result: list[dict[str, Any]] = []
        for item in tr.get_range(key_range.start, key_range.stop, **kwargs):
            payload = json.loads(bytes(item.value).decode("utf-8"))
            if isinstance(payload, dict):
                result.append(payload)
        return result

    def _clear_range(self, tr: Any, *parts: Any) -> None:
        key_range = self.range_for(*parts)
        tr.clear_range(key_range.start, key_range.stop)


class ExperimentalFoundationDbRegistry(HubRegistry):
    """FDB-backed worker/upstream registry for load-balanced experimental hubs."""

    def __init__(self, state: ExperimentalFoundationDbHubState, *, root: Path, allow_insecure_dev_network: bool = False) -> None:
        super().__init__(root, allow_insecure_dev_network=allow_insecure_dev_network)
        self.state = state
        self.fdb = state.fdb
        self.db = state.db
        self.path = root / "foundationdb-hub-workers.json"
        self._lock = threading.Lock()
        self.backend = "foundationdb"

    def _worker_key(self, node_id: str) -> bytes:
        return self.state.pack("worker", node_id)

    def _upstream_key(self, node_id: str) -> bytes:
        return self.state.pack("upstream_hub", node_id)

    def _read_worker_payload(self, tr: Any, node_id: str) -> dict[str, Any] | None:
        return _json_loads(tr[self._worker_key(node_id)].wait())

    def _write_worker_payload(self, tr: Any, payload: dict[str, Any]) -> None:
        normalized = self._normalize({"workers": [payload], "upstream_hubs": []})["workers"]
        if not normalized:
            raise ValueError("Worker payload did not normalize to a valid worker.")
        clean = normalized[0]
        worker_identity = str(clean.get("worker_instance_id") or clean["node_id"])
        self._clear_worker_indexes(tr, worker_identity)
        tr[self._worker_key(worker_identity)] = _json_dumps(clean)
        self._write_worker_indexes(tr, clean)

    def _read_upstream_payload(self, tr: Any, node_id: str) -> dict[str, Any] | None:
        return _json_loads(tr[self._upstream_key(node_id)].wait())

    def _write_upstream_payload(self, tr: Any, payload: dict[str, Any]) -> None:
        normalized = self._normalize({"workers": [], "upstream_hubs": [payload]})["upstream_hubs"]
        if not normalized:
            raise ValueError("Upstream payload did not normalize to a valid upstream hub.")
        clean = normalized[0]
        tr[self._upstream_key(str(clean["node_id"]))] = _json_dumps(clean)

    def _all_worker_payloads(self, tr: Any) -> list[dict[str, Any]]:
        key_range = self.state.range_for("worker")
        workers: list[dict[str, Any]] = []
        for item in tr.get_range(key_range.start, key_range.stop):
            payload = json.loads(bytes(item.value).decode("utf-8"))
            if isinstance(payload, dict):
                workers.append(payload)
        return sorted(workers, key=lambda item: (str(item.get("node_id", "")), str(item.get("worker_instance_id") or item.get("node_id", ""))))

    def _all_upstream_payloads(self, tr: Any) -> list[dict[str, Any]]:
        key_range = self.state.range_for("upstream_hub")
        upstreams: list[dict[str, Any]] = []
        for item in tr.get_range(key_range.start, key_range.stop):
            payload = json.loads(bytes(item.value).decode("utf-8"))
            if isinstance(payload, dict):
                upstreams.append(payload)
        return sorted(upstreams, key=lambda item: str(item.get("node_id", "")))

    def _worker_index_parts(self, worker: dict[str, Any]) -> list[tuple[Any, ...]]:
        status = str(worker.get("status", "available")).lower()
        active = max(0, int(worker.get("active_requests", 0) or 0))
        max_concurrency = max(1, int(worker.get("max_concurrency", 1) or 1))
        if status not in {"available", "configured"} or bool(worker.get("stale", False)) or active >= max_concurrency:
            return []
        capabilities = dict(worker.get("capabilities", {})) if isinstance(worker.get("capabilities"), dict) else {}
        pricing = dict(capabilities.get("pricing", {})) if isinstance(capabilities.get("pricing"), dict) else {}
        network = str(capabilities.get("network") or capabilities.get("assigned_network") or capabilities.get("worker_network") or "").strip().lower()
        ring = str(worker.get("assigned_ring") or capabilities.get("assigned_ring") or capabilities.get("requested_ring") or "").strip()
        price = max(1, int(worker.get("credits_per_request") or pricing.get("credits_per_request") or 1))
        price_bucket = min(1_000_000_000, price)
        models = [str(model).strip() for model in worker.get("models", []) if str(model).strip()] if isinstance(worker.get("models"), list) else []
        model = str(worker.get("model", "") or "").strip()
        if model and model not in models:
            models.insert(0, model)
        node_id = str(worker.get("node_id", ""))
        worker_identity = str(worker.get("worker_instance_id") or node_id)
        parts: list[tuple[Any, ...]] = []
        for model_name in models or [""]:
            parts.append((network, ring, model_name, price_bucket, active, node_id, worker_identity))
        return parts

    def _clear_worker_indexes(self, tr: Any, node_id: str) -> None:
        reverse = self.state.range_for("idx_worker_available_rev", node_id)
        keys_to_clear: list[bytes] = []
        for item in tr.get_range(reverse.start, reverse.stop):
            payload = _json_loads(item.value)
            if isinstance(payload, dict):
                key_hex = str(payload.get("key_hex") or "")
                try:
                    keys_to_clear.append(bytes.fromhex(key_hex))
                except ValueError:
                    pass
            keys_to_clear.append(bytes(item.key))
        for key in keys_to_clear:
            del tr[key]

    def _write_worker_indexes(self, tr: Any, worker: dict[str, Any]) -> None:
        node_id = str(worker.get("node_id", ""))
        worker_identity = str(worker.get("worker_instance_id") or node_id)
        for index_no, parts in enumerate(self._worker_index_parts(worker)):
            idx_key = self.state.pack("idx_worker_available", *parts)
            tr[idx_key] = b""
            tr[self.state.pack("idx_worker_available_rev", worker_identity, index_no)] = _json_dumps({"key_hex": idx_key.hex()})

    def _load(self) -> dict[str, Any]:
        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            return self._normalize({"workers": self._all_worker_payloads(tr), "upstream_hubs": self._all_upstream_payloads(tr)})

        return _tx(self.db)

    def _save(self, data: dict[str, Any]) -> None:
        normalized = self._normalize(data)

        @self.fdb.transactional
        def _tx(tr: Any) -> None:
            self._clear_range(tr, "worker")
            self._clear_range(tr, "upstream_hub")
            self._clear_range(tr, "idx_worker_available")
            self._clear_range(tr, "idx_worker_available_rev")
            for worker in normalized["workers"]:
                self._write_worker_payload(tr, worker)
            for upstream in normalized["upstream_hubs"]:
                self._write_upstream_payload(tr, upstream)

        _tx(self.db)

    def status(self) -> dict[str, Any]:
        self.expire_stale_workers()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            data = self._normalize({"workers": self._all_worker_payloads(tr), "upstream_hubs": self._all_upstream_payloads(tr)})
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
                "backend": "foundationdb",
                "store_version": EXPERIMENTAL_FDB_HUB_STATE_VERSION,
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
                "scheduler_indexes": {
                    "available_workers_by_network_ring_model_price": True,
                },
            }

        return _tx(self.db)

    def register_worker(
        self,
        *,
        node_id: str,
        endpoint: str,
        model: str = "",
        models: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        credits_per_request: int = 1,
        settlement_precision_places: int | None = None,
        queue_depth: int = 0,
        active_requests: int = 0,
        max_concurrency: int = 1,
        worker_instance_id: str = "",
    ) -> HubWorker:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else clean_node_id
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
        clean_capabilities.setdefault("worker_instance_id", clean_worker_instance_id)
        precision_source = (
            settlement_precision_places
            if settlement_precision_places is not None
            else clean_capabilities.get("settlement_precision_places", clean_capabilities.get("payout_precision_places", None))
        )
        clean_settlement_precision = normalize_worker_payout_precision_places(precision_source)
        clean_capabilities.setdefault("settlement_precision_places", clean_settlement_precision)
        clean_max_concurrency = HUB_WORKER_INSTANCE_SLOT_LIMIT
        clean_active = min(max(0, int(active_requests or 0)), clean_max_concurrency)
        clean_queue_depth = max(0, int(queue_depth or 0))

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            existing = self._read_worker_payload(tr, clean_worker_instance_id) or {}
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
                        "capabilities": clean_capabilities,
                    }
                ),
                queue_depth=clean_queue_depth,
                active_requests=clean_active,
                max_concurrency=clean_max_concurrency,
                lease_expires_at=str(existing.get("lease_expires_at", "") or ""),
                stale=False,
            )
            self._write_worker_payload(tr, worker.as_dict())
            return worker.as_dict()

        return self._worker_from_payload(_tx(self.db))

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
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else clean_node_id
        now = _utc_now()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            item = self._read_worker_payload(tr, clean_worker_instance_id)
            if not isinstance(item, dict) and clean_worker_instance_id != clean_node_id:
                item = self._read_worker_payload(tr, clean_node_id)
            if not isinstance(item, dict):
                raise KeyError(f"Unknown hub worker: {clean_worker_instance_id or clean_node_id}")
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
            self._write_worker_payload(tr, item)
            return item

        return self._worker_from_payload(_tx(self.db))

    def get_worker(self, node_id: str, *, worker_instance_id: str = "") -> HubWorker | None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else ""
        self.expire_stale_workers()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            if clean_worker_instance_id:
                return self._read_worker_payload(tr, clean_worker_instance_id)
            direct = self._read_worker_payload(tr, clean_node_id)
            if isinstance(direct, dict):
                return direct
            matches = [item for item in self._all_worker_payloads(tr) if item.get("node_id") == clean_node_id]
            if len(matches) == 1:
                return matches[0]
            for item in matches:
                if str(item.get("worker_instance_id") or item.get("node_id") or "") == clean_node_id:
                    return item
            return None

        payload = _tx(self.db)
        return self._worker_from_payload(payload) if isinstance(payload, dict) else None

    def expire_stale_workers(self, *, stale_after_s: float | None = None) -> int:
        threshold = self.worker_stale_after_s if stale_after_s is None else max(0.0, float(stale_after_s))
        now = datetime.now(tz=timezone.utc)

        @self.fdb.transactional
        def _tx(tr: Any) -> int:
            changed = 0
            for worker in self._all_worker_payloads(tr):
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
                    self._write_worker_payload(tr, worker)
                    changed += 1
            return changed

        return _tx(self.db)

    def register_upstream_hub(self, *, node_id: str, endpoint: str, credits_per_request: int = 1) -> HubUpstream:
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

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            existing = self._read_upstream_payload(tr, clean_node_id) or {}
            upstream = HubUpstream(
                node_id=clean_node_id,
                endpoint=clean_endpoint,
                status="available",
                credits_per_request=credit_price,
                registered_at=str(existing.get("registered_at") or now),
                last_seen_at=now,
            )
            self._write_upstream_payload(tr, upstream.as_dict())
            return upstream.as_dict()

        return HubUpstream(**_tx(self.db))

    def mark_upstream_hub(self, node_id: str, *, status: str) -> None:
        clean_node_id = _clean_node_id(node_id, default="upstream-hub")

        @self.fdb.transactional
        def _tx(tr: Any) -> None:
            upstream = self._read_upstream_payload(tr, clean_node_id)
            if not isinstance(upstream, dict):
                return
            upstream["status"] = status
            upstream["last_seen_at"] = _utc_now()
            self._write_upstream_payload(tr, upstream)

        _tx(self.db)

    def select_upstream_hub(self) -> HubUpstream | None:
        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            available: list[dict[str, Any]] = []
            for item in self._all_upstream_payloads(tr):
                if str(item.get("status", "available")) not in {"available", "configured"}:
                    continue
                available.append(item)
            return sorted(available, key=lambda item: str(item.get("last_seen_at", "") or item.get("registered_at", "")))[0] if available else None

        payload = _tx(self.db)
        return HubUpstream(**payload) if isinstance(payload, dict) else None

    def mark_worker(self, node_id: str, *, status: str, worker_instance_id: str = "") -> None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else clean_node_id
        clean_status = str(status or "available").strip().lower()
        if clean_status not in {"available", "configured", "busy", "offline", "stale", "draining"}:
            clean_status = "available"

        @self.fdb.transactional
        def _tx(tr: Any) -> None:
            worker = self._read_worker_payload(tr, clean_worker_instance_id)
            if not isinstance(worker, dict) and clean_worker_instance_id != clean_node_id:
                worker = self._read_worker_payload(tr, clean_node_id)
            if not isinstance(worker, dict):
                return
            worker["status"] = clean_status
            worker["last_seen_at"] = _utc_now()
            worker["stale"] = clean_status == "stale"
            if clean_status in {"offline", "stale", "draining"}:
                worker["active_requests"] = 0
                worker["lease_expires_at"] = ""
            self._write_worker_payload(tr, worker)

        _tx(self.db)

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

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            workers = self._all_worker_payloads(tr)
            self._expire_payloads_unlocked(tr, workers, stale_after_s=self.worker_stale_after_s)
            workers = self._all_worker_payloads(tr)
            candidates = [
                item
                for item in workers
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
                    for item in workers
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
            max_concurrency = HUB_WORKER_INSTANCE_SLOT_LIMIT
            active = min(max_concurrency, max(0, int(worker.get("active_requests", 0) or 0)) + 1)
            worker["active_requests"] = active
            worker["status"] = "busy" if active >= max_concurrency else "available"
            worker["lease_expires_at"] = (datetime.now(tz=timezone.utc) + timedelta(seconds=lease_s)).isoformat()
            worker["last_seen_at"] = _utc_now()
            worker["stale"] = False
            if request_id:
                worker["last_request_id"] = str(request_id)
            self._write_worker_payload(tr, worker)
            return worker

        payload = _tx(self.db)
        return self._worker_from_payload(payload) if isinstance(payload, dict) else None

    def release_worker(self, node_id: str, *, request_id: str = "", success: bool = True, worker_instance_id: str = "") -> None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else clean_node_id

        @self.fdb.transactional
        def _tx(tr: Any) -> None:
            worker = self._read_worker_payload(tr, clean_worker_instance_id)
            if not isinstance(worker, dict) and clean_worker_instance_id != clean_node_id:
                worker = self._read_worker_payload(tr, clean_node_id)
            if not isinstance(worker, dict):
                return
            max_concurrency = HUB_WORKER_INSTANCE_SLOT_LIMIT
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
            self._write_worker_payload(tr, worker)

        _tx(self.db)

    def select_worker(self, model: str = "") -> HubWorker | None:
        desired = str(model or "").strip()
        self.expire_stale_workers()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            available: list[dict[str, Any]] = []
            for item in self._all_worker_payloads(tr):
                if not self._is_worker_lease_candidate(item, desired=desired, preferred_node_id="", allow_model_fallback=False):
                    continue
                available.append(item)
            if not available and desired:
                for item in self._all_worker_payloads(tr):
                    if self._is_worker_lease_candidate(item, desired="", preferred_node_id="", allow_model_fallback=True):
                        available.append(item)
            return sorted(
                available,
                key=lambda item: (
                    max(0, int(item.get("queue_depth", 0) or 0)) + max(0, int(item.get("active_requests", 0) or 0)),
                    str(item.get("last_seen_at", "") or item.get("registered_at", "")),
                    str(item.get("worker_instance_id") or item.get("node_id") or ""),
                ),
            )[0] if available else None

        payload = _tx(self.db)
        return self._worker_from_payload(payload) if isinstance(payload, dict) else None

    def _expire_payloads_unlocked(self, tr: Any, workers: list[dict[str, Any]], *, stale_after_s: float) -> int:
        threshold = max(0.0, float(stale_after_s))
        now = datetime.now(tz=timezone.utc)
        changed = 0
        for worker in workers:
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
                self._write_worker_payload(tr, worker)
                changed += 1
        return changed


class ExperimentalFoundationDbRequestStateStore(_StateComponent):
    """FDB-backed lifecycle records with atomic worker-pull lease claiming."""

    def _request_key(self, request_id: str) -> bytes:
        return self.pack("request", request_id)

    def _read_record_payload(self, tr: Any, request_id: str) -> dict[str, Any] | None:
        return _json_loads(tr[self._request_key(request_id)].wait())

    def _index_keys(self, payload: dict[str, Any]) -> list[bytes]:
        request_id = str(payload.get("request_id", "") or "")
        if not request_id:
            return []
        state = str(payload.get("state", "queued") or "queued")
        created_at = str(payload.get("created_at") or payload.get("updated_at") or "")
        updated_at = str(payload.get("updated_at") or payload.get("created_at") or "")
        keys = [
            self.pack("idx_request_state", state, created_at, request_id),
            self.pack("idx_request_updated", updated_at, request_id),
        ]
        idem = str(payload.get("idempotency_key", "") or "")
        client = str(payload.get("client_node_id", "") or "")
        if idem:
            keys.append(self.pack("idx_request_idempotency", client, idem, request_id))
        return keys

    def _clear_indexes(self, tr: Any, payload: dict[str, Any]) -> None:
        for key in self._index_keys(payload):
            del tr[key]

    def _write_record_payload(self, tr: Any, payload: dict[str, Any]) -> None:
        record = HubRequestRecord.from_dict(payload)
        clean = record.as_dict()
        request_id = clean["request_id"]
        existing = self._read_record_payload(tr, request_id)
        if isinstance(existing, dict):
            self._clear_indexes(tr, existing)
        tr[self._request_key(request_id)] = _json_dumps(clean)
        for key in self._index_keys(clean):
            tr[key] = _json_dumps({"request_id": request_id})

    def create(self, record: HubRequestRecord) -> HubRequestRecord:
        payload = record.as_dict()

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            self._write_record_payload(tr, payload)
            return payload

        return HubRequestRecord.from_dict(_tx(self.db))

    def get(self, request_id: str) -> HubRequestRecord | None:
        clean = str(request_id or "").strip()
        if not clean:
            return None

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            return self._read_record_payload(tr, clean)

        payload = _tx(self.db)
        return HubRequestRecord.from_dict(payload) if isinstance(payload, dict) else None

    def find_by_idempotency_key(self, *, client_node_id: str, idempotency_key: str) -> HubRequestRecord | None:
        clean_client = clean_node_id(client_node_id, default="main-computer-client")
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            return None

        @self.fdb.transactional
        def _tx(tr: Any) -> list[dict[str, Any]]:
            key_range = self.range_for("idx_request_idempotency", clean_client, clean_key)
            matches: list[dict[str, Any]] = []
            for item in tr.get_range(key_range.start, key_range.stop):
                pointer = _json_loads(item.value)
                request_id = str(pointer.get("request_id", "") if isinstance(pointer, dict) else "")
                payload = self._read_record_payload(tr, request_id)
                if isinstance(payload, dict):
                    matches.append(payload)
            return matches

        matches = [HubRequestRecord.from_dict(payload) for payload in _tx(self.db)]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.created_at or item.updated_at)[-1]

    def list(self, *, limit: int = 100, states: set[str] | None = None) -> list[HubRequestRecord]:
        clean_limit = min(500, max(1, int(limit or 100)))

        @self.fdb.transactional
        def _tx(tr: Any) -> list[dict[str, Any]]:
            payloads: list[dict[str, Any]] = []
            if states:
                for state in sorted(str(item) for item in states):
                    key_range = self.range_for("idx_request_state", state)
                    for item in tr.get_range(key_range.start, key_range.stop, limit=clean_limit):
                        pointer = _json_loads(item.value)
                        request_id = str(pointer.get("request_id", "") if isinstance(pointer, dict) else "")
                        payload = self._read_record_payload(tr, request_id)
                        if isinstance(payload, dict):
                            payloads.append(payload)
            else:
                key_range = self.range_for("request")
                for item in tr.get_range(key_range.start, key_range.stop, limit=clean_limit):
                    payload = json.loads(bytes(item.value).decode("utf-8"))
                    if isinstance(payload, dict):
                        payloads.append(payload)
            return payloads

        records = [HubRequestRecord.from_dict(payload) for payload in _tx(self.db)]
        if states:
            records = [record for record in records if record.state in states]
        return sorted(records, key=lambda item: item.updated_at or item.created_at, reverse=True)[:clean_limit]

    def events(self, request_id: str) -> list[dict[str, Any]]:
        record = self.get(request_id)
        if record is None:
            raise KeyError(f"Unknown hub request: {request_id}")
        return [dict(event) for event in record.events]

    def update(self, request_id: str, **changes: Any) -> HubRequestRecord:
        clean = str(request_id or "").strip()
        if not clean:
            raise ValueError("request_id is required.")
        event_type = str(changes.pop("event_type", "") or "")
        event = changes.pop("event", None)

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            payload = self._read_record_payload(tr, clean)
            if not isinstance(payload, dict):
                raise KeyError(f"Unknown hub request: {clean}")
            record = HubRequestRecord.from_dict(payload)
            for key, value in changes.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            record.updated_at = _utc_now()
            if event_type:
                event_payload = {
                    "type": event_type,
                    "state": record.state,
                    "created_at": record.updated_at,
                }
                if isinstance(event, dict):
                    event_payload.update(dict(event))
                record.events.append(event_payload)
            self._write_record_payload(tr, record.as_dict())
            return record.as_dict()

        return HubRequestRecord.from_dict(_tx(self.db))

    def claim_worker_pull_lease(
        self,
        request_id: str,
        *,
        worker_node_id: str,
        lease_id: str,
        expires_at: str,
        credits_queued: int,
        attempt_history: list[dict[str, Any]],
        worker_instance_id: str = "",
    ) -> HubRequestRecord | None:
        """Atomically move one queued request to leased, or return None if another hub won."""

        clean = str(request_id or "").strip()
        clean_worker_id = clean_node_id(worker_node_id, default="hub-worker")
        clean_worker_instance_id = (
            clean_node_id(worker_instance_id, default="") if worker_instance_id else clean_worker_id
        )
        if not clean or not clean_worker_id:
            return None

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            payload = self._read_record_payload(tr, clean)
            if not isinstance(payload, dict):
                return None
            record = HubRequestRecord.from_dict(payload)
            if record.state != "queued":
                return None
            if not record.hold_id or record.charge_id:
                return None
            if record.selected_worker_instance_id and record.selected_worker_instance_id != clean_worker_instance_id:
                return None
            if record.requested_worker_node_id and record.requested_worker_node_id not in {
                clean_worker_id,
                clean_worker_instance_id,
            }:
                return None
            record.state = "leased"
            record.selected_worker_node_id = clean_worker_id
            record.selected_worker_instance_id = clean_worker_instance_id
            record.lease_id = str(lease_id or "")
            record.lease_expires_at = str(expires_at or "")
            record.credits_queued = max(0, int(credits_queued or 0))
            record.attempt_history = [dict(item) for item in attempt_history if isinstance(item, dict)]
            record.updated_at = _utc_now()
            record.events.append(
                {
                    "type": "worker_pull.lease.granted",
                    "state": record.state,
                    "created_at": record.updated_at,
                    "worker_node_id": clean_worker_id,
                    "worker_instance_id": clean_worker_instance_id,
                    "lease_id": record.lease_id,
                    "expires_at": record.lease_expires_at,
                }
            )
            self._write_record_payload(tr, record.as_dict())
            return record.as_dict()

        payload = _tx(self.db)
        return HubRequestRecord.from_dict(payload) if isinstance(payload, dict) else None

    def cancel(self, request_id: str) -> HubRequestRecord:
        record = self.get(request_id)
        if record is None:
            raise KeyError(f"Unknown hub request: {request_id}")
        if record.state in {"completed", "failed", "cancelled", "expired"}:
            return record
        return self.update(record.request_id, state="cancelled", terminal_reason="client_cancelled", event_type="request.cancelled")

    def expire_deadlines(self) -> int:
        now = datetime.now(tz=timezone.utc)

        @self.fdb.transactional
        def _tx(tr: Any) -> int:
            changed = 0
            key_range = self.range_for("request")
            for item in tr.get_range(key_range.start, key_range.stop):
                payload = json.loads(bytes(item.value).decode("utf-8"))
                if not isinstance(payload, dict):
                    continue
                record = HubRequestRecord.from_dict(payload)
                if record.state in {"completed", "failed", "cancelled", "expired"}:
                    continue
                deadline = self._parse_utc(record.deadline_at)
                if deadline is None or deadline >= now:
                    continue
                record.state = "expired"
                record.error = "Request deadline expired before completion."
                record.terminal_reason = "deadline_expired"
                record.updated_at = _utc_now()
                record.events.append(
                    {
                        "type": "request.expired",
                        "state": record.state,
                        "created_at": record.updated_at,
                        "deadline_at": record.deadline_at,
                    }
                )
                self._write_record_payload(tr, record.as_dict())
                changed += 1
            return changed

        return _tx(self.db)

    def metrics(self) -> dict[str, Any]:
        self.expire_deadlines()
        records = self.list(limit=500)
        by_state: dict[str, int] = {}
        for record in records:
            by_state[record.state] = by_state.get(record.state, 0) + 1
        active_states = {"submitted", "held", "queued", "leasing_worker", "dispatching", "running", "retrying", "leased"}
        terminal_states = {"completed", "failed", "cancelled", "expired"}
        return {
            "requests": {
                "total_recent": len(records),
                "active": sum(by_state.get(state, 0) for state in active_states),
                "terminal": sum(by_state.get(state, 0) for state in terminal_states),
                "by_state": by_state,
            },
            "request_store": {
                "backend": "foundationdb",
                "store_version": EXPERIMENTAL_FDB_HUB_STATE_VERSION,
            },
        }

    @staticmethod
    def _parse_utc(value: str) -> datetime | None:
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


class ExperimentalFoundationDbQuoteStateStore(_StateComponent):
    """FDB-backed Phase 9 quote snapshots."""

    def _quote_key(self, quote_id: str) -> bytes:
        return self.pack("quote", quote_id)

    def _read_quote(self, tr: Any, quote_id: str) -> dict[str, Any] | None:
        return _json_loads(tr[self._quote_key(quote_id)].wait())

    def _index_keys(self, quote: dict[str, Any]) -> list[bytes]:
        quote_id = str(quote.get("quote_id", "") or "")
        keys: list[bytes] = []
        account_id = str(quote.get("account_id", "") or "")
        idem = str(quote.get("idempotency_key", "") or "")
        if quote_id and account_id and idem:
            keys.append(self.pack("idx_quote_idempotency", account_id, idem, quote_id))
        return keys

    def create_or_get(self, quote: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        quote_id = str(quote.get("quote_id", "") or "").strip()
        if not quote_id:
            raise ValueError("quote_id is required.")

        @self.fdb.transactional
        def _tx(tr: Any) -> tuple[dict[str, Any], bool]:
            existing = self._read_quote(tr, quote_id)
            if isinstance(existing, dict):
                return existing, True
            clean = dict(quote)
            tr[self._quote_key(quote_id)] = _json_dumps(clean)
            for key in self._index_keys(clean):
                tr[key] = _json_dumps({"quote_id": quote_id})
            return clean, False

        return _tx(self.db)

    def get(self, quote_id: str) -> dict[str, Any] | None:
        clean = str(quote_id or "").strip()
        if not clean:
            return None

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            return self._read_quote(tr, clean)

        payload = _tx(self.db)
        return dict(payload) if isinstance(payload, dict) else None

    def find_by_idempotency_key(self, *, account_id: str, idempotency_key: str) -> dict[str, Any] | None:
        clean_account = clean_node_id(account_id, default="") if account_id else ""
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            return None

        @self.fdb.transactional
        def _tx(tr: Any) -> list[dict[str, Any]]:
            key_range = self.range_for("idx_quote_idempotency", clean_account, clean_key)
            quotes: list[dict[str, Any]] = []
            for item in tr.get_range(key_range.start, key_range.stop):
                pointer = _json_loads(item.value)
                quote_id = str(pointer.get("quote_id", "") if isinstance(pointer, dict) else "")
                payload = self._read_quote(tr, quote_id)
                if isinstance(payload, dict):
                    quotes.append(payload)
            return quotes

        quotes = [dict(item) for item in _tx(self.db)]
        if not quotes:
            return None
        return sorted(quotes, key=lambda item: str(item.get("created_at", "")))[-1]



class ExperimentalFoundationDbFeedbackStateStore(_StateComponent):
    """FDB-backed requester feedback records shared across Hub instances."""

    def _feedback_key(self, feedback_key: str) -> bytes:
        return self.pack("feedback", feedback_key)

    def _read_feedback(self, tr: Any, feedback_key: str) -> dict[str, Any] | None:
        return _json_loads(tr[self._feedback_key(feedback_key)].wait())

    def _index_keys(self, payload: dict[str, Any]) -> list[bytes]:
        feedback_key = str(payload.get("feedback_key", "") or "")
        if not feedback_key:
            return []
        updated_at = str(payload.get("updated_at") or payload.get("created_at") or "")
        keys = [self.pack("idx_feedback_updated", updated_at, feedback_key)]
        for name, value in (
            ("idx_feedback_request", payload.get("request_id")),
            ("idx_feedback_account", payload.get("account_id")),
            ("idx_feedback_worker_commitment", payload.get("worker_commitment")),
            ("idx_feedback_worker_node_id", payload.get("worker_node_id")),
            ("idx_feedback_agent_run", payload.get("agent_run_id")),
        ):
            clean_value = str(value or "").strip()
            if clean_value:
                keys.append(self.pack(name, clean_value, feedback_key))
        return keys

    def _clear_indexes(self, tr: Any, payload: dict[str, Any]) -> None:
        for key in self._index_keys(payload):
            del tr[key]

    def _normalize_report(self, report: dict[str, Any]) -> dict[str, Any]:
        request_id = str(report.get("request_id", "") or "").strip()
        account_id = clean_node_id(str(report.get("account_id") or report.get("requester_account_id") or ""), default="")
        if not request_id or not account_id:
            raise ValueError("request_id and account_id are required for feedback.")
        now = _utc_now()
        clean = dict(report)
        clean["feedback_key"] = str(report.get("feedback_key") or f"{request_id}:{account_id}")
        clean["feedback_id"] = str(report.get("feedback_id") or hashlib.sha256(clean["feedback_key"].encode("utf-8")).hexdigest()[:24])
        clean["request_id"] = request_id
        clean["account_id"] = account_id
        clean["requester_account_id"] = account_id
        clean["feedback_channel"] = clean_node_id(str(report.get("feedback_channel", "") or ""), default="") if report.get("feedback_channel") else ""
        clean["score"] = max(1, min(5, int(report.get("score", report.get("rating", 1)) or 1)))
        clean["rating"] = clean["score"]
        verdict = str(report.get("verdict", "") or "").strip().lower()
        if verdict not in {"accepted", "rejected", "needs_revision"}:
            verdict = "accepted" if clean["score"] >= 4 else "rejected" if clean["score"] <= 2 else "needs_revision"
        clean["verdict"] = verdict
        tags: list[str] = []
        for raw in report.get("feedback_tags", report.get("tags", [])) or []:
            tag = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(raw or "").strip().lower())
            if tag and tag not in tags:
                tags.append(tag)
        clean["feedback_tags"] = tags
        clean["note"] = str(report.get("note", report.get("reason", "")) or "")[:1000]
        clean["reason"] = str(report.get("reason") or clean["note"] or verdict)
        clean["source"] = str(report.get("source") or "requester").strip().lower() or "requester"
        clean["version"] = max(1, int(report.get("version", 1) or 1))
        clean["created_at"] = str(report.get("created_at") or now)
        clean["updated_at"] = str(report.get("updated_at") or now)
        clean.setdefault("history", [])
        clean["worker_identity_private"] = True
        clean["money_movement"] = False
        return clean

    @staticmethod
    def _idempotency_body(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: payload.get(key)
            for key in (
                "request_id",
                "account_id",
                "worker_commitment",
                "report_token_hash",
                "score",
                "verdict",
                "feedback_tags",
                "note",
                "source",
                "feedback_channel",
                "agent_run_id",
                "agent_step_id",
                "parent_request_id",
            )
        }

    def submit(self, report: dict[str, Any]) -> dict[str, Any]:
        clean = self._normalize_report(report)
        feedback_key = str(clean["feedback_key"])

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            existing = self._read_feedback(tr, feedback_key)
            if isinstance(existing, dict):
                if self._idempotency_body(existing) == self._idempotency_body(clean):
                    result = dict(existing)
                    result["idempotent"] = True
                    return result
                self._clear_indexes(tr, existing)
                history = [dict(item) for item in existing.get("history", []) if isinstance(item, dict)]
                archived = dict(existing)
                archived.pop("history", None)
                archived["archived_at"] = _utc_now()
                history.append(archived)
                clean["version"] = max(1, int(existing.get("version", 1) or 1)) + 1
                clean["created_at"] = str(existing.get("created_at") or clean["created_at"])
                clean["updated_at"] = _utc_now()
                clean["history"] = history[-25:]
            tr[self._feedback_key(feedback_key)] = _json_dumps(clean)
            for key in self._index_keys(clean):
                tr[key] = _json_dumps({"feedback_key": feedback_key})
            result = dict(clean)
            result["idempotent"] = False
            return result

        return _tx(self.db)

    def get_for_request(self, request_id: str, *, account_id: str = "") -> list[dict[str, Any]]:
        clean_request = str(request_id or "").strip()
        clean_account = clean_node_id(account_id, default="") if account_id else ""
        if not clean_request:
            return []

        @self.fdb.transactional
        def _tx(tr: Any) -> list[dict[str, Any]]:
            key_range = self.range_for("idx_feedback_request", clean_request)
            records: list[dict[str, Any]] = []
            for item in tr.get_range(key_range.start, key_range.stop):
                pointer = _json_loads(item.value)
                feedback_key = str(pointer.get("feedback_key", "") if isinstance(pointer, dict) else "")
                payload = self._read_feedback(tr, feedback_key)
                if not isinstance(payload, dict):
                    continue
                if clean_account and str(payload.get("account_id", "") or "") != clean_account:
                    continue
                records.append(payload)
            return records

        return sorted([dict(item) for item in _tx(self.db)], key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))

    def list(self, *, limit: int = 500) -> list[dict[str, Any]]:
        clean_limit = min(2000, max(1, int(limit or 500)))

        @self.fdb.transactional
        def _tx(tr: Any) -> list[dict[str, Any]]:
            records: list[dict[str, Any]] = []
            key_range = self.range_for("idx_feedback_updated")
            for item in tr.get_range(key_range.start, key_range.stop, limit=clean_limit, reverse=True):
                pointer = _json_loads(item.value)
                feedback_key = str(pointer.get("feedback_key", "") if isinstance(pointer, dict) else "")
                payload = self._read_feedback(tr, feedback_key)
                if isinstance(payload, dict):
                    records.append(payload)
            if not records:
                fallback_range = self.range_for("feedback")
                for item in tr.get_range(fallback_range.start, fallback_range.stop, limit=clean_limit):
                    payload = json.loads(bytes(item.value).decode("utf-8"))
                    if isinstance(payload, dict):
                        records.append(payload)
            return records

        return [dict(item) for item in _tx(self.db)][:clean_limit]



class ExperimentalFoundationDbSecureSessionStore(_StateComponent):
    def set(self, session_id: str, payload: dict[str, Any]) -> None:
        clean = str(session_id or "").strip()
        if not clean:
            raise ValueError("session_id is required.")

        @self.fdb.transactional
        def _tx(tr: Any) -> None:
            data = dict(payload)
            data["session_id"] = clean
            tr[self.pack("secure_session", clean)] = _json_dumps(data)

        _tx(self.db)

    def get(self, session_id: str) -> dict[str, Any] | None:
        clean = str(session_id or "").strip()
        if not clean:
            return None

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any] | None:
            return _json_loads(tr[self.pack("secure_session", clean)].wait())

        payload = _tx(self.db)
        return dict(payload) if isinstance(payload, dict) else None


class ExperimentalFoundationDbMultiSessionKeyStore(_StateComponent):
    def load(self) -> dict[str, Any]:
        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            data = _json_loads(tr[self.pack("multisession_keys")].wait()) or {}
            return self._normalize(data)

        return _tx(self.db)

    def save(self, data: dict[str, Any]) -> None:
        clean = self._normalize(data)

        @self.fdb.transactional
        def _tx(tr: Any) -> None:
            tr[self.pack("multisession_keys")] = _json_dumps(clean)

        _tx(self.db)

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        clean = dict(data) if isinstance(data, dict) else {}
        keys = clean.get("keys")
        if not isinstance(keys, dict):
            keys = {}
        clean["keys"] = {str(key): dict(value) for key, value in keys.items() if isinstance(value, dict)}
        clean.setdefault("version", "main-computer-multisession-keys-v1")
        return clean


class ExperimentalFoundationDbEnergyCreditLedger(EnergyCreditLedger):
    """FDB-backed local energy/payout ledger for experimental multi-hub runs."""

    def __init__(self, state: ExperimentalFoundationDbHubState, *, root: Path) -> None:
        super().__init__(root)
        self.state = state
        self.fdb = state.fdb
        self.db = state.db
        self.path = root / "foundationdb-energy-ledger.json"
        self.backend = "foundationdb"

    def _ledger_key(self) -> bytes:
        return self.state.pack("energy_ledger")

    def _read_ledger(self, tr: Any) -> dict[str, Any]:
        return self._normalize(_json_loads(tr[self._ledger_key()].wait()) or {})

    def _write_ledger(self, tr: Any, data: dict[str, Any]) -> None:
        tr[self._ledger_key()] = _json_dumps(self._normalize(data))

    def status(self, *, exact: bool = False, precision_places: Any = None) -> dict[str, Any]:
        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            data = self._read_ledger(tr)
            precision = normalize_worker_payout_precision_places(precision_places)
            return {
                "network": data["network"],
                "head": data["head"],
                "nodes": data["nodes"],
                "balances": self._balances(data),
                "payout_queue": self._payout_queue_status(data, exact=exact, precision_places=precision),
                "transactions": self._sanitize_transactions(data["transactions"][-25:], exact=exact, precision_places=precision),
                "backend": "foundationdb",
                "store_version": EXPERIMENTAL_FDB_HUB_STATE_VERSION,
            }

        return _tx(self.db)

    def register_node(self, node_id: str, role: str, endpoint: str) -> dict[str, Any]:
        clean_node_id = self._clean_id(node_id)

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            data = self._read_ledger(tr)
            if clean_node_id == data["head"]["node_id"]:
                raise ValueError("The head node is already registered.")
            nodes = [node for node in data["nodes"] if node["node_id"] != clean_node_id]
            nodes.append(asdict(EnergyNode(node_id=clean_node_id, role=role.strip() or "worker", endpoint=endpoint.strip())))
            data["nodes"] = sorted(nodes, key=lambda node: node["node_id"])
            self._write_ledger(tr, data)
            return data

        _tx(self.db)
        return self.status()

    def queue_worker_payout(self, node_id: str, credits: int, memo: str = "", request_id: str = "") -> dict[str, Any]:
        return self._queue_payout("hub_worker_payout_queued", node_id, credits, memo, request_id)

    def queue_upstream_hub_payout(self, node_id: str, credits: int, memo: str = "", request_id: str = "") -> dict[str, Any]:
        return self._queue_payout("hub_upstream_payout_queued", node_id, credits, memo, request_id)

    def _queue_payout(self, kind: str, node_id: str, credits: int, memo: str, request_id: str) -> dict[str, Any]:
        if credits <= 0:
            raise ValueError("Credits must be positive.")
        clean_node_id = self._clean_id(node_id)

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            data = self._read_ledger(tr)
            self._ensure_node(data, clean_node_id)
            created_at = datetime.now(tz=timezone.utc).isoformat()
            clean_request_id = str(request_id or "").strip()
            seed = f"{kind}|{clean_node_id}|{credits}|{memo}|{clean_request_id}|{created_at}".encode("utf-8")
            payout = PendingEnergyPayout(
                payout_id="payout_" + hashlib.sha256(seed).hexdigest()[:20],
                kind=kind,
                node_id=clean_node_id,
                credits=credits,
                memo=memo.strip(),
                request_id=clean_request_id,
                created_at=created_at,
            )
            data["pending_payouts"].append(asdict(payout))
            self._write_ledger(tr, data)
            return data

        _tx(self.db)
        return self.status()

    def payout_summary(self, node_id: str, *, exact: bool = False, precision_places: Any = None) -> dict[str, Any]:
        clean_node_id = self._clean_id(node_id)

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            data = self._read_ledger(tr)
            self._ensure_node(data, clean_node_id)
            precision = normalize_worker_payout_precision_places(precision_places)
            pending = [item for item in data["pending_payouts"] if item.get("node_id") == clean_node_id]
            exact_credits = sum(int(item.get("credits", 0) or 0) for item in pending)
            published_credits, dust_credits, precision, bucket_size = truncate_worker_payout_for_precision(
                exact_credits,
                precision_places=precision,
            )
            payload = {
                "ok": True,
                "node_id": clean_node_id,
                "pending_credits": exact_credits if exact else published_credits,
                "pending_credits_published": published_credits,
                "pending_count": len(pending),
                "pending_payouts": [
                    self._sanitize_payout(item, exact=exact, precision_places=precision)
                    for item in pending[-100:]
                ],
                "privacy": self._privacy_context(
                    exact=exact,
                    precision_places=precision,
                    rounding_bucket_credits=bucket_size,
                ),
                "ledger": {
                    "network": data["network"],
                    "head": data["head"],
                    "nodes": data["nodes"],
                    "balances": self._balances(data),
                    "payout_queue": self._payout_queue_status(data, exact=exact, precision_places=precision),
                    "transactions": self._sanitize_transactions(data["transactions"][-25:], exact=exact, precision_places=precision),
                    "backend": "foundationdb",
                },
            }
            if exact:
                payload["pending_credits_exact"] = exact_credits
                payload["bridge_retained_credits_if_claimed"] = dust_credits
            return payload

        return _tx(self.db)

    def claim_payouts(self, node_id: str, memo: str = "", *, exact: bool = False, precision_places: Any = None) -> dict[str, Any]:
        clean_node_id = self._clean_id(node_id)

        @self.fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            data = self._read_ledger(tr)
            self._ensure_node(data, clean_node_id)
            precision = normalize_worker_payout_precision_places(precision_places)
            selected = [item for item in data["pending_payouts"] if item.get("node_id") == clean_node_id]
            if not selected:
                return {
                    "ok": True,
                    "node_id": clean_node_id,
                    "claimed_credits": 0,
                    "claimed_credits_published": 0,
                    "claimed_count": 0,
                    "privacy": self._privacy_context(exact=exact, precision_places=precision),
                    "ledger": {
                        "network": data["network"],
                        "head": data["head"],
                        "nodes": data["nodes"],
                        "balances": self._balances(data),
                        "payout_queue": self._payout_queue_status(data, exact=exact, precision_places=precision),
                        "transactions": self._sanitize_transactions(data["transactions"][-25:], exact=exact, precision_places=precision),
                        "backend": "foundationdb",
                    },
                }
            credits = sum(int(item.get("credits", 0) or 0) for item in selected)
            published_credits, dust_credits, precision, bucket_size = truncate_worker_payout_for_precision(
                credits,
                precision_places=precision,
            )
            kinds = {str(item.get("kind", "")) for item in selected}
            if kinds == {"hub_upstream_payout_queued"}:
                kind = "hub_upstream_payout_claim"
            elif kinds == {"hub_worker_payout_queued"}:
                kind = "hub_worker_payout_claim"
            else:
                kind = "hub_payout_claim"
            memo_text = memo.strip() or f"claimed {len(selected)} queued hub payout(s)"
            tx_obj = self._transaction(kind, clean_node_id, credits, memo_text)
            data["transactions"].append(asdict(tx_obj))
            selected_ids = {str(item.get("payout_id", "")) for item in selected}
            data["pending_payouts"] = [item for item in data["pending_payouts"] if str(item.get("payout_id", "")) not in selected_ids]
            self._write_ledger(tr, data)
            transaction = asdict(tx_obj)
            if not exact:
                transaction = self._sanitize_transaction(transaction, exact=False, precision_places=precision)
            payload = {
                "ok": True,
                "node_id": clean_node_id,
                "claimed_credits": credits if exact else published_credits,
                "claimed_credits_published": published_credits,
                "claimed_count": len(selected),
                "transaction": transaction,
                "privacy": self._privacy_context(
                    exact=exact,
                    precision_places=precision,
                    rounding_bucket_credits=bucket_size,
                ),
                "ledger": {
                    "network": data["network"],
                    "head": data["head"],
                    "nodes": data["nodes"],
                    "balances": self._balances(data),
                    "payout_queue": self._payout_queue_status(data, exact=exact, precision_places=precision),
                    "transactions": self._sanitize_transactions(data["transactions"][-25:], exact=exact, precision_places=precision),
                    "backend": "foundationdb",
                },
            }
            if exact:
                payload["claimed_credits_exact"] = credits
                payload["bridge_retained_credits_if_claimed"] = dust_credits
            return payload

        return _tx(self.db)

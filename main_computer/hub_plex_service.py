from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from main_computer.energy import EnergyCreditLedger
from main_computer.hub_plex_models import (
    HubAIRequest,
    HubRequestRecord,
    HubRequestStatus,
    chat_response_from_payload,
    clean_node_id,
)
from main_computer.hub_security import HUB_SECURITY_PROFILE, hub_transport_is_encrypted_or_loopback
from main_computer.models import ChatResponse


HUB_WORKER_CHAT_PATH = "/api/hub/worker/chat"
HUB_WORKER_SESSION_START_PATH = "/api/hub/worker/sessions/start"
HUB_WORKER_SESSION_CHAT_PATH = "/api/hub/worker/sessions/chat"


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def stable_request_id(payload: dict[str, Any]) -> str:
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    stamp = str(time.time_ns()).encode("ascii")
    return "hub_" + hashlib.sha256(seed + stamp).hexdigest()[:20]


def stable_session_id(request_id: str, payload: dict[str, Any]) -> str:
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    stamp = str(time.time_ns()).encode("ascii")
    return "sess_" + hashlib.sha256(request_id.encode("utf-8") + seed + stamp).hexdigest()[:24]


def idempotent_request_id(client_node_id: str, idempotency_key: str) -> str:
    seed = json.dumps(
        {
            "client_node_id": clean_node_id(client_node_id, default="main-computer-client"),
            "idempotency_key": str(idempotency_key or "").strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return "hub_" + hashlib.sha256(seed).hexdigest()[:20]


def parse_utc(value: str) -> datetime | None:
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


def response_from_record(record: HubRequestRecord) -> ChatResponse | None:
    if not isinstance(record.response, dict):
        return None
    return ChatResponse(
        content=str(record.response.get("content", "")),
        provider=str(record.response.get("provider", "hub") or "hub"),
        model=str(record.response.get("model", record.model) or record.model),
        metadata=dict(record.response.get("metadata", {})) if isinstance(record.response.get("metadata"), dict) else {},
    )


def require_allowed_transport(url: str, *, role: str, allow_insecure_dev_network: bool = False) -> None:
    if not hub_transport_is_encrypted_or_loopback(
        url,
        allow_insecure_dev_network=allow_insecure_dev_network,
    ):
        raise ValueError(
            f"{role} endpoint must use HTTPS, except for local loopback development URLs. "
            "Set MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK=1 only for local Docker/dev networks."
        )


def _public_payload(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "as_dict"):
        data = item.as_dict()
        return dict(data) if isinstance(data, dict) else {}
    try:
        return asdict(item)
    except TypeError:
        return dict(getattr(item, "__dict__", {}))


def _item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _short_response_summary(response: ChatResponse) -> str:
    text = " ".join(str(response.content or "").split())
    if len(text) > 240:
        return text[:237] + "..."
    return text


class RequestStateStore:
    """JSON-backed state store for hub request lifecycle records."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "hub_requests.json"
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, record: HubRequestRecord) -> HubRequestRecord:
        with self._lock:
            data = self._load_unlocked()
            data[record.request_id] = record.as_dict()
            self._save_unlocked(data)
        return record

    def get(self, request_id: str) -> HubRequestRecord | None:
        clean = str(request_id or "").strip()
        if not clean:
            return None
        with self._lock:
            payload = self._load_unlocked().get(clean)
        return HubRequestRecord.from_dict(payload) if isinstance(payload, dict) else None

    def find_by_idempotency_key(self, *, client_node_id: str, idempotency_key: str) -> HubRequestRecord | None:
        clean_client = clean_node_id(client_node_id, default="main-computer-client")
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            return None
        with self._lock:
            records = self._load_unlocked()
            matches = [
                HubRequestRecord.from_dict(payload)
                for payload in records.values()
                if isinstance(payload, dict)
                and str(payload.get("client_node_id", "")) == clean_client
                and str(payload.get("idempotency_key", "")) == clean_key
            ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.created_at or item.updated_at)[-1]

    def list(self, *, limit: int = 100, states: set[str] | None = None) -> list[HubRequestRecord]:
        clean_limit = min(500, max(1, int(limit or 100)))
        with self._lock:
            records = [HubRequestRecord.from_dict(payload) for payload in self._load_unlocked().values() if isinstance(payload, dict)]
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
        with self._lock:
            data = self._load_unlocked()
            payload = data.get(clean)
            if not isinstance(payload, dict):
                raise KeyError(f"Unknown hub request: {clean}")
            record = HubRequestRecord.from_dict(payload)
            for key, value in changes.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            record.updated_at = utc_now()
            if event_type:
                event_payload = {
                    "type": event_type,
                    "state": record.state,
                    "created_at": record.updated_at,
                }
                if isinstance(event, dict):
                    event_payload.update(dict(event))
                record.events.append(event_payload)
            data[clean] = record.as_dict()
            self._save_unlocked(data)
            return record

    def cancel(self, request_id: str) -> HubRequestRecord:
        record = self.get(request_id)
        if record is None:
            raise KeyError(f"Unknown hub request: {request_id}")
        if record.state in {"completed", "failed", "cancelled", "expired"}:
            return record
        return self.update(record.request_id, state="cancelled", terminal_reason="client_cancelled", event_type="request.cancelled")

    def expire_deadlines(self) -> int:
        now = datetime.now(tz=timezone.utc)
        changed = 0
        with self._lock:
            data = self._load_unlocked()
            for request_id, payload in list(data.items()):
                if not isinstance(payload, dict):
                    continue
                record = HubRequestRecord.from_dict(payload)
                if record.state in {"completed", "failed", "cancelled", "expired"}:
                    continue
                deadline = parse_utc(record.deadline_at)
                if deadline is None or deadline >= now:
                    continue
                record.state = "expired"
                record.error = "Request deadline expired before completion."
                record.terminal_reason = "deadline_expired"
                record.updated_at = utc_now()
                record.events.append(
                    {
                        "type": "request.expired",
                        "state": record.state,
                        "created_at": record.updated_at,
                        "deadline_at": record.deadline_at,
                    }
                )
                data[request_id] = record.as_dict()
                changed += 1
            if changed:
                self._save_unlocked(data)
        return changed

    def metrics(self) -> dict[str, Any]:
        self.expire_deadlines()
        records = self.list(limit=500)
        by_state: dict[str, int] = {}
        for record in records:
            by_state[record.state] = by_state.get(record.state, 0) + 1
        active_states = {"queued", "leasing_worker", "dispatching", "running", "retrying"}
        terminal_states = {"completed", "failed", "cancelled", "expired"}
        return {
            "requests": {
                "total_recent": len(records),
                "active": sum(by_state.get(state, 0) for state in active_states),
                "terminal": sum(by_state.get(state, 0) for state in terminal_states),
                "by_state": by_state,
            }
        }

    def _load_unlocked(self) -> dict[str, dict[str, Any]]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    records = data.get("requests", data)
                    if isinstance(records, dict):
                        return {str(key): dict(value) for key, value in records.items() if isinstance(value, dict)}
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _save_unlocked(self, records: dict[str, dict[str, Any]]) -> None:
        self.path.write_text(
            json.dumps({"requests": records}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class AIRequestPlexService:
    """Coordinates AI request routing from the hub API to worker machines."""

    def __init__(
        self,
        registry: Any,
        ledger: EnergyCreditLedger,
        *,
        root: Path,
        timeout_s: float = 600.0,
        allow_insecure_dev_network: bool = False,
        credit_ledger: Any | None = None,
        default_credits_per_request: int = 1,
    ) -> None:
        self.registry = registry
        self.ledger = ledger
        self.timeout_s = max(1.0, float(timeout_s or 600.0))
        self.allow_insecure_dev_network = bool(allow_insecure_dev_network)
        self.credit_ledger = credit_ledger
        self.default_credits_per_request = max(1, int(default_credits_per_request or 1))
        self.request_store = RequestStateStore(root)
        self._secure_sessions: dict[str, dict[str, Any]] = {}
        self._session_lock = threading.Lock()

    def quote_request(self, request: HubAIRequest) -> dict[str, Any]:
        """Return the simple v0 paid request quote used by the mock-worker path."""

        estimated = max(1, self.default_credits_per_request)
        max_credits = max(0, int(request.max_credits or 0))
        if max_credits <= 0:
            max_credits = estimated
        if max_credits < estimated:
            raise ValueError(f"max_credits must be at least the quoted cost of {estimated}.")
        return {
            "ok": True,
            "unit": "compute_credit",
            "quote": {
                "model": str(request.model or ""),
                "estimated_credits": estimated,
                "max_credits": max_credits,
                "base_credits": self.default_credits_per_request,
                "expires_at": (datetime.now(tz=timezone.utc) + timedelta(minutes=5)).isoformat(),
            },
        }

    def submit(self, request: HubAIRequest, *, polling_base_path: str = "/api/hub/v1/requests") -> HubRequestStatus:
        _response, status = self.dispatch_sync_with_status(request, polling_base_path=polling_base_path)
        return status

    def dispatch_sync(self, request: HubAIRequest) -> ChatResponse:
        response, _status = self.dispatch_sync_with_status(request)
        return response

    def dispatch_sync_with_status(
        self,
        request: HubAIRequest,
        *,
        polling_base_path: str = "/api/hub/v1/requests",
    ) -> tuple[ChatResponse, HubRequestStatus]:
        normalized = request.as_payload()
        existing = self._idempotent_record(request)
        if existing is not None:
            status = HubRequestStatus.from_record(existing, polling_url=f"{polling_base_path.rstrip('/')}/{existing.request_id}")
            existing_response = response_from_record(existing)
            if existing_response is not None:
                return existing_response, status
            if existing.state in {"failed", "cancelled", "expired"}:
                raise RuntimeError(existing.error or f"Idempotent request already ended in state {existing.state}.")
            raise RuntimeError(f"Idempotent request is already {existing.state}; poll {status.polling_url} for completion.")

        request_id = (
            idempotent_request_id(request.client_node_id, request.idempotency_key)
            if request.idempotency_key
            else stable_request_id(normalized)
        )
        record = self._create_record(
            request_id=request_id,
            request=request,
            security_mode="legacy-plaintext",
            hub_blind=False,
        )
        try:
            if self._request_requires_paid_account(request):
                self._ensure_paid_hold(record, request)
            response = self._dispatch_plaintext(record, request)
            status = self.get_status(request_id, polling_base_path=polling_base_path)
            return response, status
        except Exception as exc:
            self._release_paid_hold_for_request(request_id, reason="dispatch_failed", error=str(exc))
            try:
                self.request_store.update(
                    request_id,
                    state="failed",
                    error=str(exc),
                    terminal_reason="dispatch_failed",
                    event_type="request.failed",
                    event={"error": str(exc)},
                )
            except Exception:
                pass
            raise

    def get_status(self, request_id: str, *, polling_base_path: str = "/api/hub/v1/requests") -> HubRequestStatus:
        record = self.request_store.get(request_id)
        if record is None:
            raise KeyError(f"Unknown hub request: {request_id}")
        return HubRequestStatus.from_record(record, polling_url=f"{polling_base_path.rstrip('/')}/{record.request_id}")

    def cancel(self, request_id: str, *, polling_base_path: str = "/api/hub/v1/requests") -> HubRequestStatus:
        record = self.request_store.cancel(request_id)
        self._release_record_worker(record, success=False)
        self._release_paid_hold_for_request(record.request_id, reason="client_cancelled")
        refreshed = self.request_store.get(record.request_id) or record
        return HubRequestStatus.from_record(refreshed, polling_url=f"{polling_base_path.rstrip('/')}/{record.request_id}")

    def list_statuses(
        self,
        *,
        limit: int = 100,
        states: set[str] | None = None,
        polling_base_path: str = "/api/hub/v1/requests",
    ) -> list[HubRequestStatus]:
        self.request_store.expire_deadlines()
        return [
            HubRequestStatus.from_record(record, polling_url=f"{polling_base_path.rstrip('/')}/{record.request_id}")
            for record in self.request_store.list(limit=limit, states=states)
        ]

    def get_events(self, request_id: str) -> list[dict[str, Any]]:
        return self.request_store.events(request_id)

    def metrics(self) -> dict[str, Any]:
        request_metrics = self.request_store.metrics()
        registry_status = self.registry.status() if hasattr(self.registry, "status") else {}
        workers = [item for item in registry_status.get("workers", []) if isinstance(item, dict)]
        worker_by_status: dict[str, int] = {}
        for worker in workers:
            status = str(worker.get("status", "unknown") or "unknown")
            worker_by_status[status] = worker_by_status.get(status, 0) + 1
        return {
            "ok": True,
            **request_metrics,
            "workers": {
                "total": len(workers),
                "available": int(registry_status.get("available_worker_count", 0) or 0),
                "stale": int(registry_status.get("stale_worker_count", 0) or 0),
                "by_status": worker_by_status,
                "active_requests": sum(max(0, int(worker.get("active_requests", 0) or 0)) for worker in workers),
                "queued_depth": sum(max(0, int(worker.get("queue_depth", 0) or 0)) for worker in workers),
            },
            "upstreams": {
                "total": int(registry_status.get("upstream_count", 0) or 0),
            },
        }

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
            "client_node_id": clean_node_id(client_node_id, default="main-computer-client"),
            "hop_count": max(0, int(hop_count or 0)),
        }
        request_id = stable_request_id(request_payload)
        request = HubAIRequest(
            messages=[],
            model=str(model or ""),
            client_node_id=request_payload["client_node_id"],
            hop_count=request_payload["hop_count"],
        )
        record = self._create_record(
            request_id=request_id,
            request=request,
            security_mode="high-security",
            hub_blind=True,
        )
        try:
            return self._start_secure_session_for_record(
                record=record,
                requester_public_key=requester_public_key,
                model=model,
                client_node_id=client_node_id,
                hop_count=hop_count,
            )
        except Exception as exc:
            try:
                self.request_store.update(
                    request_id,
                    state="failed",
                    error=str(exc),
                    event_type="request.failed",
                )
            except Exception:
                pass
            raise

    def secure_chat(self, *, session_id: str, request_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        session = self._session(session_id)
        stored_request_id = str(session.get("request_id", ""))
        local_record_id = str(session.get("local_request_id") or stored_request_id)
        if stored_request_id and request_id and stored_request_id != request_id:
            raise ValueError("Hub session request id mismatch.")
        try:
            self.request_store.update(
                local_record_id,
                state="dispatching",
                event_type="request.dispatching",
            )
            if session.get("kind") == "upstream":
                return self._secure_chat_upstream(session=session, envelope=envelope)
            return self._secure_chat_worker(session=session, envelope=envelope)
        except Exception as exc:
            try:
                self.request_store.update(
                    local_record_id,
                    state="failed",
                    error=str(exc),
                    event_type="request.failed",
                )
            except Exception:
                pass
            raise

    def _idempotent_record(self, request: HubAIRequest) -> HubRequestRecord | None:
        if not request.idempotency_key:
            return None
        return self.request_store.find_by_idempotency_key(
            client_node_id=request.client_node_id,
            idempotency_key=request.idempotency_key,
        )

    def _deadline_at(self, request: HubAIRequest) -> str:
        if request.deadline_seconds <= 0:
            return ""
        return (datetime.now(tz=timezone.utc) + timedelta(seconds=float(request.deadline_seconds))).isoformat()

    def _max_retries(self, request: HubAIRequest) -> int:
        raw = request.metadata.get("max_retries") if isinstance(request.metadata, dict) else None
        try:
            return max(0, min(5, int(raw if raw is not None else 1)))
        except (TypeError, ValueError):
            return 1

    def _lease_worker_for_request(self, request: HubAIRequest, *, request_id: str) -> Any:
        if hasattr(self.registry, "lease_worker"):
            return self.registry.lease_worker(
                request.model,
                request_id=request_id,
                preferred_node_id=request.requested_worker_node_id,
                lease_seconds=self.timeout_s,
            )
        return self.registry.select_worker(request.model)

    def _release_record_worker(self, record: HubRequestRecord, *, success: bool) -> None:
        node_id = str(record.selected_worker_node_id or "")
        if not node_id:
            return
        if hasattr(self.registry, "release_worker"):
            self.registry.release_worker(node_id, request_id=record.request_id, success=success)
            return
        self.registry.mark_worker(node_id, status="available" if success else "offline")

    def _record_attempt(
        self,
        record: HubRequestRecord,
        *,
        attempt: int,
        worker_node_id: str,
        worker_model: str,
        error: str = "",
    ) -> list[dict[str, Any]]:
        history = [dict(item) for item in record.attempt_history]
        history.append(
            {
                "attempt": attempt,
                "worker_node_id": worker_node_id,
                "model": worker_model,
                "error": error,
                "created_at": utc_now(),
            }
        )
        return history

    def _request_requires_paid_account(self, request: HubAIRequest) -> bool:
        return bool(str(request.account_id or "").strip() or int(request.max_credits or 0) > 0)

    def _ensure_paid_hold(self, record: HubRequestRecord, request: HubAIRequest) -> dict[str, Any]:
        if self.credit_ledger is None:
            raise ValueError("Paid request accounting is not available on this hub.")
        if not request.account_id:
            raise ValueError("account_id is required for paid requests.")
        quote = self.quote_request(request)
        max_credits = int(quote["quote"]["max_credits"])
        hold_result = self.credit_ledger.create_hold(
            account_id=request.account_id,
            request_id=record.request_id,
            credits=max_credits,
            expires_at=record.deadline_at,
            memo=f"paid request hold {record.request_id}",
            metadata={"quote": quote["quote"], "idempotency_key": request.idempotency_key},
        )
        hold = dict(hold_result.get("hold", {}))
        self.request_store.update(
            record.request_id,
            account_id=hold.get("account_id", request.account_id),
            max_credits=max_credits,
            hold_id=str(hold.get("hold_id", "")),
            event_type="payment.hold.created",
            event={
                "account_id": hold.get("account_id", request.account_id),
                "hold_id": hold.get("hold_id", ""),
                "held_credits": hold.get("credits", max_credits),
                "idempotent": bool(hold_result.get("idempotent", False)),
            },
        )
        return hold_result

    def _release_paid_hold_for_request(self, request_id: str, *, reason: str, error: str = "") -> None:
        if self.credit_ledger is None:
            return
        record = self.request_store.get(request_id)
        if record is None or not record.hold_id or record.charge_id:
            return
        try:
            release = self.credit_ledger.release_hold(
                hold_id=record.hold_id,
                reason=reason,
                memo=f"release paid request hold {record.request_id}: {reason}",
                metadata={"error": error} if error else {},
            )
        except Exception:
            return
        hold = dict(release.get("hold", {}))
        self.request_store.update(
            record.request_id,
            released_credits=max(0, int(hold.get("credits", record.max_credits) or 0)),
            event_type="payment.hold.released",
            event={
                "account_id": record.account_id,
                "hold_id": record.hold_id,
                "reason": reason,
                "error": error,
            },
        )

    def _finalize_paid_request(
        self,
        *,
        record: HubRequestRecord,
        worker_node_id: str,
        worker_credits: int,
    ) -> dict[str, Any]:
        if self.credit_ledger is None or not record.hold_id:
            return {}
        charge = self.credit_ledger.charge_hold(
            hold_id=record.hold_id,
            charged_credits=worker_credits,
            worker_node_id=worker_node_id,
            memo=f"paid request charge {record.request_id}",
            metadata={"worker_node_id": worker_node_id},
        )
        charge_payload = dict(charge.get("charge", {}))
        earning_payload = dict(charge.get("worker_earning") or {})
        receipt = {
            "request_id": record.request_id,
            "account_id": charge_payload.get("account_id", record.account_id),
            "hold_id": record.hold_id,
            "charge_id": charge_payload.get("charge_id", ""),
            "charged_credits": max(0, int(charge_payload.get("charged_credits", 0) or 0)),
            "released_credits": max(0, int(charge_payload.get("released_credits", 0) or 0)),
            "worker_earning_id": earning_payload.get("earning_id", ""),
            "worker_commitment": earning_payload.get("worker_commitment", ""),
            "created_at": charge_payload.get("created_at", utc_now()),
            "unit": "compute_credit",
        }
        self.request_store.update(
            record.request_id,
            account_id=receipt["account_id"],
            charge_id=receipt["charge_id"],
            charged_credits=receipt["charged_credits"],
            released_credits=receipt["released_credits"],
            worker_earning_id=receipt["worker_earning_id"],
            receipt=receipt,
            event_type="payment.charge.created",
            event=receipt,
        )
        return receipt

    def _create_record(
        self,
        *,
        request_id: str,
        request: HubAIRequest,
        security_mode: str,
        hub_blind: bool,
    ) -> HubRequestRecord:
        now = utc_now()
        record = HubRequestRecord(
            request_id=request_id,
            client_node_id=clean_node_id(request.client_node_id, default="main-computer-client"),
            model=str(request.model or ""),
            state="queued",
            created_at=now,
            updated_at=now,
            security_mode=security_mode,
            hub_blind=hub_blind,
            events=[{"type": "request.accepted", "state": "queued", "created_at": now}],
            idempotency_key=str(request.idempotency_key or "").strip(),
            deadline_at=self._deadline_at(request),
            max_retries=self._max_retries(request),
            requested_worker_node_id=clean_node_id(request.requested_worker_node_id, default="") if request.requested_worker_node_id else "",
            account_id=clean_node_id(request.account_id, default="") if request.account_id else "",
            max_credits=max(0, int(request.max_credits or 0)),
        )
        return self.request_store.create(record)

    def _dispatch_plaintext(self, record: HubRequestRecord, request: HubAIRequest) -> ChatResponse:
        if request.hop_count >= 8:
            raise RuntimeError("Hub forwarding loop detected: hop limit exceeded.")

        last_error: Exception | None = None
        max_attempts = max(1, record.max_retries + 1)
        for attempt in range(1, max_attempts + 1):
            current = self.request_store.get(record.request_id) or record
            deadline = parse_utc(current.deadline_at)
            if deadline is not None and deadline < datetime.now(tz=timezone.utc):
                self.request_store.update(
                    record.request_id,
                    state="expired",
                    error="Request deadline expired before a worker completed it.",
                    terminal_reason="deadline_expired",
                    event_type="request.expired",
                    event={"deadline_at": current.deadline_at},
                )
                raise RuntimeError("Request deadline expired before a worker completed it.")
            self.request_store.update(
                record.request_id,
                state="leasing_worker",
                retry_count=max(0, attempt - 1),
                event_type="worker.lease.started",
                event={"attempt": attempt, "max_attempts": max_attempts},
            )
            worker = self._lease_worker_for_request(request, request_id=record.request_id)
            if worker is None:
                break
            worker_payload = _public_payload(worker)
            worker_node_id = str(_item_value(worker, "node_id", ""))
            worker_endpoint = str(_item_value(worker, "endpoint", "")).rstrip("/")
            worker_model = str(_item_value(worker, "model", "") or "")
            worker_credits = max(1, int(_item_value(worker, "credits_per_request", 1) or 1))
            attempt_history = self._record_attempt(
                current,
                attempt=attempt,
                worker_node_id=worker_node_id,
                worker_model=worker_model,
            )
            self.request_store.update(
                record.request_id,
                state="dispatching",
                selected_worker_node_id=worker_node_id,
                attempt_history=attempt_history,
                event_type="worker.selected",
                event={"attempt": attempt, "worker_node_id": worker_node_id, "worker_model": worker_model},
            )
            payload = {
                "request_id": record.request_id,
                "model": request.model or worker_model,
                "client_node_id": clean_node_id(request.client_node_id, default="main-computer-client"),
                "messages": request.messages,
                "energy": {
                    "credits": worker_credits,
                    "settlement": "batched-worker-claim",
                    "memo": f"hub request {record.request_id}",
                },
            }
            try:
                paid_record = self.request_store.get(record.request_id) or current
                if paid_record.hold_id and worker_credits > max(0, int(paid_record.max_credits or 0)):
                    raise RuntimeError(
                        f"Selected worker requires {worker_credits} credits but request only held {paid_record.max_credits}."
                    )
                self.request_store.update(
                    record.request_id,
                    state="running",
                    event_type="request.started",
                    event={"attempt": attempt, "worker_node_id": worker_node_id},
                )
                response_payload = self._post_json(worker_endpoint + HUB_WORKER_CHAT_PATH, payload)
                response = chat_response_from_payload(
                    response_payload,
                    default_provider="hub-worker",
                    default_model=worker_model or request.model or "hub-worker-model",
                )
            except Exception as exc:
                last_error = exc
                try:
                    if hasattr(self.registry, "release_worker"):
                        self.registry.release_worker(worker_node_id, request_id=record.request_id, success=False)
                    else:
                        self.registry.mark_worker(worker_node_id, status="offline")
                finally:
                    refreshed = self.request_store.get(record.request_id) or current
                    failed_history = self._record_attempt(
                        refreshed,
                        attempt=attempt,
                        worker_node_id=worker_node_id,
                        worker_model=worker_model,
                        error=str(exc),
                    )
                    next_state = "retrying" if attempt < max_attempts else "leasing_worker"
                    self.request_store.update(
                        record.request_id,
                        state=next_state,
                        retry_count=attempt,
                        attempt_history=failed_history,
                        error=str(exc),
                        event_type="worker.offline",
                        event={"attempt": attempt, "worker_node_id": worker_node_id, "error": str(exc)},
                    )
                continue

            latest_record = self.request_store.get(record.request_id) or record
            receipt = self._finalize_paid_request(
                record=latest_record,
                worker_node_id=worker_node_id,
                worker_credits=worker_credits,
            )
            energy_status = self.ledger.queue_worker_payout(
                worker_node_id,
                worker_credits,
                memo=f"hub request {record.request_id}",
                request_id=record.request_id,
            )
            self._release_record_worker(
                HubRequestRecord.from_dict({**record.as_dict(), "selected_worker_node_id": worker_node_id}),
                success=True,
            )
            metadata = dict(response.metadata)
            metadata["hub"] = {
                "request_id": record.request_id,
                "worker_node_id": worker_node_id,
                "worker_endpoint": worker_endpoint,
                "credits_queued": worker_credits,
                "settlement": "batched-worker-claim",
                "payout_queue": energy_status.get("payout_queue", {}),
                "security_mode": "legacy-plaintext",
                "hub_blind": False,
                "attempt": attempt,
            }
            if receipt:
                metadata["hub"]["payment"] = dict(receipt)
            completed = ChatResponse(
                content=response.content,
                provider="hub",
                model=response.model,
                metadata=metadata,
            )
            self.request_store.update(
                record.request_id,
                state="completed",
                selected_worker_node_id=worker_node_id,
                response={
                    "content": completed.content,
                    "provider": completed.provider,
                    "model": completed.model,
                    "metadata": completed.metadata,
                },
                response_summary=_short_response_summary(completed),
                credits_queued=worker_credits,
                charge_id=str(receipt.get("charge_id", "")) if receipt else "",
                charged_credits=max(0, int(receipt.get("charged_credits", 0) or 0)) if receipt else 0,
                released_credits=max(0, int(receipt.get("released_credits", 0) or 0)) if receipt else 0,
                worker_earning_id=str(receipt.get("worker_earning_id", "")) if receipt else "",
                receipt=dict(receipt),
                error="",
                terminal_reason="completed",
                event_type="request.completed",
                event={"attempt": attempt, "worker_node_id": worker_node_id},
            )
            return completed

        upstream = self.registry.select_upstream_hub()
        if upstream is not None and not request.requested_worker_node_id:
            return self._forward_to_upstream(
                record=record,
                request=request,
                last_worker_error=last_error,
            )
        if last_error is not None:
            raise RuntimeError(f"Worker dispatch failed and no upstream hub is available: {last_error}") from last_error
        raise RuntimeError("No hub workers or upstream hubs are registered or available.")
    def _start_secure_session_for_record(
        self,
        *,
        record: HubRequestRecord,
        requester_public_key: str,
        model: str,
        client_node_id: str,
        hop_count: int,
    ) -> dict[str, Any]:
        if hop_count >= 8:
            raise RuntimeError("Hub forwarding loop detected: hop limit exceeded.")

        request_payload = {
            "requester_public_key": requester_public_key,
            "model": model,
            "client_node_id": clean_node_id(client_node_id, default="main-computer-client"),
            "hop_count": max(0, int(hop_count or 0)),
        }
        last_error: Exception | None = None
        for attempt in range(2):
            self.request_store.update(
                record.request_id,
                state="leasing_worker",
                event_type="worker.lease.started",
            )
            worker = self._lease_worker_for_request(
                HubAIRequest(
                    messages=[],
                    model=str(model or ""),
                    client_node_id=request_payload["client_node_id"],
                    hop_count=request_payload["hop_count"],
                ),
                request_id=record.request_id,
            )
            if worker is None:
                break
            worker_node_id = str(_item_value(worker, "node_id", ""))
            worker_endpoint = str(_item_value(worker, "endpoint", "")).rstrip("/")
            worker_model = str(_item_value(worker, "model", "") or "")
            worker_credits = max(1, int(_item_value(worker, "credits_per_request", 1) or 1))
            session_id = stable_session_id(record.request_id, request_payload)
            payload = {
                "request_id": record.request_id,
                "session_id": session_id,
                "model": model or worker_model,
                "client_node_id": request_payload["client_node_id"],
                "requester_public_key": requester_public_key,
                "energy": {
                    "credits": worker_credits,
                    "settlement": "batched-worker-claim",
                    "memo": f"hub request {record.request_id}",
                },
            }
            self.request_store.update(
                record.request_id,
                state="dispatching",
                selected_worker_node_id=worker_node_id,
                session_id=session_id,
                event_type="worker.selected",
            )
            try:
                worker_session = self._post_json(worker_endpoint + HUB_WORKER_SESSION_START_PATH, payload)
            except Exception as exc:
                last_error = exc
                if hasattr(self.registry, "release_worker"):
                    self.registry.release_worker(worker_node_id, request_id=record.request_id, success=False)
                else:
                    self.registry.mark_worker(worker_node_id, status="offline")
                self.request_store.update(
                    record.request_id,
                    state="leasing_worker",
                    error=str(exc),
                    event_type="worker.offline",
                )
                continue

            worker_public_key = str(worker_session.get("worker_public_key") or "")
            if not worker_public_key:
                raise RuntimeError("Hub worker did not return a temporary public key.")
            with self._session_lock:
                self._secure_sessions[session_id] = {
                    "kind": "worker",
                    "session_id": session_id,
                    "request_id": record.request_id,
                    "worker": _public_payload(worker),
                    "credits": worker_credits,
                    "created_at": utc_now(),
                }
            self.request_store.update(
                record.request_id,
                state="running",
                selected_worker_node_id=worker_node_id,
                session_id=session_id,
                error="",
                event_type="request.started",
            )
            return {
                "ok": True,
                "mode": "high-security",
                "session_id": session_id,
                "request_id": record.request_id,
                "worker_public_key": worker_public_key,
                "encryption": {
                    "profile": HUB_SECURITY_PROFILE,
                    "temporary_public_keys": True,
                    "hub_blind": True,
                },
                "metadata": {
                    "hub": {
                        "request_id": record.request_id,
                        "worker_node_id": worker_node_id,
                        "worker_endpoint": worker_endpoint,
                        "credits_queued": worker_credits,
                        "settlement": "batched-worker-claim",
                        "security_mode": "high-security",
                        "hub_blind": True,
                        "encryption_profile": HUB_SECURITY_PROFILE,
                        "attempt": attempt + 1,
                    }
                },
            }

        upstream = self.registry.select_upstream_hub()
        if upstream is not None:
            return self._start_upstream_secure_session(
                record=record,
                requester_public_key=requester_public_key,
                model=model,
                client_node_id=client_node_id,
                hop_count=hop_count,
            )
        if last_error is not None:
            raise RuntimeError(f"Worker secure-session start failed and no upstream hub is available: {last_error}") from last_error
        raise RuntimeError("No hub workers or upstream hubs are registered or available.")

    def _secure_chat_worker(self, *, session: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        worker_data = dict(session.get("worker", {}))
        worker_node_id = str(worker_data.get("node_id", ""))
        worker_endpoint = str(worker_data.get("endpoint", "")).rstrip("/")
        worker_credits = max(1, int(worker_data.get("credits_per_request", session.get("credits", 1)) or 1))
        request_id = str(session.get("request_id", ""))
        try:
            worker_response = self._post_json(
                worker_endpoint + HUB_WORKER_SESSION_CHAT_PATH,
                {
                    "session_id": session["session_id"],
                    "request_id": request_id,
                    "envelope": envelope,
                },
            )
        except Exception:
            if hasattr(self.registry, "release_worker"):
                self.registry.release_worker(worker_node_id, request_id=request_id, success=False)
            else:
                self.registry.mark_worker(worker_node_id, status="offline")
            raise

        energy_status = self.ledger.queue_worker_payout(
            worker_node_id,
            worker_credits,
            memo=f"hub request {request_id}",
            request_id=request_id,
        )
        if hasattr(self.registry, "release_worker"):
            self.registry.release_worker(worker_node_id, request_id=request_id, success=True)
        else:
            self.registry.mark_worker(worker_node_id, status="available")
        hub_meta = {
            "request_id": request_id,
            "worker_node_id": worker_node_id,
            "worker_endpoint": worker_endpoint,
            "credits_queued": worker_credits,
            "settlement": "batched-worker-claim",
            "payout_queue": energy_status.get("payout_queue", {}),
            "security_mode": "high-security",
            "hub_blind": True,
            "encryption_profile": HUB_SECURITY_PROFILE,
        }
        response_payload = {
            "ok": True,
            "mode": "high-security",
            "session_id": session["session_id"],
            "request_id": request_id,
            "response_envelope": worker_response.get("response_envelope"),
            "metadata": {"hub": hub_meta},
        }
        self.request_store.update(
            request_id,
            state="completed",
            selected_worker_node_id=worker_node_id,
            response={"metadata": response_payload["metadata"], "encrypted": True},
            response_summary="encrypted response envelope relayed",
            credits_queued=worker_credits,
            error="",
            event_type="request.completed",
        )
        return response_payload

    def _start_upstream_secure_session(
        self,
        *,
        record: HubRequestRecord,
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

        upstream_node_id = str(_item_value(upstream, "node_id", ""))
        upstream_endpoint = str(_item_value(upstream, "endpoint", "")).rstrip("/")
        upstream_credits = max(1, int(_item_value(upstream, "credits_per_request", 1) or 1))
        payload = {
            "model": model,
            "client_node_id": clean_node_id(client_node_id, default="main-computer-client"),
            "requester_public_key": requester_public_key,
            "hop_count": hop_count + 1,
            "forwarded_by": record.request_id,
        }
        self.request_store.update(
            record.request_id,
            state="dispatching",
            selected_upstream_hub_node_id=upstream_node_id,
            event_type="upstream.selected",
        )
        try:
            upstream_session = self._post_json(upstream_endpoint + "/api/hub/sessions/start", payload)
        except Exception:
            self.registry.mark_upstream_hub(upstream_node_id, status="offline")
            raise

        upstream_session_id = str(upstream_session.get("session_id", ""))
        upstream_request_id = str(upstream_session.get("request_id", ""))
        if not upstream_session_id or not upstream_request_id:
            raise RuntimeError("Upstream hub did not return a complete high-security session.")
        with self._session_lock:
            self._secure_sessions[upstream_session_id] = {
                "kind": "upstream",
                "session_id": upstream_session_id,
                "request_id": upstream_request_id,
                "local_request_id": record.request_id,
                "upstream": _public_payload(upstream),
                "upstream_session_id": upstream_session_id,
                "upstream_request_id": upstream_request_id,
                "credits": upstream_credits,
                "created_at": utc_now(),
            }
        self.registry.mark_upstream_hub(upstream_node_id, status="available")
        self.request_store.update(
            record.request_id,
            state="running",
            selected_upstream_hub_node_id=upstream_node_id,
            session_id=upstream_session_id,
            error="",
            event_type="request.started",
        )
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
                    "local_request_id": record.request_id,
                    "upstream_hub_node_id": upstream_node_id,
                    "upstream_hub_endpoint": upstream_endpoint,
                    "forwarded": True,
                    "credits_queued": upstream_credits,
                    "settlement": "batched-worker-claim",
                    "security_mode": "high-security",
                    "hub_blind": True,
                    "encryption_profile": HUB_SECURITY_PROFILE,
                }
            },
        }

    def _secure_chat_upstream(self, *, session: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        upstream_data = dict(session.get("upstream", {}))
        upstream_node_id = str(upstream_data.get("node_id", ""))
        upstream_endpoint = str(upstream_data.get("endpoint", "")).rstrip("/")
        upstream_credits = max(1, int(upstream_data.get("credits_per_request", session.get("credits", 1)) or 1))
        request_id = str(session.get("request_id", ""))
        local_request_id = str(session.get("local_request_id", request_id))
        try:
            upstream_response = self._post_json(
                upstream_endpoint + "/api/hub/sessions/chat",
                {
                    "session_id": str(session.get("upstream_session_id", "")),
                    "request_id": str(session.get("upstream_request_id", "")),
                    "envelope": envelope,
                },
            )
        except Exception:
            self.registry.mark_upstream_hub(upstream_node_id, status="offline")
            raise

        energy_status = self.ledger.queue_upstream_hub_payout(
            upstream_node_id,
            upstream_credits,
            memo=f"hub upstream request {local_request_id}",
            request_id=local_request_id,
        )
        self.registry.mark_upstream_hub(upstream_node_id, status="available")
        upstream_meta = {}
        if isinstance(upstream_response.get("metadata"), dict):
            upstream_meta = dict(upstream_response["metadata"].get("hub", {})) if isinstance(upstream_response["metadata"].get("hub"), dict) else {}
        hub_meta = {
            **upstream_meta,
            "request_id": request_id,
            "upstream_hub_node_id": upstream_node_id,
            "upstream_hub_endpoint": upstream_endpoint,
            "forwarded": True,
            "credits_queued": upstream_credits,
            "settlement": "batched-worker-claim",
            "payout_queue": energy_status.get("payout_queue", {}),
            "security_mode": "high-security",
            "hub_blind": True,
            "encryption_profile": HUB_SECURITY_PROFILE,
        }
        response_payload = {
            "ok": True,
            "mode": "high-security",
            "session_id": session["session_id"],
            "request_id": request_id,
            "response_envelope": upstream_response.get("response_envelope"),
            "metadata": {"hub": hub_meta},
        }
        self.request_store.update(
            local_request_id,
            state="completed",
            selected_upstream_hub_node_id=upstream_node_id,
            response={"metadata": response_payload["metadata"], "encrypted": True},
            response_summary="encrypted upstream response envelope relayed",
            credits_queued=upstream_credits,
            error="",
            event_type="request.completed",
        )
        return response_payload

    def _forward_to_upstream(
        self,
        *,
        record: HubRequestRecord,
        request: HubAIRequest,
        last_worker_error: Exception | None = None,
    ) -> ChatResponse:
        if request.hop_count >= 8:
            raise RuntimeError("Hub forwarding loop detected: hop limit exceeded.")
        upstream = self.registry.select_upstream_hub()
        if upstream is None:
            if last_worker_error is not None:
                raise RuntimeError(f"Worker dispatch failed and no upstream hub is available: {last_worker_error}") from last_worker_error
            raise RuntimeError("No hub workers or upstream hubs are registered or available.")

        upstream_node_id = str(_item_value(upstream, "node_id", ""))
        upstream_endpoint = str(_item_value(upstream, "endpoint", "")).rstrip("/")
        upstream_credits = max(1, int(_item_value(upstream, "credits_per_request", 1) or 1))
        payload = {
            "model": request.model,
            "client_node_id": clean_node_id(request.client_node_id, default="main-computer-client"),
            "messages": request.messages,
            "hop_count": request.hop_count + 1,
            "forwarded_by": record.request_id,
            "high_security": False,
        }
        self.request_store.update(
            record.request_id,
            state="dispatching",
            selected_upstream_hub_node_id=upstream_node_id,
            event_type="upstream.selected",
        )
        try:
            self.request_store.update(record.request_id, state="running", event_type="request.started")
            response_payload = self._post_json(upstream_endpoint + "/api/hub/chat", payload)
            response = chat_response_from_payload(
                response_payload,
                default_provider="upstream-hub",
                default_model=request.model or "hub-forwarded-model",
            )
        except Exception:
            self.registry.mark_upstream_hub(upstream_node_id, status="offline")
            raise

        energy_status = self.ledger.queue_upstream_hub_payout(
            upstream_node_id,
            upstream_credits,
            memo=f"hub upstream request {record.request_id}",
            request_id=record.request_id,
        )
        self.registry.mark_upstream_hub(upstream_node_id, status="available")
        metadata = dict(response.metadata)
        upstream_meta = dict(metadata.get("hub", {})) if isinstance(metadata.get("hub"), dict) else {}
        metadata["hub"] = {
            **upstream_meta,
            "request_id": record.request_id,
            "upstream_hub_node_id": upstream_node_id,
            "upstream_hub_endpoint": upstream_endpoint,
            "forwarded": True,
            "credits_queued": upstream_credits,
            "settlement": "batched-worker-claim",
            "payout_queue": energy_status.get("payout_queue", {}),
            "security_mode": "legacy-plaintext",
            "hub_blind": False,
        }
        completed = ChatResponse(
            content=response.content,
            provider="hub",
            model=response.model,
            metadata=metadata,
        )
        self.request_store.update(
            record.request_id,
            state="completed",
            selected_upstream_hub_node_id=upstream_node_id,
            response={
                "content": completed.content,
                "provider": completed.provider,
                "model": completed.model,
                "metadata": completed.metadata,
            },
            response_summary=_short_response_summary(completed),
            credits_queued=upstream_credits,
            error="",
            event_type="request.completed",
        )
        return completed

    def _session(self, session_id: str) -> dict[str, Any]:
        clean = str(session_id or "").strip()
        with self._session_lock:
            session = dict(self._secure_sessions.get(clean, {}))
        if not session:
            raise ValueError("Unknown or expired hub session.")
        session["session_id"] = clean
        return session

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        require_allowed_transport(
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

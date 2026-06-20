from __future__ import annotations

import hashlib
import json
import os
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
    sanitize_hub_response_payload,
    sanitize_requester_response_payload,
)
from main_computer.hub_security import HUB_SECURITY_PROFILE, hub_transport_is_encrypted_or_loopback
from main_computer.hub_credit_models import (
    WorkerQualityReport,
    make_report_token,
    normalize_address,
    token_digest,
)
from main_computer.models import ChatResponse


HUB_WORKER_CHAT_PATH = "/api/hub/worker/chat"
HUB_WORKER_SESSION_START_PATH = "/api/hub/worker/sessions/start"
HUB_WORKER_SESSION_CHAT_PATH = "/api/hub/worker/sessions/chat"
PHASE9_PRICING_MODE = "market_offer_fixed_per_call_v0"
PHASE9_PRICING_TYPE = "fixed_per_call_v0"
PHASE9_EXECUTION_MODE = "worker_pull_v0"
DEFAULT_REQUESTER_RESULT_RETENTION_WINDOW_SECONDS = 3600


def phase9_offer_id(*, worker_node_id: str, models: list[str], credits_per_request: int, execution_mode: str) -> str:
    seed = json.dumps(
        {
            "worker_node_id": str(worker_node_id or ""),
            "models": sorted(str(model) for model in models if str(model).strip()),
            "pricing_type": PHASE9_PRICING_TYPE,
            "credits_per_request": max(0, int(credits_per_request or 0)),
            "unit": "compute_credit",
            "execution_mode": str(execution_mode or PHASE9_EXECUTION_MODE),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return "offer_" + hashlib.sha256(seed).hexdigest()[:24]


def phase9_quote_id(*, account_id: str, idempotency_key: str, seed_payload: dict[str, Any]) -> str:
    if idempotency_key:
        seed = {"account_id": clean_node_id(account_id, default=""), "idempotency_key": str(idempotency_key or "").strip()}
    else:
        seed = {**dict(seed_payload), "stamp": time.time_ns()}
    return "quote_" + hashlib.sha256(json.dumps(seed, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def normalize_execution_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in {"worker_pull", "worker_pull_v0"}:
        return PHASE9_EXECUTION_MODE
    return text


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _worker_ring_from_payload(payload: dict[str, Any]) -> int | None:
    worker = _public_payload(payload)
    capabilities = dict(worker.get("capabilities", {})) if isinstance(worker.get("capabilities"), dict) else {}
    existing_offer = dict(worker.get("offer", {})) if isinstance(worker.get("offer"), dict) else {}
    network = dict(capabilities.get("network", {})) if isinstance(capabilities.get("network"), dict) else {}
    for candidate in (
        existing_offer.get("assigned_ring"),
        existing_offer.get("ring"),
        worker.get("effective_ring"),
        worker.get("assigned_ring"),
        worker.get("ring"),
        capabilities.get("effective_ring"),
        capabilities.get("assigned_ring"),
        capabilities.get("ring"),
        capabilities.get("requested_ring"),
        network.get("assigned_ring"),
        network.get("ring"),
    ):
        parsed = _optional_int(candidate)
        if parsed is not None:
            return parsed
    return None


def _request_requested_ring(request: HubAIRequest) -> int | None:
    metadata = dict(request.metadata) if isinstance(request.metadata, dict) else {}
    for candidate in (
        metadata.get("requested_ring"),
        metadata.get("ring"),
        metadata.get("max_ring"),
        metadata.get("worker_ring"),
    ):
        parsed = _optional_int(candidate)
        if parsed is not None:
            return parsed
    return None


def market_worker_offer_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    worker = _public_payload(payload)
    capabilities = dict(worker.get("capabilities", {})) if isinstance(worker.get("capabilities"), dict) else {}
    pricing = dict(capabilities.get("pricing", {})) if isinstance(capabilities.get("pricing"), dict) else {}
    existing_offer = dict(worker.get("offer", {})) if isinstance(worker.get("offer"), dict) else {}
    if bool(capabilities.get("phase9_unpriced", False)):
        return {}
    pricing_type = str(
        existing_offer.get("pricing_type") or pricing.get("pricing_type") or pricing.get("type") or PHASE9_PRICING_TYPE
    ).strip()
    if pricing_type in {"", "none", "unpriced", "unpriced_v0"}:
        return {}
    try:
        credits = int(
            existing_offer.get("credits_per_request")
            or pricing.get("credits_per_request")
            or worker.get("credits_per_request")
            or 0
        )
    except (TypeError, ValueError):
        credits = 0
    if credits <= 0:
        return {}
    models = [
        str(model).strip()
        for model in (
            existing_offer.get("models")
            if isinstance(existing_offer.get("models"), list)
            else worker.get("models") if isinstance(worker.get("models"), list) else []
        )
        if str(model).strip()
    ]
    model = str(worker.get("model", "") or "").strip()
    if model and model not in models:
        models.insert(0, model)
    if not models:
        return {}
    execution = dict(capabilities.get("execution", {})) if isinstance(capabilities.get("execution"), dict) else {}
    execution_mode = normalize_execution_mode(
        existing_offer.get("execution_mode")
        or pricing.get("execution_mode")
        or execution.get("mode")
        or capabilities.get("execution_mode")
        or PHASE9_EXECUTION_MODE
    )
    worker_node_id = str(existing_offer.get("worker_node_id") or worker.get("node_id") or "")
    worker_instance_id = str(
        existing_offer.get("worker_instance_id")
        or worker.get("worker_instance_id")
        or capabilities.get("worker_instance_id")
        or worker_node_id
    )
    assigned_ring = _worker_ring_from_payload(worker)
    offer = {
        "offer_id": str(existing_offer.get("offer_id") or phase9_offer_id(
            worker_node_id=worker_instance_id or worker_node_id,
            models=models,
            credits_per_request=credits,
            execution_mode=execution_mode,
        )),
        "worker_node_id": worker_node_id,
        "worker_instance_id": worker_instance_id,
        "seller_kind": str(existing_offer.get("seller_kind") or "hub_connected_worker"),
        "models": models,
        "capabilities": list(existing_offer.get("capabilities", []))
        if isinstance(existing_offer.get("capabilities"), list)
        else ["chat.completions"],
        "pricing_type": PHASE9_PRICING_TYPE,
        "credits_per_request": credits,
        "unit": "compute_credit",
        "execution_mode": execution_mode,
        "price_source": str(existing_offer.get("price_source") or "worker_registration"),
        "settlement": dict(existing_offer.get("settlement", {}))
        if isinstance(existing_offer.get("settlement"), dict)
        else {
            "earning_mode": "worker_earning_v0",
            "claim_mode": "worker_claim_v0",
            "settlement_mode": "rounded_batch_v0",
        },
    }
    if assigned_ring is not None:
        offer["assigned_ring"] = assigned_ring
    return offer


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



def stable_lease_id(request_id: str, worker_node_id: str) -> str:
    seed = json.dumps(
        {"request_id": str(request_id or ""), "worker_node_id": str(worker_node_id or ""), "stamp": time.time_ns()},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return "lease_" + hashlib.sha256(seed).hexdigest()[:24]


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
    response = sanitize_hub_response_payload(record.response)
    return ChatResponse(
        content=str(response.get("content", "")),
        provider=str(response.get("provider", "hub") or "hub"),
        model=str(response.get("model", record.model) or record.model),
        metadata=dict(response.get("metadata", {})) if isinstance(response.get("metadata"), dict) else {},
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


def _worker_wallet_address_from_payload(item: Any) -> str:
    payload = _public_payload(item)
    capabilities = dict(payload.get("capabilities", {})) if isinstance(payload.get("capabilities"), dict) else {}
    wallet = (
        payload.get("wallet_address")
        or payload.get("worker_wallet_address")
        or payload.get("payout_wallet_address")
        or capabilities.get("wallet_address")
        or capabilities.get("worker_wallet_address")
        or capabilities.get("payout_wallet_address")
        or ""
    )
    return normalize_address(str(wallet or ""))


REQUESTER_FEEDBACK_VERDICTS = {"accepted", "rejected", "needs_revision"}
REQUESTER_FEEDBACK_TAGS = {
    "correct",
    "useful",
    "low_quality",
    "wrong_format",
    "incomplete",
    "unsafe",
    "timeout_but_recovered",
    "fail_signal",
    "random_noise",
}


def _clean_feedback_tag(value: Any) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value or "").strip().lower())


def _feedback_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    clean.pop("worker_node_id", None)
    clean.pop("worker_instance_id", None)
    clean.pop("worker_wallet_address", None)
    clean.pop("report_token_hash", None)
    clean["worker_identity_private"] = True
    clean["money_movement"] = False
    return clean


def _feedback_identity(report: dict[str, Any]) -> dict[str, str]:
    requester = str(
        report.get("requester_wallet_address")
        or report.get("account_id")
        or report.get("requester_account_id")
        or ""
    ).strip()
    return {
        "request_id": str(report.get("request_id", "") or "").strip(),
        "account_id": str(report.get("account_id", "") or report.get("requester_account_id", "") or "").strip(),
        "requester_key": requester,
    }


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
        active_states = {"submitted", "held", "queued", "leasing_worker", "dispatching", "running", "retrying", "leased"}
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




class QuoteStateStore:
    """JSON-backed quote snapshots used for Phase 9 market-backed requests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "hub_quotes.json"
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def create_or_get(self, quote: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        quote_id = str(quote.get("quote_id", "") or "").strip()
        if not quote_id:
            raise ValueError("quote_id is required.")
        with self._lock:
            data = self._load_unlocked()
            existing = data.get(quote_id)
            if isinstance(existing, dict):
                return dict(existing), True
            data[quote_id] = dict(quote)
            self._save_unlocked(data)
            return dict(quote), False

    def get(self, quote_id: str) -> dict[str, Any] | None:
        clean = str(quote_id or "").strip()
        if not clean:
            return None
        with self._lock:
            payload = self._load_unlocked().get(clean)
        return dict(payload) if isinstance(payload, dict) else None

    def find_by_idempotency_key(self, *, account_id: str, idempotency_key: str) -> dict[str, Any] | None:
        clean_account = clean_node_id(account_id, default="") if account_id else ""
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            return None
        with self._lock:
            quotes = [
                dict(payload)
                for payload in self._load_unlocked().values()
                if isinstance(payload, dict)
                and str(payload.get("account_id", "")) == clean_account
                and str(payload.get("idempotency_key", "")) == clean_key
            ]
        if not quotes:
            return None
        return sorted(quotes, key=lambda item: str(item.get("created_at", "")))[-1]

    def _load_unlocked(self) -> dict[str, dict[str, Any]]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    records = data.get("quotes", data)
                    if isinstance(records, dict):
                        return {str(key): dict(value) for key, value in records.items() if isinstance(value, dict)}
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _save_unlocked(self, records: dict[str, dict[str, Any]]) -> None:
        self.path.write_text(
            json.dumps({"quotes": records}, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class FeedbackStateStore:
    """JSON-backed requester feedback/complaint records.

    Current state is one feedback record per request/account pair.  Resubmitting
    the same body is idempotent; changing the body replaces the current record
    and appends the prior version to audit history.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "hub_requester_feedback.json"
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def submit(self, report: dict[str, Any]) -> dict[str, Any]:
        clean = self._normalize_report(report)
        key = str(clean["feedback_key"])
        with self._lock:
            data = self._load_unlocked()
            current = data.get(key)
            if isinstance(current, dict):
                current_body = self._idempotency_body(current)
                new_body = self._idempotency_body(clean)
                if current_body == new_body:
                    result = dict(current)
                    result["idempotent"] = True
                    return result
                history = [dict(item) for item in current.get("history", []) if isinstance(item, dict)]
                archived = dict(current)
                archived.pop("history", None)
                archived["archived_at"] = utc_now()
                history.append(archived)
                clean["version"] = max(1, int(current.get("version", 1) or 1)) + 1
                clean["created_at"] = str(current.get("created_at") or clean["created_at"])
                clean["updated_at"] = utc_now()
                clean["history"] = history[-25:]
            data[key] = clean
            self._save_unlocked(data)
        result = dict(clean)
        result["idempotent"] = False
        return result

    def get_for_request(self, request_id: str, *, account_id: str = "") -> list[dict[str, Any]]:
        clean_request = str(request_id or "").strip()
        clean_account = clean_node_id(account_id, default="") if account_id else ""
        if not clean_request:
            return []
        with self._lock:
            records = [
                dict(payload)
                for payload in self._load_unlocked().values()
                if isinstance(payload, dict)
                and str(payload.get("request_id", "") or "") == clean_request
                and (not clean_account or str(payload.get("account_id", "") or "") == clean_account)
            ]
        return sorted(records, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))

    def list(self, *, limit: int = 500) -> list[dict[str, Any]]:
        clean_limit = min(2000, max(1, int(limit or 500)))
        with self._lock:
            records = [dict(payload) for payload in self._load_unlocked().values() if isinstance(payload, dict)]
        return sorted(records, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)[:clean_limit]

    def _normalize_report(self, report: dict[str, Any]) -> dict[str, Any]:
        request_id = str(report.get("request_id", "") or "").strip()
        account_id = clean_node_id(str(report.get("account_id") or report.get("requester_account_id") or ""), default="")
        if not request_id or not account_id:
            raise ValueError("request_id and account_id are required for feedback.")
        requester_wallet = normalize_address(str(report.get("requester_wallet_address", "") or ""))
        now = utc_now()
        supplied_key = str(report.get("feedback_key", "") or "").strip()
        key = supplied_key or f"{request_id}:{account_id}"
        clean = dict(report)
        clean["feedback_key"] = key
        clean["feedback_id"] = str(report.get("feedback_id") or hashlib.sha256(key.encode("utf-8")).hexdigest()[:24])
        clean["request_id"] = request_id
        clean["account_id"] = account_id
        clean["requester_account_id"] = account_id
        clean["requester_wallet_address"] = requester_wallet
        clean["feedback_channel"] = clean_node_id(str(report.get("feedback_channel", "") or ""), default="") if report.get("feedback_channel") else ""
        clean["score"] = max(1, min(5, int(report.get("score", report.get("rating", 1)) or 1)))
        clean["rating"] = clean["score"]
        verdict = str(report.get("verdict", "") or "").strip().lower()
        if verdict not in REQUESTER_FEEDBACK_VERDICTS:
            verdict = "accepted" if clean["score"] >= 4 else "rejected" if clean["score"] <= 2 else "needs_revision"
        clean["verdict"] = verdict
        tags: list[str] = []
        for raw in report.get("feedback_tags", report.get("tags", [])) or []:
            tag = _clean_feedback_tag(raw)
            if tag and (tag in REQUESTER_FEEDBACK_TAGS or tag.startswith("lab_")) and tag not in tags:
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

    def _load_unlocked(self) -> dict[str, dict[str, Any]]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    records = data.get("feedback", data)
                    if isinstance(records, dict):
                        return {str(key): dict(value) for key, value in records.items() if isinstance(value, dict)}
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _save_unlocked(self, records: dict[str, dict[str, Any]]) -> None:
        self.path.write_text(
            json.dumps({"feedback": records}, ensure_ascii=False, indent=2, sort_keys=True),
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
        request_store: Any | None = None,
        quote_store: Any | None = None,
        secure_session_store: Any | None = None,
        feedback_store: Any | None = None,
    ) -> None:
        self.registry = registry
        self.ledger = ledger
        self.timeout_s = max(1.0, float(timeout_s or 600.0))
        self.allow_insecure_dev_network = bool(allow_insecure_dev_network)
        self.credit_ledger = credit_ledger
        self.root = Path(root)
        self.default_credits_per_request = max(1, int(default_credits_per_request or 1))
        self.request_store = request_store if request_store is not None else RequestStateStore(root)
        self.quote_store = quote_store if quote_store is not None else QuoteStateStore(root)
        self.feedback_store = feedback_store if feedback_store is not None else FeedbackStateStore(root)
        self.secure_session_store = secure_session_store
        self._secure_sessions: dict[str, dict[str, Any]] = {}
        self._session_lock = threading.Lock()

    def _store_secure_session(self, session_id: str, payload: dict[str, Any]) -> None:
        clean = str(session_id or "").strip()
        if not clean:
            raise ValueError("session_id is required.")
        data = dict(payload)
        data["session_id"] = clean
        store = self.secure_session_store
        if store is not None and hasattr(store, "set"):
            store.set(clean, data)
            return
        with self._session_lock:
            self._secure_sessions[clean] = data

    def _load_secure_session(self, session_id: str) -> dict[str, Any]:
        clean = str(session_id or "").strip()
        store = self.secure_session_store
        if store is not None and hasattr(store, "get"):
            session = store.get(clean)
            if isinstance(session, dict):
                data = dict(session)
                data["session_id"] = clean
                return data
            return {}
        with self._session_lock:
            session = dict(self._secure_sessions.get(clean, {}))
        if session:
            session["session_id"] = clean
        return session

    def quote_request(self, request: HubAIRequest) -> dict[str, Any]:
        """Return a paid request quote.

        Legacy callers without an explicit market/worker-pull pricing mode keep the
        original default-price quote. Phase 9 callers get a durable quote backed by
        a compatible priced worker seller offer.
        """

        if self._market_pricing_requested(request):
            quote, idempotent = self._quote_market_paid_request(request)
            return {"ok": True, "unit": "compute_credit", "quote": quote, "idempotent": idempotent}

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


    def submit_worker_pull(
        self,
        request: HubAIRequest,
        *,
        polling_base_path: str = "/api/hub/v1/requests",
    ) -> HubRequestStatus:
        """Accept a paid request for outbound worker-pull leasing.

        This path is intentionally opt-in for Worker Pull v0. It creates the
        paid hold before the request becomes visible to worker polling.
        """

        if not self._request_requires_paid_account(request):
            raise ValueError("Worker Pull v0 requires a paid account_id and max_credits hold.")
        normalized = request.as_payload()
        existing = self._idempotent_record(request)
        if existing is not None:
            return HubRequestStatus.from_record(existing, polling_url=f"{polling_base_path.rstrip('/')}/{existing.request_id}")

        request_id = (
            idempotent_request_id(request.client_node_id, request.idempotency_key)
            if request.idempotency_key
            else stable_request_id(normalized)
        )
        record = self._create_record(
            request_id=request_id,
            request=request,
            security_mode="legacy-plaintext-worker-pull-v0",
            hub_blind=False,
            initial_state="submitted",
            initial_event_type="request.submitted",
        )
        try:
            self._ensure_paid_hold(record, request)
            held = self.request_store.get(request_id) or record
            self.request_store.update(
                request_id,
                state="held",
                event_type="request.held",
                event={"account_id": held.account_id, "hold_id": held.hold_id},
            )
            self.request_store.update(
                request_id,
                state="queued",
                event_type="request.queued",
                event={"worker_pull_v0": True, "held": True},
            )
            queued = self.request_store.get(request_id) or record
            return HubRequestStatus.from_record(queued, polling_url=f"{polling_base_path.rstrip('/')}/{request_id}")
        except Exception as exc:
            self._release_paid_hold_for_request(request_id, reason="worker_pull_submit_failed", error=str(exc))
            try:
                self.request_store.update(
                    request_id,
                    state="failed",
                    error=str(exc),
                    terminal_reason="worker_pull_submit_failed",
                    event_type="request.failed",
                    event={"error": str(exc)},
                )
            except Exception:
                pass
            raise

    def poll_worker(
        self,
        *,
        worker_node_id: str,
        worker_instance_id: str = "",
        lease_seconds: float | None = None,
        polling_base_path: str = "/api/hub/v1/requests",
    ) -> dict[str, Any]:
        """Return one held queued request as a worker-pull lease, if available."""

        clean_worker_id = clean_node_id(worker_node_id, default="hub-worker")
        clean_worker_instance_id = (
            clean_node_id(worker_instance_id, default="") if worker_instance_id else clean_worker_id
        )
        self._expire_worker_pull_leases()
        worker = (
            self.registry.get_worker(clean_worker_id, worker_instance_id=clean_worker_instance_id)
            if hasattr(self.registry, "get_worker")
            else None
        )
        if worker is None:
            raise KeyError(f"Unknown hub worker: {clean_worker_id}")

        candidates = sorted(
            self.request_store.list(limit=500, states={"queued"}),
            key=lambda item: item.created_at or item.updated_at,
        )
        for record in candidates:
            if not record.hold_id or record.charge_id:
                continue
            worker_node_identity = str(_item_value(worker, "node_id", clean_worker_id))
            worker_instance_identity = str(_item_value(worker, "worker_instance_id", worker_node_identity))
            if record.selected_worker_instance_id and record.selected_worker_instance_id != worker_instance_identity:
                continue
            if record.requested_worker_node_id and record.requested_worker_node_id not in {
                clean_worker_id,
                clean_worker_instance_id,
                worker_node_identity,
                worker_instance_identity,
            }:
                continue
            if not self._worker_can_run_record(worker, record):
                continue
            leased_worker = self.registry.lease_worker(
                record.model,
                request_id=record.request_id,
                preferred_node_id=record.requested_worker_node_id or worker_node_identity,
                preferred_worker_instance_id=record.selected_worker_instance_id or worker_instance_identity,
                lease_seconds=lease_seconds or self.timeout_s,
            ) if hasattr(self.registry, "lease_worker") else worker
            if leased_worker is None:
                return {"ok": True, "lease": None}
            worker_node_id = str(_item_value(leased_worker, "node_id", clean_worker_id))
            leased_worker_instance_id = str(_item_value(leased_worker, "worker_instance_id", worker_node_id))
            selected_offer = self._record_selected_offer(record)
            quoted_credits = self._record_quoted_credits(record)
            worker_credits = quoted_credits or max(1, int(_item_value(leased_worker, "credits_per_request", 1) or 1))
            if worker_credits > max(0, int(record.max_credits or 0)):
                self._release_paid_hold_for_request(
                    record.request_id,
                    reason="worker_pull_worker_price_exceeds_hold",
                    error=f"Worker requires {worker_credits} credits but request held {record.max_credits}.",
                )
                self.request_store.update(
                    record.request_id,
                    state="failed",
                    error=f"Worker requires {worker_credits} credits but request held {record.max_credits}.",
                    terminal_reason="worker_price_exceeds_hold",
                    event_type="request.failed",
                    event={
                        "worker_node_id": worker_node_id,
                        "worker_instance_id": leased_worker_instance_id,
                        "credits_per_request": worker_credits,
                    },
                )
                self._release_record_worker(
                    HubRequestRecord.from_dict(
                        {
                            **record.as_dict(),
                            "selected_worker_node_id": worker_node_id,
                            "selected_worker_instance_id": leased_worker_instance_id,
                        }
                    ),
                    success=False,
                )
                continue
            lease_id = stable_lease_id(record.request_id, leased_worker_instance_id)
            expires_at = (datetime.now(tz=timezone.utc) + timedelta(seconds=max(1.0, float(lease_seconds or self.timeout_s)))).isoformat()
            request_payload = dict(record.request_payload)
            attempt_history = self._record_attempt(
                record,
                attempt=max(1, len(record.attempt_history) + 1),
                worker_node_id=worker_node_id,
                worker_instance_id=leased_worker_instance_id,
                worker_model=str(_item_value(leased_worker, "model", "") or record.model),
            )
            if hasattr(self.request_store, "claim_worker_pull_lease"):
                claimed_record = self.request_store.claim_worker_pull_lease(
                    record.request_id,
                    worker_node_id=worker_node_id,
                    worker_instance_id=leased_worker_instance_id,
                    lease_id=lease_id,
                    expires_at=expires_at,
                    credits_queued=worker_credits,
                    attempt_history=attempt_history,
                )
                if claimed_record is None:
                    self._release_record_worker(
                        HubRequestRecord.from_dict(
                            {
                                **record.as_dict(),
                                "selected_worker_node_id": worker_node_id,
                                "selected_worker_instance_id": leased_worker_instance_id,
                            }
                        ),
                        success=True,
                    )
                    continue
                record = claimed_record
            else:
                record = self.request_store.update(
                    record.request_id,
                    state="leased",
                    selected_worker_node_id=worker_node_id,
                    selected_worker_instance_id=leased_worker_instance_id,
                    lease_id=lease_id,
                    lease_expires_at=expires_at,
                    credits_queued=worker_credits,
                    attempt_history=attempt_history,
                    event_type="worker_pull.lease.granted",
                    event={
                        "worker_node_id": worker_node_id,
                        "worker_instance_id": leased_worker_instance_id,
                        "lease_id": lease_id,
                        "expires_at": expires_at,
                    },
                )
            lease = {
                "lease_id": lease_id,
                "request_id": record.request_id,
                "model": str(request_payload.get("model") or record.model),
                "messages": [dict(item) for item in request_payload.get("messages", []) if isinstance(item, dict)],
                "mock_provider_config": dict(request_payload.get("metadata", {}).get("mock_provider_config", {}))
                if isinstance(request_payload.get("metadata"), dict)
                and isinstance(request_payload.get("metadata", {}).get("mock_provider_config"), dict)
                else {},
                "expires_at": expires_at,
                "worker_node_id": worker_node_id,
                "worker_instance_id": leased_worker_instance_id,
            }
            market_metadata = self._record_market_metadata(record)
            if market_metadata:
                lease["pricing"] = {
                    "quoted_credits": worker_credits,
                    "worker_earning_credits": worker_credits,
                    "unit": "compute_credit",
                    "pricing_mode": str(market_metadata.get("pricing_mode", PHASE9_PRICING_MODE) or PHASE9_PRICING_MODE),
                    "execution_mode": str(market_metadata.get("execution_mode", PHASE9_EXECUTION_MODE) or PHASE9_EXECUTION_MODE),
                }
                if selected_offer:
                    lease["selected_offer"] = {
                        "offer_id": str(selected_offer.get("offer_id", "")),
                        "worker_node_id": str(selected_offer.get("worker_node_id", "")),
                        "worker_instance_id": str(
                            selected_offer.get("worker_instance_id", "") or selected_offer.get("worker_node_id", "")
                        ),
                        "credits_per_request": max(0, int(selected_offer.get("credits_per_request", 0) or 0)),
                    }
                    assigned_ring = _optional_int(selected_offer.get("assigned_ring"))
                    if assigned_ring is not None:
                        lease["selected_offer"]["assigned_ring"] = assigned_ring
            return {
                "ok": True,
                "lease": lease,
                "request": HubRequestStatus.from_record(
                    self.request_store.get(record.request_id) or record,
                    polling_url=f"{polling_base_path.rstrip('/')}/{record.request_id}",
                ).as_dict(),
            }
        return {"ok": True, "lease": None}

    def _record_result_retention_window_seconds(self, record: HubRequestRecord) -> int:
        metadata = (
            dict(record.request_payload.get("metadata", {}))
            if isinstance(record.request_payload, dict) and isinstance(record.request_payload.get("metadata"), dict)
            else {}
        )
        raw = (
            metadata.get("requester_result_retention_window_seconds")
            or metadata.get("result_retention_window_seconds")
            or metadata.get("pickup_window_seconds")
            or DEFAULT_REQUESTER_RESULT_RETENTION_WINDOW_SECONDS
        )
        try:
            return max(0, int(float(raw)))
        except (TypeError, ValueError):
            return DEFAULT_REQUESTER_RESULT_RETENTION_WINDOW_SECONDS

    def _record_result_retention_payload(
        self,
        record: HubRequestRecord,
        *,
        retained_at: str | None = None,
    ) -> dict[str, Any]:
        retained_at_text = str(retained_at or record.updated_at or utc_now())
        window_seconds = self._record_result_retention_window_seconds(record)
        retained_dt = parse_utc(retained_at_text) or datetime.now(tz=timezone.utc)
        expires_at = (
            retained_dt + timedelta(seconds=window_seconds)
            if window_seconds > 0
            else retained_dt
        ).isoformat()
        return {
            "mode": "requester_result_pickup_v0",
            "retained": True,
            "window_seconds": window_seconds,
            "retained_at": retained_at_text,
            "expires_at": expires_at,
        }

    def _record_result_retention_status(self, record: HubRequestRecord) -> dict[str, Any]:
        metadata = dict(record.response.get("metadata", {})) if isinstance(record.response, dict) and isinstance(record.response.get("metadata"), dict) else {}
        hub_metadata = dict(metadata.get("hub", {})) if isinstance(metadata.get("hub"), dict) else {}
        retention = (
            dict(hub_metadata.get("result_retention", {}))
            if isinstance(hub_metadata.get("result_retention"), dict)
            else self._record_result_retention_payload(record)
        )
        expires_at = str(retention.get("expires_at", "") or "")
        expires_dt = parse_utc(expires_at)
        expired = bool(expires_dt is not None and expires_dt < datetime.now(tz=timezone.utc))
        retention["expired"] = expired
        retention["retained"] = bool(retention.get("retained", True)) and not expired
        return retention

    def pickup_completed_result(
        self,
        request_id: str,
        *,
        account_id: str = "",
        client_node_id: str = "",
        polling_base_path: str = "/api/hub/v1/requests",
    ) -> dict[str, Any]:
        """Return a retained completed result for a reconnecting requester.

        Worker loss is still terminal/no-charge.  This pickup path is only for
        requests that already completed successfully while the requester was not
        actively polling the Hub.
        """

        clean_request_id = str(request_id or "").strip()
        if not clean_request_id:
            raise ValueError("request_id is required.")
        record = self.request_store.get(clean_request_id)
        if record is None:
            raise KeyError(f"Unknown hub request: {clean_request_id}")
        clean_account_id = clean_node_id(account_id, default="") if account_id else ""
        clean_client_id = clean_node_id(client_node_id, default="") if client_node_id else ""
        if clean_account_id and record.account_id and clean_account_id != record.account_id:
            raise PermissionError("Requester account does not own this request.")
        if clean_client_id and clean_client_id != record.client_node_id:
            raise PermissionError("Requester client does not own this request.")
        retention = self._record_result_retention_status(record)
        status = HubRequestStatus.from_record(
            record,
            polling_url=f"{polling_base_path.rstrip('/')}/{record.request_id}",
        ).as_requester_dict()
        if record.state != "completed" or not isinstance(record.response, dict):
            return {
                "ok": False,
                "request_id": record.request_id,
                "state": record.state,
                "retained": False,
                "expired": False,
                "result_available": False,
                "retention": retention,
                "request": status,
            }
        if bool(retention.get("expired", False)):
            return {
                "ok": False,
                "request_id": record.request_id,
                "state": record.state,
                "retained": False,
                "expired": True,
                "result_available": False,
                "retention": retention,
                "request": status,
            }
        response = sanitize_requester_response_payload(record.response)
        return {
            "ok": True,
            "request_id": record.request_id,
            "state": record.state,
            "retained": True,
            "expired": False,
            "result_available": True,
            "retention": retention,
            "response": response,
            "result": response,
            "request": status,
        }

    def submit_worker_result(
        self,
        *,
        worker_node_id: str,
        request_id: str,
        lease_id: str,
        result: dict[str, Any],
        worker_instance_id: str = "",
        polling_base_path: str = "/api/hub/v1/requests",
    ) -> dict[str, Any]:
        """Accept a worker-pull result and finalize paid accounting exactly once."""

        self._expire_worker_pull_leases()
        clean_worker_id = clean_node_id(worker_node_id, default="hub-worker")
        clean_worker_instance_id = (
            clean_node_id(worker_instance_id, default="") if worker_instance_id else clean_worker_id
        )
        clean_request_id = str(request_id or "").strip()
        clean_lease_id = str(lease_id or "").strip()
        if not clean_request_id or not clean_lease_id:
            raise ValueError("request_id and lease_id are required.")
        record = self.request_store.get(clean_request_id)
        if record is None:
            raise KeyError(f"Unknown hub request: {clean_request_id}")
        if record.state == "completed" or record.charge_id:
            if record.selected_worker_node_id and record.selected_worker_node_id != clean_worker_id:
                raise ValueError("Worker result replay was submitted by the wrong worker.")
            if record.selected_worker_instance_id and record.selected_worker_instance_id != clean_worker_instance_id:
                raise ValueError("Worker result replay was submitted by the wrong worker instance.")
            if record.lease_id and record.lease_id != clean_lease_id:
                raise ValueError("Worker result replay lease_id does not match the accepted lease.")
            return {
                "ok": True,
                "idempotent": True,
                "duplicate_completion_additional_charge": 0,
                "request": HubRequestStatus.from_record(
                    record,
                    polling_url=f"{polling_base_path.rstrip('/')}/{record.request_id}",
                ).as_dict(),
            }
        if record.state != "leased":
            raise ValueError(f"Request is not leased; current state is {record.state}.")
        if record.lease_id != clean_lease_id:
            raise ValueError("Worker result lease_id does not match the active lease.")
        if record.selected_worker_node_id != clean_worker_id:
            raise ValueError("Worker result was submitted by the wrong worker.")
        if record.selected_worker_instance_id and record.selected_worker_instance_id != clean_worker_instance_id:
            raise ValueError("Worker result was submitted by the wrong worker instance.")
        lease_deadline = parse_utc(record.lease_expires_at)
        if lease_deadline is not None and lease_deadline < datetime.now(tz=timezone.utc):
            self._fail_worker_pull_lease_timeout(record)
            raise ValueError("Worker result lease has expired; request failed without charging requester.")
        if not isinstance(result, dict):
            raise ValueError("result must be a JSON object.")
        status = str(result.get("status") or "success").strip().lower()
        if status not in {"success", "ok", "completed"}:
            error = str(result.get("error") or result.get("message") or "worker result reported failure")
            self._release_paid_hold_for_request(record.request_id, reason="worker_pull_result_failed", error=error)
            self._release_record_worker(record, success=False)
            failed = self.request_store.update(
                record.request_id,
                state="failed",
                error=error,
                terminal_reason="worker_result_failed",
                lease_id="",
                lease_expires_at="",
                event_type="request.failed",
                event={
                    "worker_node_id": clean_worker_id,
                    "worker_instance_id": clean_worker_instance_id,
                    "lease_id": clean_lease_id,
                    "error": error,
                },
            )
            return {"ok": False, "request": HubRequestStatus.from_record(failed, polling_url=f"{polling_base_path.rstrip('/')}/{record.request_id}").as_dict()}

        response_payload = result.get("response") if isinstance(result.get("response"), dict) else result
        response = chat_response_from_payload(
            response_payload,
            default_provider="hub-worker-pull",
            default_model=record.model or "hub-worker-model",
        )
        quoted_credits = self._record_quoted_credits(record)
        worker_credits = quoted_credits or max(1, int(record.credits_queued or result.get("charged_credits") or 1))
        if worker_credits > max(0, int(record.max_credits or 0)):
            raise ValueError(f"Worker result charge {worker_credits} exceeds held max_credits {record.max_credits}.")
        latest_record = self.request_store.get(record.request_id) or record
        receipt = self._finalize_paid_request(
            record=latest_record,
            worker_node_id=clean_worker_id,
            worker_credits=worker_credits,
        )
        energy_status = self.ledger.queue_worker_payout(
            clean_worker_id,
            worker_credits,
            memo=f"hub worker-pull request {record.request_id}",
            request_id=record.request_id,
        )
        self._release_record_worker(record, success=True)
        metadata = dict(response.metadata)
        retained_at = utc_now()
        result_retention = self._record_result_retention_payload(record, retained_at=retained_at)
        metadata["hub"] = {
            "request_id": record.request_id,
            "worker_node_id": clean_worker_id,
            "worker_instance_id": clean_worker_instance_id,
            "credits_queued": worker_credits,
            "settlement": "batched-worker-claim",
            "payout_queue": energy_status.get("payout_queue", {}),
            "security_mode": "legacy-plaintext-worker-pull-v0",
            "hub_blind": False,
            "result_retention": result_retention,
            "worker_pull_v0": True,
            "lease_id": clean_lease_id,
        }
        market_metadata = self._record_market_metadata(record)
        if market_metadata:
            selected_offer = self._record_selected_offer(record)
            metadata["hub"]["pricing"] = {
                "quoted_credits": worker_credits,
                "worker_earning_credits": worker_credits,
                "unit": "compute_credit",
                "pricing_mode": str(market_metadata.get("pricing_mode", PHASE9_PRICING_MODE) or PHASE9_PRICING_MODE),
                "execution_mode": str(market_metadata.get("execution_mode", PHASE9_EXECUTION_MODE) or PHASE9_EXECUTION_MODE),
            }
            if selected_offer:
                metadata["hub"]["selected_offer"] = {
                    "offer_id": str(selected_offer.get("offer_id", "")),
                    "worker_node_id": str(selected_offer.get("worker_node_id", "")),
                    "worker_instance_id": str(
                        selected_offer.get("worker_instance_id", "") or selected_offer.get("worker_node_id", "")
                    ),
                    "credits_per_request": max(0, int(selected_offer.get("credits_per_request", 0) or 0)),
                }
        if receipt:
            metadata["hub"]["payment"] = dict(receipt)
        completed = ChatResponse(
            content=response.content,
            provider="hub",
            model=response.model,
            metadata=metadata,
        )
        completed_record = self.request_store.update(
            record.request_id,
            state="completed",
            selected_worker_node_id=clean_worker_id,
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
            event={
                "worker_node_id": clean_worker_id,
                "lease_id": clean_lease_id,
                "worker_pull_v0": True,
                "requester_result_retained": True,
                "result_retention_window_seconds": result_retention["window_seconds"],
                "result_retention_expires_at": result_retention["expires_at"],
            },
        )
        return {
            "ok": True,
            "request": HubRequestStatus.from_record(
                completed_record,
                polling_url=f"{polling_base_path.rstrip('/')}/{record.request_id}",
            ).as_dict(),
        }

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
        existing = self.request_store.get(request_id)
        record = self.request_store.cancel(request_id)
        if existing is not None and existing.state not in {"completed", "failed", "cancelled", "expired"}:
            self._release_record_worker(existing, success=True)
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

    def submit_requester_feedback(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit requester/agent feedback against a completed request.

        The requester supplies a request id and opaque report token.  Worker
        identity is copied from the completed request by the Hub and is only
        returned through ring-control/private summaries.
        """

        clean_request_id = str(request_id or "").strip()
        if not clean_request_id:
            raise ValueError("request_id is required.")
        record = self.request_store.get(clean_request_id)
        if record is None:
            raise KeyError(f"Unknown hub request: {clean_request_id}")
        if record.state != "completed":
            raise ValueError("Feedback can only be submitted for completed requests.")
        receipt = self._record_receipt_for_feedback(record)
        worker_commitment = str(receipt.get("worker_commitment", "") or "")
        if not worker_commitment:
            raise ValueError("Completed request does not have a worker commitment for feedback.")
        expected_token = str(receipt.get("report_token", "") or "")
        supplied_token = str(payload.get("report_token", "") or "").strip()
        if expected_token and supplied_token != expected_token:
            raise PermissionError("Invalid report token for request feedback.")
        account_id = clean_node_id(str(payload.get("account_id") or payload.get("requester_account_id") or record.account_id or ""), default="")
        if not account_id:
            raise ValueError("account_id is required.")
        if record.account_id and account_id != record.account_id:
            raise PermissionError("Requester account does not own this request.")
        metadata = dict(record.request_payload.get("metadata", {})) if isinstance(record.request_payload, dict) and isinstance(record.request_payload.get("metadata"), dict) else {}
        feedback_channel_raw = str(payload.get("feedback_channel") or payload.get("reviewer_label") or "").strip()
        feedback_channel = clean_node_id(feedback_channel_raw, default="") if feedback_channel_raw else ""
        feedback_payload = {
            "request_id": record.request_id,
            "account_id": account_id,
            "requester_account_id": account_id,
            "requester_wallet_address": normalize_address(str(payload.get("requester_wallet_address", "") or "")),
            "worker_commitment": worker_commitment,
            "report_token_hash": token_digest(expected_token or supplied_token),
            "worker_node_id": record.selected_worker_node_id,
            "worker_instance_id": record.selected_worker_instance_id,
            "worker_wallet_address": normalize_address(str(receipt.get("worker_wallet_address", "") or "")),
            "score": payload.get("score", payload.get("rating", 1)),
            "rating": payload.get("score", payload.get("rating", 1)),
            "verdict": payload.get("verdict", ""),
            "feedback_tags": payload.get("feedback_tags", payload.get("tags", [])),
            "note": payload.get("note", payload.get("reason", "")),
            "reason": payload.get("reason", payload.get("note", "")),
            "source": payload.get("source", "requester"),
            "agent_run_id": str(payload.get("agent_run_id") or metadata.get("agent_run_id") or ""),
            "agent_step_id": str(payload.get("agent_step_id") or metadata.get("agent_step_id") or ""),
            "parent_request_id": str(payload.get("parent_request_id") or metadata.get("parent_request_id") or ""),
            "requester_connection_id": str(payload.get("requester_connection_id") or metadata.get("requester_connection_id") or ""),
            "agent_label": str(payload.get("agent_label") or metadata.get("agent_label") or metadata.get("purpose") or ""),
        }
        if feedback_channel:
            feedback_payload["feedback_channel"] = feedback_channel
            feedback_payload["feedback_key"] = f"{record.request_id}:{account_id}:{feedback_channel}"
        stored = self.feedback_store.submit(feedback_payload)
        try:
            self.request_store.update(
                record.request_id,
                event_type="request.feedback.submitted",
                event={
                    "feedback_id": str(stored.get("feedback_id", "")),
                    "feedback_key": str(stored.get("feedback_key", "")),
                    "account_id": account_id,
                    "worker_commitment": worker_commitment,
                    "verdict": str(stored.get("verdict", "")),
                    "score": int(stored.get("score", 0) or 0),
                    "source": str(stored.get("source", "")),
                    "worker_identity_private": True,
                    "money_movement": False,
                },
            )
        except Exception:
            pass
        public = _feedback_public_payload(stored)
        return {"ok": True, "feedback": public, "idempotent": bool(stored.get("idempotent", False))}

    def get_request_feedback(self, request_id: str, *, account_id: str = "") -> dict[str, Any]:
        clean_request_id = str(request_id or "").strip()
        if not clean_request_id:
            raise ValueError("request_id is required.")
        records = self.feedback_store.get_for_request(clean_request_id, account_id=account_id)
        public = [_feedback_public_payload(record) for record in records]
        return {"ok": True, "request_id": clean_request_id, "feedback": public, "feedback_count": len(public)}

    def worker_reliability_summary(
        self,
        *,
        worker_node_id: str = "",
        worker_commitment: str = "",
        include_private: bool = False,
        limit: int = 500,
    ) -> dict[str, Any]:
        clean_worker_id = clean_node_id(worker_node_id, default="") if worker_node_id else ""
        clean_commitment = str(worker_commitment or "").strip()
        records = self.feedback_store.list(limit=limit)
        if clean_worker_id:
            records = [record for record in records if str(record.get("worker_node_id", "") or "") == clean_worker_id]
        if clean_commitment:
            records = [record for record in records if str(record.get("worker_commitment", "") or "") == clean_commitment]
        completed_records = self.request_store.list(limit=limit, states={"completed"})
        completed_for_worker = []
        for record in completed_records:
            receipt = dict(record.receipt) if isinstance(record.receipt, dict) else {}
            if clean_worker_id and record.selected_worker_node_id != clean_worker_id:
                continue
            if clean_commitment and str(receipt.get("worker_commitment", "") or "") != clean_commitment:
                continue
            if clean_worker_id or clean_commitment:
                completed_for_worker.append(record)
        tag_counts: dict[str, int] = {}
        verdict_counts = {"accepted": 0, "rejected": 0, "needs_revision": 0}
        requester_keys: set[str] = set()
        negative_requester_keys: set[str] = set()
        source_counts: dict[str, int] = {}
        score_total = 0
        score_count = 0
        for record in records:
            score = max(1, min(5, int(record.get("score", record.get("rating", 0)) or 0)))
            if score > 0:
                score_total += score
                score_count += 1
            verdict = str(record.get("verdict", "") or "").strip().lower()
            if verdict in verdict_counts:
                verdict_counts[verdict] += 1
            source = str(record.get("source", "") or "requester")
            source_counts[source] = source_counts.get(source, 0) + 1
            identity = _feedback_identity(record)
            requester_key = identity["requester_key"]
            if requester_key:
                requester_keys.add(requester_key)
                if verdict == "rejected" or score <= 2:
                    negative_requester_keys.add(requester_key)
            for tag in record.get("feedback_tags", []) or []:
                clean_tag = _clean_feedback_tag(tag)
                if clean_tag:
                    tag_counts[clean_tag] = tag_counts.get(clean_tag, 0) + 1
        summary = {
            "ok": True,
            "summary_mode": "ring_control_feedback_read_surface_v0",
            "worker_identity_private_for_requesters": True,
            "feedback_money_movement_count": 0,
            "completed_request_count": len(completed_for_worker),
            "feedback_count": len(records),
            "accepted_count": verdict_counts["accepted"],
            "rejected_count": verdict_counts["rejected"],
            "needs_revision_count": verdict_counts["needs_revision"],
            "average_score": round(score_total / score_count, 4) if score_count else 0.0,
            "feedback_tag_counts": tag_counts,
            "fail_signal_observed_count": int(tag_counts.get("fail_signal", 0)),
            "agent_complaint_count": int(source_counts.get("agent", 0)),
            "noisy_requester_complaint_count": int(source_counts.get("noisy_requester", 0)),
            "unique_requester_count": len(requester_keys),
            "bounded_negative_feedback_count": len(negative_requester_keys),
        }
        if include_private:
            summary.update(
                {
                    "worker_node_id": clean_worker_id,
                    "worker_commitment": clean_commitment,
                    "private_worker_mapping_visible": True,
                    "feedback": [dict(record) for record in records],
                }
            )
        return summary

    def ring_control_feedback_summary(self, *, limit: int = 500) -> dict[str, Any]:
        records = self.feedback_store.list(limit=limit)
        worker_ids = sorted({str(record.get("worker_node_id", "") or "") for record in records if str(record.get("worker_node_id", "") or "")})
        summaries = [
            self.worker_reliability_summary(worker_node_id=worker_id, include_private=True, limit=limit)
            for worker_id in worker_ids
        ]
        return {
            "ok": True,
            "summary_mode": "ring_control_feedback_read_surface_v0",
            "worker_summary_count": len(summaries),
            "workers": summaries,
            "feedback_money_movement_count": 0,
        }

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
                preferred_worker_instance_id=str(
                    (
                        request.metadata.get("selected_offer", {})
                        if isinstance(request.metadata, dict) and isinstance(request.metadata.get("selected_offer"), dict)
                        else {}
                    ).get("worker_instance_id", "")
                ),
                lease_seconds=self.timeout_s,
            )
        return self.registry.select_worker(request.model)

    def _release_record_worker(self, record: HubRequestRecord, *, success: bool) -> None:
        node_id = str(record.selected_worker_node_id or "")
        if not node_id:
            return
        if hasattr(self.registry, "release_worker"):
            self.registry.release_worker(
                node_id,
                request_id=record.request_id,
                success=success,
                worker_instance_id=record.selected_worker_instance_id,
            )
            return
        self.registry.mark_worker(node_id, status="available" if success else "offline")

    def _record_attempt(
        self,
        record: HubRequestRecord,
        *,
        attempt: int,
        worker_node_id: str,
        worker_model: str,
        worker_instance_id: str = "",
        error: str = "",
    ) -> list[dict[str, Any]]:
        history = [dict(item) for item in record.attempt_history]
        history.append(
            {
                "attempt": attempt,
                "worker_node_id": worker_node_id,
                "worker_instance_id": worker_instance_id or worker_node_id,
                "model": worker_model,
                "error": error,
                "created_at": utc_now(),
            }
        )
        return history

    def _request_execution_mode(self, request: HubAIRequest) -> str:
        metadata = dict(request.metadata) if isinstance(request.metadata, dict) else {}
        return normalize_execution_mode(metadata.get("execution_mode"))

    def _request_pricing_mode(self, request: HubAIRequest) -> str:
        metadata = dict(request.metadata) if isinstance(request.metadata, dict) else {}
        return str(metadata.get("pricing_mode") or "").strip()

    def _market_pricing_requested(self, request: HubAIRequest) -> bool:
        metadata = dict(request.metadata) if isinstance(request.metadata, dict) else {}
        pricing_mode = str(metadata.get("pricing_mode") or "").strip()
        execution_mode = normalize_execution_mode(metadata.get("execution_mode"))
        return (
            pricing_mode == PHASE9_PRICING_MODE
            or (bool(str(metadata.get("execution_mode") or "").strip()) and execution_mode == PHASE9_EXECUTION_MODE)
            or metadata.get("worker_pull_v0") is True
            or bool(str(metadata.get("quote_id") or "").strip())
        )

    def _quote_market_paid_request(self, request: HubAIRequest) -> tuple[dict[str, Any], bool]:
        account_id = clean_node_id(request.account_id, default="") if request.account_id else ""
        if not account_id:
            raise ValueError("account_id is required for market-backed paid AI quotes.")
        model = str(request.model or "").strip()
        if not model:
            raise ValueError("model is required for market-backed paid AI quotes.")
        max_credits = max(0, int(request.max_credits or 0))
        if max_credits <= 0:
            raise ValueError("max_credits must be positive for market-backed paid AI quotes.")
        metadata = dict(request.metadata) if isinstance(request.metadata, dict) else {}
        idempotency_key = str(request.idempotency_key or metadata.get("idempotency_key") or "").strip()
        if idempotency_key:
            existing = self.quote_store.find_by_idempotency_key(account_id=account_id, idempotency_key=idempotency_key)
            if existing is not None:
                return existing, True
        execution_mode = normalize_execution_mode(metadata.get("execution_mode")) or PHASE9_EXECUTION_MODE
        requested_ring = _request_requested_ring(request)
        selected = self._select_market_worker_offer(
            model=model,
            max_credits=max_credits,
            execution_mode=execution_mode,
            requested_worker_node_id=request.requested_worker_node_id,
            requested_ring=requested_ring,
        )
        quoted_credits = max(0, int(selected.get("credits_per_request", 0) or 0))
        if quoted_credits <= 0:
            raise ValueError("Selected worker offer is not priced.")
        if quoted_credits > max_credits:
            raise ValueError(
                f"Selected worker offer price {quoted_credits} exceeds requester max_credits {max_credits}."
            )
        now = utc_now()
        seed_payload = {
            "account_id": account_id,
            "model": model,
            "max_credits": max_credits,
            "execution_mode": execution_mode,
            "requested_ring": requested_ring,
            "selected_offer_id": selected.get("offer_id", ""),
            "quoted_credits": quoted_credits,
        }
        quote = {
            "quote_id": phase9_quote_id(
                account_id=account_id,
                idempotency_key=idempotency_key,
                seed_payload=seed_payload,
            ),
            "idempotency_key": idempotency_key,
            "account_id": account_id,
            "model": model,
            "quoted_credits": quoted_credits,
            "estimated_credits": quoted_credits,
            "max_credits": max_credits,
            "unit": "compute_credit",
            "pricing_mode": PHASE9_PRICING_MODE,
            "execution_mode": execution_mode,
            "requested_ring": requested_ring,
            "selected_offer": dict(selected),
            "selected_offer_id": str(selected.get("offer_id", "")),
            "selected_worker_node_id": str(selected.get("worker_node_id", "")),
            "selected_worker_instance_id": str(selected.get("worker_instance_id", "") or selected.get("worker_node_id", "")),
            "selected_offer_price_source": str(selected.get("price_source", "worker_registration") or "worker_registration"),
            "created_at": now,
            "expires_at": (datetime.now(tz=timezone.utc) + timedelta(minutes=5)).isoformat(),
        }
        stored, idempotent = self.quote_store.create_or_get(quote)
        return stored, idempotent

    def _select_market_worker_offer(
        self,
        *,
        model: str,
        max_credits: int,
        execution_mode: str,
        requested_worker_node_id: str = "",
        requested_ring: int | None = None,
    ) -> dict[str, Any]:
        status = self.registry.status() if hasattr(self.registry, "status") else {}
        workers = status.get("workers", []) if isinstance(status, dict) else []
        requested = clean_node_id(requested_worker_node_id, default="") if requested_worker_node_id else ""
        compatible: list[dict[str, Any]] = []
        unpriced_match = False
        cheapest_over_budget_price: int | None = None
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            if requested and requested not in {
                str(worker.get("node_id", "")),
                str(worker.get("worker_instance_id") or worker.get("node_id", "")),
            }:
                continue
            state = str(worker.get("status", "available") or "available").lower()
            if state not in {"available", "configured"} or bool(worker.get("stale", False)):
                continue
            offer = market_worker_offer_from_payload(worker)
            if not offer:
                if self._worker_payload_supports_model(worker, model):
                    unpriced_match = True
                continue
            if normalize_execution_mode(offer.get("execution_mode")) != normalize_execution_mode(execution_mode):
                continue
            if model not in [str(item).strip() for item in offer.get("models", []) if str(item).strip()]:
                continue
            worker_ring = _worker_ring_from_payload(worker)
            if requested_ring is not None:
                if worker_ring is None or worker_ring > requested_ring:
                    continue
            offer_price = max(0, int(offer.get("credits_per_request", 0) or 0))
            if max_credits > 0 and offer_price > max_credits:
                if cheapest_over_budget_price is None or offer_price < cheapest_over_budget_price:
                    cheapest_over_budget_price = offer_price
                continue
            if worker_ring is not None and "assigned_ring" not in offer:
                offer = {**offer, "assigned_ring": worker_ring}
            compatible.append(offer)
        if not compatible:
            if cheapest_over_budget_price is not None:
                raise ValueError(
                    f"Selected worker offer price {cheapest_over_budget_price} exceeds requester max_credits {max_credits}."
                )
            if requested_ring is not None:
                raise ValueError(
                    "No compatible priced worker offer is available for this model, execution mode, "
                    f"requested_ring <= {requested_ring}, and requester max_credits."
                )
            if unpriced_match:
                raise ValueError("Compatible worker exists but does not advertise a priced AI seller offer.")
            raise ValueError("No compatible priced worker offer is available for this model and execution mode.")
        assignment_counts = {} if requested else self._market_worker_assignment_counts()
        selected = sorted(
            compatible,
            key=lambda offer: (
                max(0, int(offer.get("credits_per_request", 0) or 0)),
                assignment_counts.get(
                    str(offer.get("worker_instance_id", "") or offer.get("worker_node_id", "")),
                    0,
                ),
                max(0, int(offer.get("assigned_ring", 1_000_000) or 1_000_000)),
                str(offer.get("worker_node_id", "")),
                str(offer.get("worker_instance_id", "") or offer.get("worker_node_id", "")),
                str(offer.get("offer_id", "")),
            ),
        )[0]
        if max_credits > 0 and int(selected.get("credits_per_request", 0) or 0) > max_credits:
            raise ValueError(
                f"Selected worker offer price {selected.get('credits_per_request')} exceeds requester max_credits {max_credits}."
            )
        return dict(selected)

    def _market_worker_assignment_counts(self) -> dict[str, int]:
        """Count active market assignments by selected worker for quote-time load balancing."""

        active_states = {"submitted", "held", "queued", "leasing_worker", "dispatching", "running", "retrying", "leased"}
        try:
            records = self.request_store.list(limit=500, states=active_states)
        except Exception:
            return {}

        counts: dict[str, int] = {}
        for record in records:
            worker_identity = ""
            market_metadata = self._record_market_metadata(record)
            if market_metadata:
                selected_offer = self._record_selected_offer(record)
                worker_identity = str(
                    selected_offer.get("worker_instance_id")
                    or market_metadata.get("selected_worker_instance_id")
                    or selected_offer.get("worker_node_id")
                    or market_metadata.get("selected_worker_node_id")
                    or ""
                ).strip()
            if not worker_identity:
                worker_identity = str(
                    record.selected_worker_instance_id
                    or record.requested_worker_node_id
                    or record.selected_worker_node_id
                    or ""
                ).strip()
            if worker_identity:
                counts[worker_identity] = counts.get(worker_identity, 0) + 1
        return counts

    def _worker_payload_supports_model(self, worker: dict[str, Any], model: str) -> bool:
        desired = str(model or "").strip()
        if not desired:
            return True
        models = [str(item).strip() for item in worker.get("models", []) if str(item).strip()] if isinstance(worker.get("models"), list) else []
        worker_model = str(worker.get("model", "") or "").strip()
        if worker_model and worker_model not in models:
            models.append(worker_model)
        return not models or desired in models

    def _accepted_market_quote_for_request(self, request: HubAIRequest) -> dict[str, Any]:
        metadata = dict(request.metadata) if isinstance(request.metadata, dict) else {}
        quote_id = str(metadata.get("quote_id") or "").strip()
        if quote_id:
            quote = self.quote_store.get(quote_id)
            if quote is None:
                raise ValueError(f"Unknown market quote_id: {quote_id}")
            self._validate_quote_matches_request(quote, request)
            return quote
        quote, _idempotent = self._quote_market_paid_request(request)
        self._validate_quote_matches_request(quote, request)
        return quote

    def _validate_quote_matches_request(self, quote: dict[str, Any], request: HubAIRequest) -> None:
        account_id = clean_node_id(request.account_id, default="") if request.account_id else ""
        model = str(request.model or "").strip()
        quoted_credits = max(0, int(quote.get("quoted_credits", quote.get("estimated_credits", 0)) or 0))
        max_credits = max(0, int(request.max_credits or quote.get("max_credits", 0) or 0))
        if str(quote.get("account_id", "")) != account_id:
            raise ValueError("quote account_id does not match request account_id.")
        if str(quote.get("model", "")) != model:
            raise ValueError("quote model does not match request model.")
        if quoted_credits <= 0:
            raise ValueError("quote is not priced.")
        if max_credits < quoted_credits:
            raise ValueError(f"requester max_credits {max_credits} is below quoted_credits {quoted_credits}.")
        if normalize_execution_mode(quote.get("execution_mode")) != (
            normalize_execution_mode(request.metadata.get("execution_mode") if isinstance(request.metadata, dict) else "")
            or PHASE9_EXECUTION_MODE
        ):
            raise ValueError("quote execution_mode does not match request execution_mode.")
        quote_requested_ring = _optional_int(quote.get("requested_ring"))
        request_requested_ring = _request_requested_ring(request)
        if (
            quote_requested_ring is not None
            and request_requested_ring is not None
            and quote_requested_ring != request_requested_ring
        ):
            raise ValueError("quote requested_ring does not match request requested_ring.")

    def _record_market_acceptance(
        self,
        record: HubRequestRecord,
        request: HubAIRequest,
        quote: dict[str, Any],
        *,
        held_credits: int,
    ) -> HubRequestRecord:
        payload = dict(record.request_payload)
        metadata = dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else {}
        selected_offer = dict(quote.get("selected_offer", {})) if isinstance(quote.get("selected_offer"), dict) else {}
        metadata.update(
            {
                "phase9_market_backed_paid_ai_request": True,
                "pricing_mode": PHASE9_PRICING_MODE,
                "execution_mode": normalize_execution_mode(quote.get("execution_mode")),
                "quote": dict(quote),
                "selected_offer": selected_offer,
                "requested_ring": _optional_int(quote.get("requested_ring")),
                "quoted_credits": max(0, int(quote.get("quoted_credits", 0) or 0)),
                "held_credits": max(0, int(held_credits or 0)),
                "accepted_at": utc_now(),
            }
        )
        payload["metadata"] = metadata
        return self.request_store.update(
            record.request_id,
            request_payload=payload,
            requested_worker_node_id=str(quote.get("selected_worker_node_id") or selected_offer.get("worker_node_id") or ""),
            selected_worker_instance_id=str(
                quote.get("selected_worker_instance_id")
                or selected_offer.get("worker_instance_id")
                or selected_offer.get("worker_node_id")
                or ""
            ),
            event_type="phase9.market_quote.accepted",
            event={
                "quote_id": str(quote.get("quote_id", "")),
                "offer_id": str(selected_offer.get("offer_id", "")),
                "worker_node_id": str(selected_offer.get("worker_node_id", "")),
                "worker_instance_id": str(
                    selected_offer.get("worker_instance_id", "") or selected_offer.get("worker_node_id", "")
                ),
                "requested_ring": _optional_int(quote.get("requested_ring")),
                "assigned_ring": _optional_int(selected_offer.get("assigned_ring")),
                "quoted_credits": max(0, int(quote.get("quoted_credits", 0) or 0)),
                "held_credits": max(0, int(held_credits or 0)),
            },
        )

    def _record_market_metadata(self, record: HubRequestRecord) -> dict[str, Any]:
        metadata = (
            dict(record.request_payload.get("metadata", {}))
            if isinstance(record.request_payload, dict) and isinstance(record.request_payload.get("metadata"), dict)
            else {}
        )
        return metadata if metadata.get("phase9_market_backed_paid_ai_request") is True else {}

    def _record_quoted_credits(self, record: HubRequestRecord) -> int:
        metadata = self._record_market_metadata(record)
        if not metadata:
            return 0
        quote = dict(metadata.get("quote", {})) if isinstance(metadata.get("quote"), dict) else {}
        return max(0, int(quote.get("quoted_credits", metadata.get("quoted_credits", 0)) or 0))

    def _record_selected_offer(self, record: HubRequestRecord) -> dict[str, Any]:
        metadata = self._record_market_metadata(record)
        if not metadata:
            return {}
        return dict(metadata.get("selected_offer", {})) if isinstance(metadata.get("selected_offer"), dict) else {}

    def _request_requires_paid_account(self, request: HubAIRequest) -> bool:
        return bool(str(request.account_id or "").strip() or int(request.max_credits or 0) > 0)

    def _ensure_paid_hold(self, record: HubRequestRecord, request: HubAIRequest) -> dict[str, Any]:
        if self.credit_ledger is None:
            raise ValueError("Paid request accounting is not available on this hub.")
        if not request.account_id:
            raise ValueError("account_id is required for paid requests.")
        if self._market_pricing_requested(request):
            quote = self._accepted_market_quote_for_request(request)
            requester_max_credits = max(0, int(request.max_credits or quote.get("max_credits", 0) or 0))
            held_credits = max(0, int(quote.get("quoted_credits", quote.get("estimated_credits", 0)) or 0))
            if held_credits <= 0:
                raise ValueError("Market-backed paid request quote must have positive quoted_credits.")
            if requester_max_credits < held_credits:
                raise ValueError(f"requester max_credits {requester_max_credits} is below quoted_credits {held_credits}.")
            record = self._record_market_acceptance(record, request, quote, held_credits=held_credits)
        else:
            quote_response = self.quote_request(request)
            quote = dict(quote_response["quote"])
            requester_max_credits = max(0, int(quote.get("max_credits", request.max_credits) or request.max_credits or 0))
            held_credits = requester_max_credits
        hold_result = self.credit_ledger.create_hold(
            account_id=request.account_id,
            request_id=record.request_id,
            credits=held_credits,
            expires_at=record.deadline_at,
            memo=f"paid request hold {record.request_id}",
            metadata={"quote": quote, "idempotency_key": request.idempotency_key},
        )
        hold = dict(hold_result.get("hold", {}))
        self.request_store.update(
            record.request_id,
            account_id=hold.get("account_id", request.account_id),
            max_credits=requester_max_credits,
            hold_id=str(hold.get("hold_id", "")),
            event_type="payment.hold.created",
            event={
                "account_id": hold.get("account_id", request.account_id),
                "hold_id": hold.get("hold_id", ""),
                "held_credits": hold.get("credits", held_credits),
                "quoted_credits": held_credits if self._market_pricing_requested(request) else 0,
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

    def _feedback_secret(self) -> str:
        return str(os.environ.get("MAIN_COMPUTER_HUB_REPORT_SECRET") or "main-computer-dev-report-secret-v0")

    def _record_receipt_for_feedback(self, record: HubRequestRecord) -> dict[str, Any]:
        receipt = dict(record.receipt) if isinstance(record.receipt, dict) else {}
        if not receipt and isinstance(record.response, dict):
            metadata = dict(record.response.get("metadata", {})) if isinstance(record.response.get("metadata"), dict) else {}
            hub_metadata = dict(metadata.get("hub", {})) if isinstance(metadata.get("hub"), dict) else {}
            receipt = dict(hub_metadata.get("payment", {})) if isinstance(hub_metadata.get("payment"), dict) else {}
        if receipt.get("worker_commitment") and not receipt.get("report_token"):
            receipt["report_token"] = make_report_token(
                hub_secret=self._feedback_secret(),
                account_id=str(receipt.get("account_id") or record.account_id or ""),
                request_id=record.request_id,
                worker_commitment=str(receipt.get("worker_commitment", "") or ""),
            )
        return receipt

    def _finalize_paid_request(
        self,
        *,
        record: HubRequestRecord,
        worker_node_id: str,
        worker_credits: int,
    ) -> dict[str, Any]:
        if self.credit_ledger is None or not record.hold_id:
            return {}
        market_metadata = self._record_market_metadata(record)
        selected_offer = self._record_selected_offer(record)
        quote = dict(market_metadata.get("quote", {})) if isinstance(market_metadata.get("quote"), dict) else {}
        request_payload = dict(record.request_payload or {}) if isinstance(record.request_payload, dict) else {}
        request_metadata = dict(request_payload.get("metadata") or {}) if isinstance(request_payload.get("metadata"), dict) else {}
        charge_metadata = {"worker_node_id": worker_node_id}
        scheduler_lab_run_id = str(request_metadata.get("scheduler_lab_run_id", "") or "").strip()
        if scheduler_lab_run_id:
            charge_metadata["scheduler_lab_run_id"] = scheduler_lab_run_id
        worker_payload = self.registry.get_worker(worker_node_id) if hasattr(self.registry, "get_worker") else None
        worker_wallet_address = _worker_wallet_address_from_payload(worker_payload) if worker_payload is not None else ""
        if worker_wallet_address:
            charge_metadata["worker_wallet_address"] = worker_wallet_address
        if market_metadata:
            charge_metadata.update(
                {
                    "phase9_market_backed_paid_ai_request": True,
                    "quote_id": str(quote.get("quote_id", "")),
                    "offer_id": str(selected_offer.get("offer_id", "")),
                    "pricing_mode": str(market_metadata.get("pricing_mode", PHASE9_PRICING_MODE) or PHASE9_PRICING_MODE),
                    "source": "selected_worker_offer",
                    "selected_offer_price_source": str(selected_offer.get("price_source", "worker_registration") or "worker_registration"),
                }
            )
        charge = self.credit_ledger.charge_hold(
            hold_id=record.hold_id,
            charged_credits=worker_credits,
            worker_node_id=worker_node_id,
            memo=f"paid request charge {record.request_id}",
            metadata=charge_metadata,
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
        if receipt.get("worker_commitment"):
            receipt["report_token"] = make_report_token(
                hub_secret=self._feedback_secret(),
                account_id=str(receipt.get("account_id") or record.account_id or ""),
                request_id=record.request_id,
                worker_commitment=str(receipt.get("worker_commitment", "") or ""),
            )
        if worker_wallet_address:
            receipt["worker_wallet_address"] = worker_wallet_address
        if market_metadata:
            receipt.update(
                {
                    "quote_id": str(quote.get("quote_id", "")),
                    "offer_id": str(selected_offer.get("offer_id", "")),
                    "pricing_mode": str(market_metadata.get("pricing_mode", PHASE9_PRICING_MODE) or PHASE9_PRICING_MODE),
                    "selected_offer_price_source": str(selected_offer.get("price_source", "worker_registration") or "worker_registration"),
                }
            )
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
        initial_state: str = "queued",
        initial_event_type: str = "request.accepted",
    ) -> HubRequestRecord:
        now = utc_now()
        normalized_payload = request.as_payload()
        record = HubRequestRecord(
            request_id=request_id,
            client_node_id=clean_node_id(request.client_node_id, default="main-computer-client"),
            model=str(request.model or ""),
            state=initial_state,
            created_at=now,
            updated_at=now,
            security_mode=security_mode,
            hub_blind=hub_blind,
            events=[{"type": initial_event_type, "state": initial_state, "created_at": now}],
            idempotency_key=str(request.idempotency_key or "").strip(),
            deadline_at=self._deadline_at(request),
            max_retries=self._max_retries(request),
            requested_worker_node_id=clean_node_id(request.requested_worker_node_id, default="") if request.requested_worker_node_id else "",
            account_id=clean_node_id(request.account_id, default="") if request.account_id else "",
            max_credits=max(0, int(request.max_credits or 0)),
            request_payload=normalized_payload,
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
            worker_instance_id = str(_item_value(worker, "worker_instance_id", worker_node_id))
            worker_endpoint = str(_item_value(worker, "endpoint", "")).rstrip("/")
            worker_model = str(_item_value(worker, "model", "") or "")
            worker_credits = max(1, int(_item_value(worker, "credits_per_request", 1) or 1))
            attempt_history = self._record_attempt(
                current,
                attempt=attempt,
                worker_node_id=worker_node_id,
                worker_instance_id=worker_instance_id,
                worker_model=worker_model,
            )
            self.request_store.update(
                record.request_id,
                state="dispatching",
                selected_worker_node_id=worker_node_id,
                selected_worker_instance_id=worker_instance_id,
                attempt_history=attempt_history,
                event_type="worker.selected",
                event={
                    "attempt": attempt,
                    "worker_node_id": worker_node_id,
                    "worker_instance_id": worker_instance_id,
                    "worker_model": worker_model,
                },
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
                    event={"attempt": attempt, "worker_node_id": worker_node_id, "worker_instance_id": worker_instance_id},
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
                        self.registry.release_worker(
                            worker_node_id,
                            request_id=record.request_id,
                            success=False,
                            worker_instance_id=worker_instance_id,
                        )
                    else:
                        self.registry.mark_worker(worker_node_id, status="offline")
                finally:
                    refreshed = self.request_store.get(record.request_id) or current
                    failed_history = self._record_attempt(
                        refreshed,
                        attempt=attempt,
                        worker_node_id=worker_node_id,
                        worker_instance_id=worker_instance_id,
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
                        event={
                            "attempt": attempt,
                            "worker_node_id": worker_node_id,
                            "worker_instance_id": worker_instance_id,
                            "error": str(exc),
                        },
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
                HubRequestRecord.from_dict(
                    {
                        **record.as_dict(),
                        "selected_worker_node_id": worker_node_id,
                        "selected_worker_instance_id": worker_instance_id,
                    }
                ),
                success=True,
            )
            metadata = dict(response.metadata)
            metadata["hub"] = {
                "request_id": record.request_id,
                "worker_node_id": worker_node_id,
                "worker_instance_id": worker_instance_id,
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
                selected_worker_instance_id=worker_instance_id,
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

    def _worker_can_run_record(self, worker: Any, record: HubRequestRecord) -> bool:
        desired = str(record.model or "").strip()
        payload = _public_payload(worker)
        market_metadata = self._record_market_metadata(record)
        if market_metadata:
            selected_offer = self._record_selected_offer(record)
            selected_worker = str(selected_offer.get("worker_node_id") or market_metadata.get("selected_worker_node_id") or "").strip()
            if selected_worker and str(payload.get("node_id", "")) != selected_worker:
                return False
            if not market_worker_offer_from_payload(payload):
                return False
            requested_ring = _optional_int(market_metadata.get("requested_ring"))
            worker_ring = _worker_ring_from_payload(payload)
            if requested_ring is not None and (worker_ring is None or worker_ring > requested_ring):
                return False
        if not desired:
            return True
        models = [str(item).strip() for item in payload.get("models", []) if str(item).strip()] if isinstance(payload.get("models"), list) else []
        model = str(payload.get("model", "") or "").strip()
        if model and model not in models:
            models.append(model)
        return not models or desired in models

    def _fail_worker_pull_lease_timeout(self, record: HubRequestRecord, *, reason: str = "worker_lost_timeout") -> HubRequestRecord:
        """Mark an expired worker-pull lease as failed without charging the requester.

        Worker-pull v0 treats temporary disconnects as recoverable until the lease
        deadline.  Once that deadline passes, the request is no longer requeued
        implicitly: the requester must make a new request, the paid hold is
        released, and any late worker result is rejected without creating a
        worker earning.
        """

        worker_node_id = str(record.selected_worker_node_id or "").strip()
        lease_id = str(record.lease_id or "").strip()
        error = "Worker disconnected or failed to return a result before the lease deadline."
        self._release_paid_hold_for_request(record.request_id, reason=reason, error=error)
        failed = self.request_store.update(
            record.request_id,
            state="failed",
            error=error,
            terminal_reason=reason,
            lease_id="",
            lease_expires_at="",
            event_type="request.failed",
            event={
                "reason": reason,
                "lease_id": lease_id,
                "worker_node_id": worker_node_id,
                "worker_pull_v0": True,
                "charge_created": False,
                "requester_charged": False,
            },
        )
        self._release_record_worker(record, success=False)
        return failed

    def _expire_worker_pull_leases(self) -> int:
        now = datetime.now(tz=timezone.utc)
        changed = 0
        for record in self.request_store.list(limit=500, states={"leased"}):
            deadline = parse_utc(record.lease_expires_at)
            if deadline is None or deadline >= now:
                continue
            self._fail_worker_pull_lease_timeout(record)
            changed += 1
        return changed

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
            self._store_secure_session(
                session_id,
                {
                    "kind": "worker",
                    "session_id": session_id,
                    "request_id": record.request_id,
                    "worker": _public_payload(worker),
                    "credits": worker_credits,
                    "created_at": utc_now(),
                },
            )
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
        self._store_secure_session(
            upstream_session_id,
            {
                "kind": "upstream",
                "session_id": upstream_session_id,
                "request_id": upstream_request_id,
                "local_request_id": record.request_id,
                "upstream": _public_payload(upstream),
                "upstream_session_id": upstream_session_id,
                "upstream_request_id": upstream_request_id,
                "credits": upstream_credits,
                "created_at": utc_now(),
            },
        )
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
        session = self._load_secure_session(clean)
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

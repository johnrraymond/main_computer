from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from main_computer.models import ChatAttachment, ChatMessage, ChatResponse
from main_computer.hub_credit_models import (
    DEFAULT_WORKER_PAYOUT_PRECISION_PLACES,
    normalize_worker_payout_precision_places,
    truncate_worker_payout_for_precision,
)


REQUEST_STATES = {
    "submitted",
    "held",
    "queued",
    "leasing_worker",
    "dispatching",
    "running",
    "retrying",
    "leased",
    "completed",
    "failed",
    "cancelled",
    "expired",
}


def clean_node_id(value: str, *, default: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    return text or default


def _public_payout_privacy_context(*, precision_places: Any = None, rounding_bucket_credits: int | None = None) -> dict[str, Any]:
    precision = normalize_worker_payout_precision_places(precision_places)
    if rounding_bucket_credits is None:
        _published, _dust, precision, bucket_size = truncate_worker_payout_for_precision(0, precision_places=precision)
    else:
        bucket_size = max(1, int(rounding_bucket_credits or 1))
    return {
        "exact_amounts_visible": False,
        "exact_amounts_hidden": True,
        "precision_places": precision,
        "rounding_bucket_credits": bucket_size,
        "rounding": "floor_to_precision",
        "request_links_redacted": True,
    }


def _privacy_precision_from_payload(payload: dict[str, Any], fallback: Any = None) -> int:
    privacy = payload.get("privacy") if isinstance(payload.get("privacy"), dict) else {}
    return normalize_worker_payout_precision_places(privacy.get("precision_places", fallback))


def _public_payout_amount(value: Any, *, precision_places: Any = None) -> tuple[int, int, int, int]:
    try:
        units = int(value or 0)
    except (TypeError, ValueError):
        units = 0
    return truncate_worker_payout_for_precision(max(0, units), precision_places=precision_places)


def sanitize_public_payout_queue(payload: Any, *, precision_places: Any = None) -> Any:
    """Return a payout queue payload safe for normal/non-audit responses.

    Older completed request records may contain exact legacy ``payout_queue``
    metadata.  Sanitizing at serialization time prevents same-scope/idempotent
    request replays from leaking high-precision worker payout amounts even if
    the stored request predates the privacy hardening patch.
    """

    if not isinstance(payload, dict):
        return payload
    precision = _privacy_precision_from_payload(payload, fallback=precision_places)
    _published_zero, _dust_zero, precision, bucket_size = truncate_worker_payout_for_precision(0, precision_places=precision)
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {
            "balances_exact",
            "bridge_retained_credits_if_claimed_by_node",
            "credits_exact",
            "bridge_retained_credits_if_claimed",
        }:
            continue
        if key == "balances" and isinstance(value, dict):
            balances: dict[str, int] = {}
            for node_id, amount in value.items():
                published, _dust, _precision, _bucket = _public_payout_amount(amount, precision_places=precision)
                balances[str(node_id)] = published
            clean[key] = balances
            continue
        if key == "recent" and isinstance(value, list):
            recent: list[dict[str, Any]] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                payout = {
                    "payout_id": str(item.get("payout_id", "")),
                    "kind": str(item.get("kind", "")),
                    "node_id": str(item.get("node_id", "")),
                    "created_at": str(item.get("created_at", "")),
                }
                published, _dust, _precision, _bucket = _public_payout_amount(item.get("credits", 0), precision_places=precision)
                payout["credits"] = published
                payout["credits_published"] = published
                payout["memo"] = "privacy-redacted"
                payout["request_id"] = ""
                payout["privacy"] = _public_payout_privacy_context(
                    precision_places=precision,
                    rounding_bucket_credits=bucket_size,
                )
                recent.append(payout)
            clean[key] = recent
            continue
        if key == "privacy":
            continue
        clean[key] = value
    clean["privacy"] = _public_payout_privacy_context(
        precision_places=precision,
        rounding_bucket_credits=bucket_size,
    )
    return clean


def sanitize_hub_response_payload(payload: Any, *, precision_places: Any = None) -> Any:
    """Sanitize worker payout metadata embedded inside a hub response payload."""

    if not isinstance(payload, dict):
        return payload
    response = dict(payload)
    metadata = response.get("metadata")
    if not isinstance(metadata, dict):
        return response
    clean_metadata = dict(metadata)
    hub = clean_metadata.get("hub")
    if isinstance(hub, dict):
        clean_hub = dict(hub)
        if "payout_queue" in clean_hub:
            clean_hub["payout_queue"] = sanitize_public_payout_queue(
                clean_hub.get("payout_queue"),
                precision_places=precision_places,
            )
        clean_metadata["hub"] = clean_hub
    response["metadata"] = clean_metadata
    return response


def chat_message_to_payload(message: ChatMessage | dict[str, Any]) -> dict[str, Any]:
    if isinstance(message, ChatMessage):
        return {
            "role": message.role,
            "content": message.content,
            "attachments": [asdict(attachment) for attachment in message.attachments],
        }
    if not isinstance(message, dict):
        return {"role": "user", "content": str(message), "attachments": []}
    role = str(message.get("role", "user"))
    if role not in {"system", "user", "assistant"}:
        role = "user"
    attachments: list[dict[str, Any]] = []
    for item in message.get("attachments", []) or []:
        if isinstance(item, dict):
            attachments.append(dict(item))
    return {
        "role": role,
        "content": str(message.get("content", "")),
        "attachments": attachments,
    }


def chat_message_from_payload(payload: dict[str, Any]) -> ChatMessage:
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


def chat_response_from_payload(payload: dict[str, Any], *, default_provider: str, default_model: str) -> ChatResponse:
    return ChatResponse(
        content=str(payload.get("content", "")),
        provider=str(payload.get("provider") or default_provider),
        model=str(payload.get("model") or default_model),
        metadata=dict(payload.get("metadata", {})) if isinstance(payload.get("metadata", {}), dict) else {},
    )


@dataclass(frozen=True)
class HubAIRequest:
    """Normalized request accepted by the hub backend/API."""

    messages: list[dict[str, Any]]
    model: str = ""
    client_node_id: str = "main-computer-client"
    hop_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    deadline_seconds: float = 0.0
    requested_worker_node_id: str = ""
    account_id: str = ""
    max_credits: int = 0

    @classmethod
    def from_messages(
        cls,
        messages: list[ChatMessage] | tuple[ChatMessage, ...],
        *,
        model: str = "",
        client_node_id: str = "main-computer-client",
        hop_count: int = 0,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str = "",
        deadline_seconds: float = 0.0,
        requested_worker_node_id: str = "",
        account_id: str = "",
        max_credits: int = 0,
    ) -> "HubAIRequest":
        return cls(
            messages=[chat_message_to_payload(message) for message in messages],
            model=str(model or ""),
            client_node_id=clean_node_id(client_node_id, default="main-computer-client"),
            hop_count=max(0, int(hop_count or 0)),
            metadata=dict(metadata or {}),
            idempotency_key=str(idempotency_key or "").strip(),
            deadline_seconds=max(0.0, float(deadline_seconds or 0.0)),
            requested_worker_node_id=clean_node_id(requested_worker_node_id, default="") if requested_worker_node_id else "",
            account_id=clean_node_id(account_id, default="") if account_id else "",
            max_credits=max(0, int(max_credits or 0)),
        )

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        default_model: str = "",
        default_client_node_id: str = "main-computer-client",
    ) -> "HubAIRequest":
        messages_payload = payload.get("messages")
        if not messages_payload and payload.get("prompt") is not None:
            messages_payload = [{"role": "user", "content": str(payload.get("prompt", ""))}]
        if not isinstance(messages_payload, list):
            raise ValueError("messages must be a list, or prompt must be supplied.")
        messages = [chat_message_to_payload(item) for item in messages_payload if isinstance(item, dict)]
        if not messages:
            raise ValueError("At least one message is required.")
        metadata = dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else {}
        for key in ("execution_mode", "pricing_mode", "quote_id", "requested_ring", "max_price_credits"):
            if payload.get(key) is not None and key not in metadata:
                metadata[key] = payload.get(key)
        idempotency_key = str(payload.get("idempotency_key") or metadata.get("idempotency_key") or "").strip()
        requested_worker_node_id = str(
            payload.get("worker_node_id") or payload.get("requested_worker_node_id") or metadata.get("worker_node_id") or ""
        ).strip()
        account_id = str(payload.get("account_id") or metadata.get("account_id") or "").strip()
        try:
            max_credits = int(
                payload.get("max_credits")
                or payload.get("max_price_credits")
                or metadata.get("max_credits")
                or metadata.get("max_price_credits")
                or 0
            )
        except (TypeError, ValueError):
            max_credits = 0
        try:
            deadline_seconds = float(payload.get("deadline_seconds") or payload.get("timeout_seconds") or metadata.get("deadline_seconds") or 0.0)
        except (TypeError, ValueError):
            deadline_seconds = 0.0
        return cls(
            messages=messages,
            model=str(payload.get("model", default_model) or ""),
            client_node_id=clean_node_id(str(payload.get("client_node_id", default_client_node_id)), default="main-computer-client"),
            hop_count=max(0, int(payload.get("hop_count", 0) or 0)),
            metadata=metadata,
            idempotency_key=idempotency_key,
            deadline_seconds=max(0.0, deadline_seconds),
            requested_worker_node_id=clean_node_id(requested_worker_node_id, default="") if requested_worker_node_id else "",
            account_id=clean_node_id(account_id, default="") if account_id else "",
            max_credits=max(0, max_credits),
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "messages": [chat_message_to_payload(message) for message in self.messages],
            "model": self.model,
            "client_node_id": clean_node_id(self.client_node_id, default="main-computer-client"),
            "hop_count": max(0, int(self.hop_count or 0)),
            "metadata": dict(self.metadata),
            "idempotency_key": str(self.idempotency_key or "").strip(),
            "deadline_seconds": max(0.0, float(self.deadline_seconds or 0.0)),
            "requested_worker_node_id": clean_node_id(self.requested_worker_node_id, default="") if self.requested_worker_node_id else "",
            "account_id": clean_node_id(self.account_id, default="") if self.account_id else "",
            "max_credits": max(0, int(self.max_credits or 0)),
        }


@dataclass(frozen=True)
class HubAIResponse:
    content: str
    provider: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_chat_response(cls, response: ChatResponse) -> "HubAIResponse":
        return cls(
            content=response.content,
            provider=response.provider,
            model=response.model,
            metadata=dict(response.metadata),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "provider": self.provider,
            "model": self.model,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class HubWorkerSummary:
    node_id: str
    worker_instance_id: str = ""
    model: str = ""
    models: list[str] = field(default_factory=list)
    status: str = "available"
    credits_per_request: int = 1
    settlement_precision_places: int = DEFAULT_WORKER_PAYOUT_PRECISION_PLACES
    registered_at: str = ""
    last_seen_at: str = ""
    endpoint: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    offer: dict[str, Any] = field(default_factory=dict)
    queue_depth: int = 0
    active_requests: int = 0
    max_concurrency: int = 1
    lease_expires_at: str = ""
    stale: bool = False

    @classmethod
    def from_worker_payload(cls, payload: dict[str, Any], *, include_endpoint: bool = False) -> "HubWorkerSummary":
        model = str(payload.get("model", "") or "")
        raw_models = payload.get("models")
        if isinstance(raw_models, list):
            models = [str(item).strip() for item in raw_models if str(item).strip()]
        else:
            models = []
        if model and model not in models:
            models.insert(0, model)
        return cls(
            node_id=str(payload.get("node_id", "")),
            worker_instance_id=str(payload.get("worker_instance_id") or payload.get("node_id", "")),
            model=model,
            models=models,
            status=str(payload.get("status", "available") or "available"),
            credits_per_request=max(1, int(payload.get("credits_per_request", 1) or 1)),
            settlement_precision_places=normalize_worker_payout_precision_places(
                payload.get("settlement_precision_places")
                if payload.get("settlement_precision_places") is not None
                else (
                    payload.get("capabilities", {}).get("settlement_precision_places")
                    if isinstance(payload.get("capabilities"), dict)
                    else None
                )
            ),
            registered_at=str(payload.get("registered_at", "")),
            last_seen_at=str(payload.get("last_seen_at", "") or payload.get("last_heartbeat_at", "")),
            endpoint=str(payload.get("endpoint", "")) if include_endpoint else "",
            capabilities=dict(payload.get("capabilities", {})) if isinstance(payload.get("capabilities"), dict) else {},
            offer=dict(payload.get("offer", {})) if isinstance(payload.get("offer"), dict) else {},
            queue_depth=max(0, int(payload.get("queue_depth", 0) or 0)),
            active_requests=max(0, int(payload.get("active_requests", 0) or 0)),
            max_concurrency=1,
            lease_expires_at=str(payload.get("lease_expires_at", "") or ""),
            stale=bool(payload.get("stale", False)) or str(payload.get("status", "")).lower() == "stale",
        )

    def as_dict(self) -> dict[str, Any]:
        data = {
            "node_id": self.node_id,
            "worker_instance_id": self.worker_instance_id or self.node_id,
            "model": self.model,
            "models": list(self.models),
            "status": self.status,
            "credits_per_request": self.credits_per_request,
            "settlement_precision_places": self.settlement_precision_places,
            "registered_at": self.registered_at,
            "last_seen_at": self.last_seen_at,
            "capabilities": dict(self.capabilities),
            "queue_depth": self.queue_depth,
            "active_requests": self.active_requests,
            "max_concurrency": self.max_concurrency,
            "lease_expires_at": self.lease_expires_at,
            "stale": self.stale,
        }
        if self.offer:
            data["offer"] = dict(self.offer)
        if self.endpoint:
            data["endpoint"] = self.endpoint
        return data


@dataclass
class HubRequestRecord:
    request_id: str
    client_node_id: str
    model: str
    state: str = "queued"
    created_at: str = ""
    updated_at: str = ""
    selected_worker_node_id: str = ""
    selected_worker_instance_id: str = ""
    selected_upstream_hub_node_id: str = ""
    session_id: str = ""
    error: str = ""
    response: dict[str, Any] | None = None
    response_summary: str = ""
    credits_queued: int = 0
    security_mode: str = "legacy-plaintext"
    hub_blind: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    idempotency_key: str = ""
    deadline_at: str = ""
    retry_count: int = 0
    max_retries: int = 1
    attempt_history: list[dict[str, Any]] = field(default_factory=list)
    terminal_reason: str = ""
    requested_worker_node_id: str = ""
    account_id: str = ""
    max_credits: int = 0
    hold_id: str = ""
    charge_id: str = ""
    charged_credits: int = 0
    released_credits: int = 0
    worker_earning_id: str = ""
    receipt: dict[str, Any] = field(default_factory=dict)
    request_payload: dict[str, Any] = field(default_factory=dict)
    lease_id: str = ""
    lease_expires_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "client_node_id": self.client_node_id,
            "model": self.model,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "selected_worker_node_id": self.selected_worker_node_id,
            "selected_worker_instance_id": self.selected_worker_instance_id,
            "selected_upstream_hub_node_id": self.selected_upstream_hub_node_id,
            "session_id": self.session_id,
            "error": self.error,
            "response": dict(self.response) if isinstance(self.response, dict) else None,
            "response_summary": self.response_summary,
            "credits_queued": self.credits_queued,
            "security_mode": self.security_mode,
            "hub_blind": self.hub_blind,
            "events": [dict(event) for event in self.events],
            "idempotency_key": self.idempotency_key,
            "deadline_at": self.deadline_at,
            "retry_count": max(0, int(self.retry_count or 0)),
            "max_retries": max(0, int(self.max_retries or 0)),
            "attempt_history": [dict(item) for item in self.attempt_history],
            "terminal_reason": self.terminal_reason,
            "requested_worker_node_id": self.requested_worker_node_id,
            "account_id": self.account_id,
            "max_credits": max(0, int(self.max_credits or 0)),
            "hold_id": self.hold_id,
            "charge_id": self.charge_id,
            "charged_credits": max(0, int(self.charged_credits or 0)),
            "released_credits": max(0, int(self.released_credits or 0)),
            "worker_earning_id": self.worker_earning_id,
            "receipt": dict(self.receipt),
            "request_payload": dict(self.request_payload),
            "lease_id": self.lease_id,
            "lease_expires_at": self.lease_expires_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HubRequestRecord":
        state = str(payload.get("state", "queued") or "queued")
        if state not in REQUEST_STATES:
            state = "queued"
        return cls(
            request_id=str(payload.get("request_id", "")),
            client_node_id=str(payload.get("client_node_id", "main-computer-client") or "main-computer-client"),
            model=str(payload.get("model", "") or ""),
            state=state,
            created_at=str(payload.get("created_at", "") or ""),
            updated_at=str(payload.get("updated_at", "") or ""),
            selected_worker_node_id=str(payload.get("selected_worker_node_id", "") or ""),
            selected_worker_instance_id=str(payload.get("selected_worker_instance_id", "") or ""),
            selected_upstream_hub_node_id=str(payload.get("selected_upstream_hub_node_id", "") or ""),
            session_id=str(payload.get("session_id", "") or ""),
            error=str(payload.get("error", "") or ""),
            response=dict(payload.get("response")) if isinstance(payload.get("response"), dict) else None,
            response_summary=str(payload.get("response_summary", "") or ""),
            credits_queued=max(0, int(payload.get("credits_queued", 0) or 0)),
            security_mode=str(payload.get("security_mode", "legacy-plaintext") or "legacy-plaintext"),
            hub_blind=bool(payload.get("hub_blind", False)),
            events=[dict(event) for event in payload.get("events", []) if isinstance(event, dict)],
            idempotency_key=str(payload.get("idempotency_key", "") or ""),
            deadline_at=str(payload.get("deadline_at", "") or ""),
            retry_count=max(0, int(payload.get("retry_count", 0) or 0)),
            max_retries=max(0, int(payload.get("max_retries", 1) or 1)),
            attempt_history=[dict(item) for item in payload.get("attempt_history", []) if isinstance(item, dict)],
            terminal_reason=str(payload.get("terminal_reason", "") or ""),
            requested_worker_node_id=str(payload.get("requested_worker_node_id", "") or ""),
            account_id=str(payload.get("account_id", "") or ""),
            max_credits=max(0, int(payload.get("max_credits", 0) or 0)),
            hold_id=str(payload.get("hold_id", "") or ""),
            charge_id=str(payload.get("charge_id", "") or ""),
            charged_credits=max(0, int(payload.get("charged_credits", 0) or 0)),
            released_credits=max(0, int(payload.get("released_credits", 0) or 0)),
            worker_earning_id=str(payload.get("worker_earning_id", "") or ""),
            receipt=dict(payload.get("receipt", {})) if isinstance(payload.get("receipt"), dict) else {},
            request_payload=dict(payload.get("request_payload", {})) if isinstance(payload.get("request_payload"), dict) else {},
            lease_id=str(payload.get("lease_id", "") or ""),
            lease_expires_at=str(payload.get("lease_expires_at", "") or ""),
        )


@dataclass(frozen=True)
class HubRequestStatus:
    request_id: str
    client_node_id: str
    model: str
    state: str
    created_at: str
    updated_at: str
    selected_worker_node_id: str = ""
    selected_worker_instance_id: str = ""
    selected_upstream_hub_node_id: str = ""
    session_id: str = ""
    error: str = ""
    response: dict[str, Any] | None = None
    response_summary: str = ""
    credits_queued: int = 0
    security_mode: str = "legacy-plaintext"
    hub_blind: bool = False
    polling_url: str = ""
    idempotency_key: str = ""
    deadline_at: str = ""
    retry_count: int = 0
    max_retries: int = 1
    attempt_history: list[dict[str, Any]] = field(default_factory=list)
    terminal_reason: str = ""
    requested_worker_node_id: str = ""
    account_id: str = ""
    max_credits: int = 0
    hold_id: str = ""
    charge_id: str = ""
    charged_credits: int = 0
    released_credits: int = 0
    worker_earning_id: str = ""
    receipt: dict[str, Any] = field(default_factory=dict)
    request_payload: dict[str, Any] = field(default_factory=dict)
    lease_id: str = ""
    lease_expires_at: str = ""

    @classmethod
    def from_record(cls, record: HubRequestRecord, *, polling_url: str = "") -> "HubRequestStatus":
        return cls(
            request_id=record.request_id,
            client_node_id=record.client_node_id,
            model=record.model,
            state=record.state,
            created_at=record.created_at,
            updated_at=record.updated_at,
            selected_worker_node_id=record.selected_worker_node_id,
            selected_worker_instance_id=record.selected_worker_instance_id,
            selected_upstream_hub_node_id=record.selected_upstream_hub_node_id,
            session_id=record.session_id,
            error=record.error,
            response=dict(record.response) if isinstance(record.response, dict) else None,
            response_summary=record.response_summary,
            credits_queued=record.credits_queued,
            security_mode=record.security_mode,
            hub_blind=record.hub_blind,
            polling_url=polling_url,
            idempotency_key=record.idempotency_key,
            deadline_at=record.deadline_at,
            retry_count=record.retry_count,
            max_retries=record.max_retries,
            attempt_history=[dict(item) for item in record.attempt_history],
            terminal_reason=record.terminal_reason,
            requested_worker_node_id=record.requested_worker_node_id,
            account_id=record.account_id,
            max_credits=record.max_credits,
            hold_id=record.hold_id,
            charge_id=record.charge_id,
            charged_credits=record.charged_credits,
            released_credits=record.released_credits,
            worker_earning_id=record.worker_earning_id,
            receipt=dict(record.receipt),
            request_payload=dict(record.request_payload),
            lease_id=record.lease_id,
            lease_expires_at=record.lease_expires_at,
        )

    def as_dict(self) -> dict[str, Any]:
        data = {
            "request_id": self.request_id,
            "client_node_id": self.client_node_id,
            "model": self.model,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "selected_worker_node_id": self.selected_worker_node_id,
            "selected_worker_instance_id": self.selected_worker_instance_id,
            "selected_upstream_hub_node_id": self.selected_upstream_hub_node_id,
            "session_id": self.session_id,
            "error": self.error,
            "response_summary": self.response_summary,
            "credits_queued": self.credits_queued,
            "security_mode": self.security_mode,
            "hub_blind": self.hub_blind,
            "idempotency_key": self.idempotency_key,
            "deadline_at": self.deadline_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "attempt_history": [dict(item) for item in self.attempt_history],
            "terminal_reason": self.terminal_reason,
            "requested_worker_node_id": self.requested_worker_node_id,
            "account_id": self.account_id,
            "max_credits": max(0, int(self.max_credits or 0)),
            "hold_id": self.hold_id,
            "charge_id": self.charge_id,
            "charged_credits": max(0, int(self.charged_credits or 0)),
            "released_credits": max(0, int(self.released_credits or 0)),
            "worker_earning_id": self.worker_earning_id,
            "lease_id": self.lease_id,
            "lease_expires_at": self.lease_expires_at,
        }
        request_metadata = (
            dict(self.request_payload.get("metadata", {}))
            if isinstance(self.request_payload, dict) and isinstance(self.request_payload.get("metadata"), dict)
            else {}
        )
        quote = dict(request_metadata.get("quote", {})) if isinstance(request_metadata.get("quote"), dict) else {}
        selected_offer = (
            dict(request_metadata.get("selected_offer", {}))
            if isinstance(request_metadata.get("selected_offer"), dict)
            else {}
        )
        if quote:
            data["quote_id"] = str(quote.get("quote_id", ""))
            data["pricing"] = {
                "quoted_credits": max(0, int(quote.get("quoted_credits", quote.get("estimated_credits", 0)) or 0)),
                "held_credits": max(0, int(request_metadata.get("held_credits", quote.get("quoted_credits", 0)) or 0)),
                "charged_credits": max(0, int(self.charged_credits or 0)),
                "unit": str(quote.get("unit", "compute_credit") or "compute_credit"),
                "pricing_mode": str(quote.get("pricing_mode", request_metadata.get("pricing_mode", "")) or ""),
                "execution_mode": str(quote.get("execution_mode", request_metadata.get("execution_mode", "")) or ""),
            }
        if selected_offer:
            data["selected_offer"] = {
                "offer_id": str(selected_offer.get("offer_id", "")),
                "worker_node_id": str(selected_offer.get("worker_node_id", "")),
                "worker_instance_id": str(selected_offer.get("worker_instance_id", "") or selected_offer.get("worker_node_id", "")),
                "credits_per_request": max(0, int(selected_offer.get("credits_per_request", 0) or 0)),
                "unit": str(selected_offer.get("unit", "compute_credit") or "compute_credit"),
                "execution_mode": str(selected_offer.get("execution_mode", "") or ""),
                "price_source": str(selected_offer.get("price_source", "") or ""),
            }
        if self.receipt:
            data["receipt"] = dict(self.receipt)
        if self.response is not None:
            data["response"] = sanitize_hub_response_payload(self.response)
        if self.polling_url:
            data["polling_url"] = self.polling_url
        return data


@dataclass(frozen=True)
class HubDispatchError:
    request_id: str
    state: str
    message: str
    selected_worker_node_id: str = ""
    selected_upstream_hub_node_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "state": self.state,
            "error": self.message,
            "selected_worker_node_id": self.selected_worker_node_id,
            "selected_upstream_hub_node_id": self.selected_upstream_hub_node_id,
        }
